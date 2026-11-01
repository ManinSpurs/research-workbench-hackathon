"""Chip files: a domain pack as a portable ZIP on disk, data only and never code.

A chip is a real file a researcher can copy, open, list and hand to someone else:

    manifest.json     required, {"format": 1, "pack": {<DomainPack fields>},
                                "presentation": {"tagline", "color", "glyph"}}
    chip.png          optional override (PNG, at most 2 MB)
    record.json       optional research record: questions, answers, results, code, harness
    conversation.txt  optional plain-text log: the secretary exchanges, the decision each
                      question reached and the evidence behind it, so the reason a topic
                      went a certain way travels with the chip

A chip is also the researcher's notebook for one topic. ``record.json`` holds what was
asked inside this chip, the answers recorded for it, the JSON body of each analysis
result, the analysis code kept as TEXT, and the growing per-chip checklist. Every list
is capped and the whole record is capped before it is parsed, so a hostile chip cannot
exhaust memory. A chip without ``record.json`` is a plain pack definition and still
reads exactly as it did before, so every chip already on disk stays valid.

The cartridge BODY (notch, dot texture, border) belongs to the app and is drawn by the
UI for every chip, so a chip carries no label image: its identity is the border colour
and the name, both already in the manifest (``presentation.color``, ``presentation.glyph``
and ``pack.title``). ``chip.png`` stays an optional override for a chip that really wants
its own art, and ``render_chip_label`` can draw just the name in the shared dot font when
someone wants a raster preview.

Nothing in a chip is ever executed or imported: the archive is opened for reading,
every member name is checked, every size is capped before extraction, and the only
bytes accepted as art must start with the PNG magic number. Code stored in a record is
data and stays data: this module never runs, imports, compiles or evaluates it, it only
carries the text in and out byte for byte, exactly like a note. Installed chips are
rewritten in a canonical form under ``<data dir>/chips/installed/`` so what lands on
disk is exactly what this module validated. The two built-in packs stay available
whether or not their exported sample files exist, because the built-in list is built
from ``app.packs`` and not from the directory.

A researcher can also make their own chip: ``build_chip(payload)`` turns the fields a form
submits into exactly the archive that install would accept, and ``python -m app.chips build
--from my_chip.json --out my_chip.chip`` does the same from a terminal. Run ``python -m
app.chips export`` to write the built-in packs into ``chips/builtin/``.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import re
import struct
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .packs import PACKS, DomainPack

FORMAT = 1
MANIFEST_NAME = "manifest.json"
ART_NAME = "chip.png"
RECORD_NAME = "record.json"
CONVERSATION_NAME = "conversation.txt"
ALLOWED_MEMBERS = frozenset({MANIFEST_NAME, ART_NAME, RECORD_NAME, CONVERSATION_NAME})
MAX_MEMBERS = 5
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ART_BYTES = 100 * 1024 * 1024
MAX_RECORD_BYTES = 100 * 1024 * 1024
MAX_CONVERSATION_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_RECORD_ENTRIES = 5000
MAX_RECORD_TEXT_CHARS = 100_000
MAX_CODE_SOURCE_CHARS = 200_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_SUFFIX = b"IEND\xaeB`\x82"

PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ART_URL = "/chips/{pack_id}/art"

BASE = Path(__file__).resolve().parent.parent
BUILTIN_DIR = BASE / "chips" / "builtin"

# Label art/metadata for the packs that ship with the app. The exported built-in
# chip files are written from exactly these values, so the two agree.
BUILTIN_PRESENTATION = {
    "social_science": {"tagline": "Constructs, coverage, and the estimand",
                       "color": "#2f5d8a", "glyph": "Σ"},
    "engineering_measurement": {"tagline": "Quantity, unit, condition, uncertainty",
                                "color": "#8a5a2f", "glyph": "Ω"},
}


VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
MAX_TITLE_CHARS = 120
MAX_TAGLINE_CHARS = 200
MAX_GLYPH_CHARS = 2

BUILD_FIELDS = frozenset({"pack_id", "version", "title", "instructions", "required_context", "checks",
                          "allowed_tools", "implementation", "tagline", "color", "glyph",
                          "presentation", "art_png"})


class Invalid(Exception):
    """A chip or request refused with HTTP 400 and one readable sentence."""


DEFAULT_COLOR = "#4a5568"


class Presentation(BaseModel):
    """The only per-chip difference in the shared cartridge body: colour and glyph.

    Optional as a whole (a chip without presentation still installs) and strict about
    its own fields. ``color`` is the chip's border colour, stored lowercase.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    tagline: str = Field(default="", max_length=MAX_TAGLINE_CHARS)
    color: str = DEFAULT_COLOR
    glyph: str = Field(default="", max_length=MAX_GLYPH_CHARS)

    @field_validator("color")
    @classmethod
    def _color(cls, value):
        if not isinstance(value, str) or not COLOR_RE.match(value):
            raise ValueError('color must be a "#rrggbb" hex color, for example "#2f5d8a"')
        return value.lower()


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal[1]
    pack: DomainPack
    presentation: Presentation = Presentation()


# ------------------------------------------------------------------ the research record
# The record is what makes a chip a notebook for one topic rather than only a pack
# definition. Every list is optional and empty by default, every model refuses unknown
# fields, and every text field is length-capped, so a hostile chip that got past the
# archive caps still cannot build an unbounded object.
class RecordQuestion(BaseModel):
    """One question asked inside this chip, with the pack version it was asked under."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    question_id: str = Field(default="", max_length=128)
    text: str = Field(default="", max_length=MAX_RECORD_TEXT_CHARS)
    working_text: str = Field(default="", max_length=MAX_RECORD_TEXT_CHARS)
    asked_at: str = Field(default="", max_length=64)
    pack_version: str = Field(default="", max_length=64)
    manifest_sha256: str = Field(default="", max_length=128)


class RecordAnswer(BaseModel):
    """The researcher's recorded answer for a question: a kind and its value."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    question_id: str = Field(default="", max_length=128)
    kind: str = Field(default="", max_length=64)
    value: str = Field(default="", max_length=MAX_RECORD_TEXT_CHARS)
    answered_at: str = Field(default="", max_length=64)


class RecordResult(BaseModel):
    """An analysis result, kept as the JSON string the app already produces."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    question_id: str = Field(default="", max_length=128)
    body_json: str = Field(default="", max_length=MAX_RECORD_TEXT_CHARS)
    created_at: str = Field(default="", max_length=64)


class RecordCode(BaseModel):
    """Analysis code kept as TEXT. It is data: never run, imported, compiled or evaluated."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(default="", max_length=256)
    language: str = Field(default="", max_length=64)
    source: str = Field(default="", max_length=MAX_CODE_SOURCE_CHARS)
    created_at: str = Field(default="", max_length=64)


class RecordHarness(BaseModel):
    """One growing per-chip rule or checklist entry the researcher accumulates."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(default="", max_length=256)
    value: str = Field(default="", max_length=MAX_RECORD_TEXT_CHARS)
    added_at: str = Field(default="", max_length=64)


class Record(BaseModel):
    """The whole research record carried by one chip. Absent members read as empty."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    questions: list[RecordQuestion] = Field(default_factory=list)
    answers: list[RecordAnswer] = Field(default_factory=list)
    results: list[RecordResult] = Field(default_factory=list)
    code: list[RecordCode] = Field(default_factory=list)
    harness: list[RecordHarness] = Field(default_factory=list)


# ------------------------------------------------------------------ archive reading
def _member_name(name) -> str:
    """Refuses zip-slip and unknown members before anything is read."""
    if not isinstance(name, str) or not name:
        raise Invalid("A chip member has an empty name")
    if "\\" in name:
        raise Invalid(f"A chip member name may not contain a backslash: {name!r}")
    if name.startswith("/") or PurePosixPath(name).is_absolute():
        raise Invalid(f"A chip member name may not be an absolute path: {name!r}")
    if name.endswith("/"):
        raise Invalid("A chip may not contain directories")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise Invalid(f"A chip member name may not traverse directories: {name!r}")
    if name not in ALLOWED_MEMBERS:
        raise Invalid(f"Unknown chip member {name!r}; a chip holds only manifest.json, chip.png,"
                      " record.json and conversation.txt")
    return name


def _read_member(archive, name: str, cap: int) -> bytes:
    try:
        with archive.open(name) as handle:
            raw = handle.read(cap + 1)
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error, RuntimeError, NotImplementedError):
        raise Invalid(f"{name} could not be read out of the chip archive") from None
    if len(raw) > cap:
        raise Invalid(f"{name} is larger than the {size_label(cap)} limit for that member")
    return raw


def _summarize(exc: ValidationError, fallback: str = "manifest") -> str:
    parts = []
    for error in exc.errors()[:3]:
        where = ".".join(str(piece) for piece in error.get("loc", ())) or fallback
        parts.append(f"{where}: {error.get('msg', 'invalid value')}")
    return "; ".join(parts)


def _known_problems(payload: dict):
    """Readable refusals for the mistakes a person actually makes."""
    declared = payload.get("format")
    if isinstance(declared, bool) or declared != FORMAT:
        return f"manifest.json declares format {declared!r}; this build reads chip format {FORMAT}"
    if "pack" not in payload:
        return "manifest.json is missing the pack object"
    if not isinstance(payload["pack"], dict):
        return "manifest.json pack must be a JSON object holding the DomainPack fields"
    return None


def _parse_manifest(raw: bytes) -> Manifest:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Invalid("manifest.json is not UTF-8 JSON") from None
    except RecursionError:
        # 200 KB of "[[[[" fits under the member cap and is not a decode error: the
        # parser runs out of nesting first. Refused with a sentence, never a traceback.
        raise Invalid("manifest.json is nested too deeply to be read as a chip manifest") from None
    if not isinstance(payload, dict):
        raise Invalid("manifest.json must be a JSON object")
    problem = _known_problems(payload)
    if problem:
        raise Invalid(problem)
    try:
        return Manifest.model_validate(payload)
    except ValidationError as exc:
        raise Invalid("manifest.json does not match the chip manifest: " + _summarize(exc)) from None


def _member_cap(name: str) -> int:
    if name == MANIFEST_NAME:
        return MAX_MANIFEST_BYTES
    if name == RECORD_NAME:
        return MAX_RECORD_BYTES
    if name == CONVERSATION_NAME:
        return MAX_CONVERSATION_BYTES
    return MAX_ART_BYTES


def _read_parts(data: bytes):
    """Validates the archive, the manifest and the art.

    Returns ``(Manifest, png bytes or None, record.json bytes or None,
    conversation.txt bytes or None)``. Everything that makes a file a chip at all is
    strict here, and the record and the conversation come back as raw member bytes so a
    caller can decide what to do with a member it cannot parse: a stored chip whose
    record is damaged is still a chip and must stay visible.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise Invalid("This chip is empty")
    if len(data) > MAX_SOURCE_BYTES:
        raise Invalid("That file is far too large to be a chip")
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except (zipfile.BadZipFile, OSError, ValueError):
        raise Invalid("This file is not a chip: it is not a readable ZIP archive") from None
    with archive:
        infos = archive.infolist()
        if not infos:
            raise Invalid("This chip is empty: it has no members")
        if len(infos) > MAX_MEMBERS:
            raise Invalid(f"A chip holds at most {MAX_MEMBERS} members; this one has {len(infos)}")
        names, total = [], 0
        for info in infos:
            name = _member_name(info.filename)
            if name in names:
                raise Invalid(f"The chip repeats the member {name!r}")
            names.append(name)
            if info.flag_bits & 0x1:
                raise Invalid("An encrypted chip member is refused; chips are never encrypted")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise Invalid(f"{name} must use stored or deflate compression")
            cap = _member_cap(name)
            if info.file_size > cap:
                raise Invalid(f"{name} is larger than the {size_label(cap)} limit for that member")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise Invalid(
                    f"The chip expands to more than the {size_label(MAX_TOTAL_BYTES)} total limit")
        if MANIFEST_NAME not in names:
            raise Invalid("A chip must contain manifest.json")
        manifest = _parse_manifest(_read_member(archive, MANIFEST_NAME, MAX_MANIFEST_BYTES))
        art = _read_member(archive, ART_NAME, MAX_ART_BYTES) if ART_NAME in names else None
        raw_record = _read_member(archive, RECORD_NAME, MAX_RECORD_BYTES) if RECORD_NAME in names else None
        raw_conversation = (_read_member(archive, CONVERSATION_NAME, MAX_CONVERSATION_BYTES)
                            if CONVERSATION_NAME in names else None)
    if art is not None and not _looks_like_png(art):
        raise Invalid("chip.png is not a PNG image")
    return manifest, art, raw_record, raw_conversation


def _read_archive(data: bytes):
    """Validates an archive and returns ``(Manifest, png bytes or None, Record, text)``.

    Never extracts and never runs a member. Every member is validated where it is read,
    so the manifest, the art, the record and the conversation all fail in this one place:
    a chip ``read_chip`` accepts is a chip whose record ``read_chip_record`` can return
    without a later, separate failure, and install passes on exactly what it was given.
    ``text`` is the conversation as plain text, ``""`` when the chip carries none.
    """
    manifest, art, raw_record, raw_conversation = _read_parts(data)
    record = _parse_record(raw_record) if raw_record is not None else Record()
    conversation = (_parse_conversation(raw_conversation)
                    if raw_conversation is not None else "")
    return manifest, art, record, conversation


def read_chip(data: bytes):
    """Validates an archive and returns ``(Manifest, png bytes or None)``. Never extracts.

    ``record.json`` and ``conversation.txt`` are validated here as well, beside the
    manifest and the art, so a malformed member is refused at install rather than
    surfacing the first time the researcher opens the chip. ``read_chip_record`` and
    install read the parsed values from that same pass instead of parsing twice.
    """
    manifest, art, _record, _conversation = _read_archive(data)
    return manifest, art


def _looks_like_png(raw: bytes) -> bool:
    return len(raw) > len(PNG_MAGIC) + 12 and raw.startswith(PNG_MAGIC) and raw.endswith(PNG_SUFFIX)


# ------------------------------------------------------------------ conversation reading
def _parse_conversation(raw: bytes) -> str:
    """The conversation as text, refused when it is not UTF-8.

    ``conversation.txt`` is a plain text note the researcher can open in any editor, so it
    is read as UTF-8 and stored verbatim: no parsing, no evaluation, no line rewriting.
    A member that is not UTF-8 is refused here rather than surfacing as a decode error the
    first time someone opens it.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Invalid("conversation.txt is not UTF-8 text") from None


def _cap_conversation(text: str, pack_id: str) -> bytes:
    """The conversation member bytes, refused when the reader would refuse them.

    The cap is applied to the encoded bytes, which is what the reader caps, so a
    conversation that fits here always reads back out of the file it was written to.
    """
    raw = text.encode("utf-8")
    if len(raw) > MAX_CONVERSATION_BYTES:
        raise Invalid(f"The conversation of {pack_id} is {len(raw)} bytes, larger than the"
                      f" {size_label(MAX_CONVERSATION_BYTES)} conversation limit;"
                      " it would be written but never read back")
    return raw


def read_chip_conversation(data: bytes) -> str:
    """The conversation a chip carries, or ``""`` when it carries none."""
    return _read_archive(data)[3]


# ------------------------------------------------------------------ record reading
RECORD_LISTS = ("questions", "answers", "results", "code", "harness")


def _record_list_problem(payload: dict) -> str | None:
    """One readable sentence for the cap a hostile record breaks first."""
    for key in RECORD_LISTS:
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            return f"record.json {key} must be a list"
        if len(value) > MAX_RECORD_ENTRIES:
            return (f"record.json {key} holds {len(value)} entries, more than the"
                    f" {MAX_RECORD_ENTRIES} entries allowed in one list")
    return None


def _parse_record(raw: bytes) -> Record:
    """Strict, capped and never evaluated: the text fields are stored, not run."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Invalid("record.json is not UTF-8 JSON") from None
    except RecursionError:
        # 400 KB of "[[[[" fits under the member cap and is not a decode error: the
        # parser runs out of nesting first. Refused with a sentence, never a traceback.
        raise Invalid("record.json is nested too deeply to be read as a chip record") from None
    if not isinstance(payload, dict):
        raise Invalid("record.json must be a JSON object")
    problem = _record_list_problem(payload)
    if problem:
        raise Invalid(problem)
    try:
        return Record.model_validate(payload)
    except ValidationError as exc:
        raise Invalid("record.json does not match the chip record: "
                      + _summarize(exc, "record")) from None


def read_chip_record(data: bytes) -> Record:
    """The record a chip carries, or an empty ``Record`` when it carries none.

    The archive is validated by ``read_chip`` first, so a member name, size,
    encryption or record problem is refused with the same sentence install uses. A chip
    without ``record.json`` reads as an empty record, which keeps every existing chip
    valid. The record comes out of that one validated pass, so this can never return a
    different record than the one the archive was accepted with.
    """
    return _read_archive(data)[2]


def record_bytes(record: Record) -> bytes:
    """The canonical record.json bytes: sorted keys, so equal records are equal bytes."""
    return json.dumps(record.model_dump(mode="json"), sort_keys=True, indent=2,
                      ensure_ascii=False).encode("utf-8")


def _cap_record_bytes(raw: bytes, what: str) -> bytes:
    """Refuses canonical record bytes the reader would refuse, before anything is written.

    The incoming member is capped on the bytes that arrived, but the canonical form this
    module writes is 1.15-3.4x larger, so a record that read in can write out unreadable.
    ``what`` names the record in the sentence the researcher sees.
    """
    if len(raw) > MAX_RECORD_BYTES:
        raise Invalid(f"{what} is {len(raw)} bytes in the chip's canonical form, larger than the"
                      f" {size_label(MAX_RECORD_BYTES)} record limit;"
                      " it would be written but never read back")
    return raw


def _record_has_content(record: Record) -> bool:
    """Whether the record says anything at all: one non-default entry anywhere.

    A record of one all-default entry (``{"questions": [{}]}``) parses, validates and
    holds nothing a researcher would miss, so it is treated as no record: a chip without
    one stays byte-identical instead of growing a member that carries only empty fields.
    """
    payload = record.model_dump(mode="json")
    return any(any(entry.values())
               for key in RECORD_LISTS for entry in (payload.get(key) or ()))


def _checked_record(record) -> Record:
    """Accepts a ``Record`` or its JSON object form and returns a validated record.

    The entry cap is checked on the dumped payload for both shapes: pydantic accepts a
    5001-entry list in a ``Record`` instance exactly as it does in a dict, so a cap that
    only guarded the dict branch would let ``write_record`` write a chip that
    ``read_record`` then refuses.
    """
    if isinstance(record, Record):
        checked = record
    elif isinstance(record, dict):
        try:
            checked = Record.model_validate(record)
        except ValidationError as exc:
            raise Invalid("The record is not a valid chip record: " + _summarize(exc, "record")) from None
    else:
        raise Invalid("A record must be a JSON object holding the chip record fields")
    problem = _record_list_problem(checked.model_dump(mode="json"))
    if problem:
        raise Invalid(problem)
    _cap_record_bytes(record_bytes(checked), "The record")
    return checked


# ------------------------------------------------------------------ archive writing
def _manifest_bytes(manifest: Manifest) -> bytes:
    return json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2,
                      ensure_ascii=False).encode("utf-8")


def _encode(manifest: Manifest, art: bytes | None, record: Record | None = None,
            conversation: str | None = None) -> bytes:
    """A deterministic archive: fixed timestamps and permissions, so the same chip is the
    same bytes on any machine and two exports of one pack cannot drift apart.

    ``record`` is written as ``record.json`` only when it is given and carries something,
    and ``conversation`` as ``conversation.txt`` only when it is given and holds text, so a
    chip built from a pack definition alone has exactly the members it had before. The
    member order is fixed (manifest, art, record, conversation), so the bytes stay
    reproducible.
    """
    members = [(MANIFEST_NAME, _manifest_bytes(manifest)), (ART_NAME, art)]
    if record is not None and _record_has_content(record):
        # The member is capped on the bytes that go in, not on the bytes that arrived:
        # a record the reader would refuse must never reach the file.
        members.append((RECORD_NAME, _cap_record_bytes(
            record_bytes(record), f"The record of {manifest.pack.pack_id}")))
    if conversation is not None and conversation.strip():
        members.append((CONVERSATION_NAME,
                        _cap_conversation(conversation, manifest.pack.pack_id)))
    total = 0
    for name, payload in members:
        if payload is None:
            continue
        cap = _member_cap(name)
        if len(payload) > cap:
            raise Invalid(f"{name} is larger than the {size_label(cap)} limit for that member")
        total += len(payload)
    if total > MAX_TOTAL_BYTES:
        raise Invalid(
            f"The chip expands to more than the {size_label(MAX_TOTAL_BYTES)} total limit")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            if payload is None:
                continue
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            info.create_system = 3  # fixed, so the host OS does not change the bytes
            archive.writestr(info, payload)
    blob = buffer.getvalue()
    if len(blob) > MAX_SOURCE_BYTES:
        raise Invalid("That file is far too large to be a chip")
    return blob


def _assemble(pack: DomainPack, presentation: Presentation, art: bytes | None) -> bytes:
    return _encode(Manifest(format=FORMAT, pack=pack, presentation=presentation), art)


# ------------------------------------------------------------------ authoring
def _field_problem(payload: dict) -> str | None:
    """Named refusals for the fields a create form submits, checked before the model."""
    pack_id = payload.get("pack_id")
    if not isinstance(pack_id, str) or not PACK_ID_RE.match(pack_id):
        return ("pack_id must be lowercase letters, digits and underscores only, starting with a letter"
                " or digit, at most 64 characters")
    version = payload.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        return "version must be text such as 1.0.0, at most 32 characters"
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return "title is required and must be non-empty text"
    if len(title) > MAX_TITLE_CHARS:
        return f"title must be at most {MAX_TITLE_CHARS} characters"
    instructions = payload.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        return "instructions are required and must be non-empty text"
    for field in ("required_context", "checks", "allowed_tools"):
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
            return f"{field} must be a list of non-empty strings"
    color = payload.get("color")
    if color is not None and (not isinstance(color, str) or not COLOR_RE.match(color)):
        return 'color must be a "#rrggbb" hex color, for example "#2f5d8a"'
    glyph = payload.get("glyph")
    if glyph is not None and (not isinstance(glyph, str) or len(glyph) > 2):
        return "glyph must be at most 2 characters"
    if payload.get("art_png") is not None and not isinstance(payload.get("art_png"), (bytes, bytearray, str)):
        return "art_png must be PNG bytes, or base64 text carrying PNG bytes"
    return None


def _payload_parts(payload) -> tuple[DomainPack, Presentation, bytes | None]:
    """One validation path shared by build, preview and install-from-form."""
    if not isinstance(payload, dict):
        raise Invalid("The chip payload must be a JSON object")
    extra = sorted(set(payload) - BUILD_FIELDS)
    if extra:
        raise Invalid(f"The chip payload does not accept the field(s): {', '.join(extra)}")
    problem = _field_problem(payload)
    if problem:
        raise Invalid(problem)
    presentation_in = dict(payload.get("presentation") or {})
    if not isinstance(presentation_in, dict):
        raise Invalid("presentation must be a JSON object")
    for key in ("tagline", "color", "glyph"):
        if payload.get(key) is not None:
            presentation_in[key] = payload[key]
    try:
        presentation = Presentation.model_validate(presentation_in)
    except ValidationError as exc:
        raise Invalid("presentation is not valid: " + _summarize(exc)) from None
    pack_fields = {key: payload[key] for key in
                   ("pack_id", "version", "title", "instructions", "required_context", "checks",
                    "allowed_tools", "implementation") if payload.get(key) is not None}
    pack_fields.setdefault("required_context", [])
    pack_fields.setdefault("checks", [])
    pack_fields.setdefault("allowed_tools", [])
    try:
        pack = DomainPack.model_validate(pack_fields)
    except ValidationError as exc:
        raise Invalid("The pack fields are not a valid domain pack: " + _summarize(exc)) from None
    art = payload.get("art_png")
    if art is not None:
        if isinstance(art, str):
            try:
                art = base64.b64decode("".join(art.split()), validate=True)
            except (binascii.Error, ValueError):
                raise Invalid("art_png is not valid base64 text carrying PNG bytes") from None
        art = bytes(art)
        if not _looks_like_png(art):
            raise Invalid("art_png is not a PNG image")
        if len(art) > MAX_ART_BYTES:
            raise Invalid(f"art_png is larger than the {size_label(MAX_ART_BYTES)} limit")
    return pack, presentation, art


def build_chip(payload) -> bytes:
    """A finished ``.chip`` from the fields a create form submits.

    The bytes pass the same reader install uses, so a chip built here always installs.
    ``art_png`` is embedded only when supplied: the shared cartridge body is the UI's,
    so a chip normally carries no image.
    """
    pack, presentation, art = _payload_parts(payload)
    raw = _assemble(pack, presentation, art)
    installed, _ = read_chip(raw)
    if installed.pack != pack or installed.presentation != presentation:
        raise Invalid("The built chip did not read back identically; refusing to hand it out")
    return raw


def build_result(payload) -> dict:
    """What ``POST /chips/build`` answers: the file a person keeps, never written to disk.

    ``manifest_sha256`` covers the whole manifest.json, so two chips that differ only in
    their presentation colour have different values; ``identity`` is the pack's own
    ``DomainPack.identity()``, the same shape ``GET /chips/{pack_id}`` reports.
    """
    raw = build_chip(payload)
    manifest, _ = read_chip(raw)
    pack = manifest.pack
    return {"pack_id": pack.pack_id,
            "version": pack.version,
            "filename": f"{pack.pack_id}-{pack.version}.chip",
            "base64": base64.b64encode(raw).decode("ascii"),
            "manifest_sha256": hashlib.sha256(_manifest_bytes(manifest)).hexdigest(),
            "identity": pack.identity()}


# ------------------------------------------------------------------ label drawing
# A 5x7 dot font. Every letter is seven rows of five cells, so a name draws in the
# same matrix the app shell uses. Characters outside the table render as a filled
# block, so a name never silently loses a character.
FONT = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
}
FONT_ROWS, FONT_COLS = 7, 5
FILLED_BLOCK = ("#####", "#####", "#####", "#####", "#####", "#####", "#####")


def label_text(payload) -> str:
    """The name this chip draws: its glyph when given, else the first two title characters."""
    if not isinstance(payload, dict):
        raise Invalid("The chip payload must be a JSON object")
    glyph = payload.get("glyph")
    if isinstance(glyph, str) and glyph.strip():
        return glyph.strip()[:2]
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return "??"
    return title.strip()[:2].upper()


def _bitmap(text: str) -> tuple[list[list[bool]], int, int]:
    rows = [bytearray() for _ in range(FONT_ROWS)]
    for index, char in enumerate(text):
        pattern = FONT.get(char.upper(), FILLED_BLOCK)
        for y in range(FONT_ROWS):
            if index:
                rows[y].append(0)  # one dot of spacing between characters
            rows[y] += bytes(1 if cell == "#" else 0 for cell in pattern[y])
    width = max((len(row) for row in rows), default=0)
    return [[bool(cell) for cell in row] for row in rows], width, FONT_ROWS


def render_chip_label(payload, size=(256, 176)) -> bytes:
    """A transparent PNG holding only the chip's drawn name, scaled into ``size``.

    No border, body, background colour or dot grid: the app draws the shared cartridge
    body itself, so this is a name block for previews, exports and the CLI. The same
    payload always renders byte-identical bytes.
    """
    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError):
        raise Invalid("size must be a (width, height) pair of integers") from None
    if not (1 <= width <= 1024 and 1 <= height <= 1024):
        raise Invalid("size must be between 1 and 1024 pixels on each side")
    text = label_text(payload)
    pattern, pattern_width, pattern_height = _bitmap(text)
    cells = max(pattern_width, 1)

    def rgb(color: int) -> tuple[int, int, int]:
        return tuple(color for _ in range(3))

    art_px, clear = rgb(0x35), rgb(0x00)
    dot = max(1, min(width // max(cells * 3, 1), height // max(pattern_height * 3, 1), 24))
    block_w, block_h = pattern_width * dot, pattern_height * dot
    left = max((width - block_w) // 2, 0)
    top = max((height - block_h) // 2, 0)
    pixels = [[clear] * width for _ in range(height)]
    for gy in range(pattern_height):
        for gx in range(pattern_width):
            if not pattern[gy][gx]:
                continue
            for dy in range(dot):
                y = top + gy * dot + dy
                if not 0 <= y < height:
                    continue
                row = pixels[y]
                for dx in range(dot):
                    x = left + gx * dot + dx
                    if 0 <= x < width:
                        row[x] = art_px
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")  # filter type 0 for this scanline
        for x in range(width):
            r, g, b = pixels[y][x]
            row += bytes((r, g, b, 0 if (r, g, b) == clear else 0xFF))
        rows.append(bytes(row))

    def chunk(tag: bytes, payload_b: bytes) -> bytes:
        return (struct.pack(">I", len(payload_b)) + tag + payload_b
                + struct.pack(">I", zlib.crc32(tag + payload_b) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (PNG_MAGIC + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def label_preview(payload) -> dict:
    png = render_chip_label(payload)
    width, height = struct.unpack(">II", png[16:24])
    return {"label_png_base64": base64.b64encode(png).decode("ascii"),
            "text": label_text(payload), "width": width, "height": height}


# ------------------------------------------------------------------ built-in samples
def builtin_chip(pack_id: str) -> bytes:
    """The exported sample file for a built-in pack, built from code (no disk read).

    No image member: a built-in chip differs from any other only by its colour and name.
    """
    from .packs import get_pack
    pack = get_pack(pack_id)
    presentation = Presentation.model_validate(BUILTIN_PRESENTATION.get(pack_id) or {})
    return _assemble(pack, presentation, None)


def export_builtins(dest: Path | None = None) -> list[Path]:
    """Writes every built-in pack as a real ``.chip`` file. Safe to rerun."""
    folder = Path(dest) if dest is not None else BUILTIN_DIR
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for pack_id in PACKS:
        path = folder / f"{pack_id}.chip"
        path.write_bytes(builtin_chip(pack_id))
        written.append(path)
    return written


def load_installed_packs(data_dir) -> dict:
    """Register every installed chip as a usable pack, at start-up.

    A chip on disk is a real pack: the researcher made it to work in it, so the app
    must know it before they ask their first question. Chips that cannot be read are
    skipped and never break start-up; a built-in id always keeps the built-in
    definition, because those two are samples that ship with the app.
    """
    from .packs import INSTALLED_PACKS, register_installed
    store = ChipStore(data_dir)
    INSTALLED_PACKS.clear()
    found = store._scan()
    for pack_id, (pack, _presentation, _path, _has_art, _record_problem) in found.items():
        if pack_id in PACKS:
            continue
        register_installed(pack)
    return dict(INSTALLED_PACKS)


# ------------------------------------------------------------------ records
def _inside(path: Path, roots) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            if resolved == root.resolve() or root.resolve() in resolved.parents:
                return True
        except OSError:  # an unresolvable root cannot authorize anything
            continue
    return False


def _presentation_row(presentation: Presentation) -> dict:
    return {"tagline": presentation.tagline, "color": presentation.color, "glyph": presentation.glyph}


def _builtin_art(pack_id: str) -> bytes | None:
    """Built-in label art comes from the exported sample file when it is present."""
    path = BUILTIN_DIR / f"{pack_id}.chip"
    try:
        manifest, art = read_chip(path.read_bytes())
    except (OSError, Invalid):
        return None
    if manifest.pack.pack_id != pack_id:
        return None
    return art


def _builtin_files_present(pack_id: str) -> bool:
    return (BUILTIN_DIR / f"{pack_id}.chip").is_file()


def builtin_records() -> list[dict]:
    """The built-in list is derived from app.packs, so it never depends on files on disk."""
    rows = []
    for pack_id, pack in PACKS.items():
        presentation = Presentation.model_validate(BUILTIN_PRESENTATION.get(pack_id) or {})
        rows.append({
            "pack_id": pack_id,
            "version": pack.version,
            "title": pack.title,
            "source": "builtin",
            **_presentation_row(presentation),
            "art_url": ART_URL.format(pack_id=pack_id) if _builtin_art(pack_id) else None,
        })
    return rows


class ChipStore:
    """Install location: ``<data dir>/chips/installed/`` and nothing else."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "chips"
        self.installed = self.root / "installed"

    # -- readable state -------------------------------------------------------
    def _source_roots(self):
        """Where a named ``path`` install may read a file from.

        A person hands over a chip from their home folder, the workbench data folder,
        this project, or a download/temp folder. Anything else (``/etc``, a system tree)
        is refused, so a request cannot turn into a read of the machine's own files.
        """
        return (self.data_dir, BASE, Path.home(), Path(tempfile.gettempdir()))

    def _scan(self) -> dict:
        """Every readable installed chip, including one whose stored record is damaged.

        The manifest and the art decide whether a file on disk is a chip; the record does
        not. A record this build cannot parse is reported as the fifth field (``None``
        when it reads) instead of hiding the chip: the chip is still the researcher's
        pack, still listed, and the bytes on disk stay protected from being overwritten.
        """
        found = {}
        if not self.installed.is_dir():
            return found
        for path in sorted(self.installed.glob("*.chip")):
            try:
                manifest, art, raw_record, _raw_conversation = _read_parts(path.read_bytes())
            except (OSError, Invalid):
                continue  # an unreadable file is not a chip; it never breaks the listing
            pack = manifest.pack
            if path.stem != pack.pack_id or pack.pack_id in PACKS:
                continue
            problem = None
            if raw_record is not None:
                try:
                    _parse_record(raw_record)
                except Invalid as exc:
                    problem = str(exc)
            found[pack.pack_id] = (pack, manifest.presentation, path, art is not None, problem)
        return found

    def _row(self, pack, presentation, path, has_art, source, record_problem=None) -> dict:
        row = {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "title": pack.title,
            "source": source,
            **_presentation_row(presentation),
            "art_url": ART_URL.format(pack_id=pack.pack_id) if has_art else None,
        }
        if record_problem is not None:
            # The chip is intact and listed; only its record.json cannot be read. Saying so
            # here is the difference between a damaged notebook and no chip at all.
            row["record_error"] = record_problem
        if path is not None:
            row["path"] = str(path)
        return row

    def list(self) -> dict:
        rows = {row["pack_id"]: row for row in builtin_records()}
        for pack_id, (pack, presentation, path, has_art, problem) in self._scan().items():
            rows.setdefault(pack_id, self._row(pack, presentation, path, has_art, "file", problem))
        return {"chips": list(rows.values())}

    def record(self, pack_id) -> dict:
        if pack_id in PACKS:
            pack = PACKS[pack_id]
            presentation = Presentation.model_validate(BUILTIN_PRESENTATION.get(pack_id) or {})
            path = BUILTIN_DIR / f"{pack_id}.chip" if _builtin_files_present(pack_id) else None
            return {"format": FORMAT,
                    **self._row(pack, presentation, path, _builtin_art(pack_id) is not None, "builtin"),
                    "pack": pack.model_dump(), "identity": pack.identity()}
        if not isinstance(pack_id, str) or pack_id not in self._scan():
            raise KeyError(pack_id)
        pack, presentation, path, has_art, problem = self._scan()[pack_id]
        return {"format": FORMAT,
                **self._row(pack, presentation, path, has_art, "file", problem),
                "pack": pack.model_dump(), "identity": pack.identity()}

    def art(self, pack_id) -> bytes:
        if pack_id in PACKS:
            art = _builtin_art(pack_id)
        else:
            if not isinstance(pack_id, str) or pack_id not in self._scan():
                raise KeyError(pack_id)
            # Art comes from the manifest and the image alone, so a damaged record never
            # takes a chip's picture away.
            art = _read_parts(self._scan()[pack_id][2].read_bytes())[1]
        if art is None:
            raise KeyError(f"{pack_id} has no art")
        return art

    # -- writes ---------------------------------------------------------------
    def _target(self, pack_id: str) -> Path:
        if not isinstance(pack_id, str) or not pack_id:
            raise Invalid("pack_id is required")
        target = (self.installed / f"{pack_id}.chip").resolve()
        if not _inside(target, (self.installed,)) or target.name != f"{pack_id}.chip":
            raise Invalid("Refused: that pack_id would write outside chips/installed")
        return target

    def install_bytes(self, raw: bytes) -> dict:
        """Installs a chip, keeping the research record it carries.

        The incoming ``record.json`` is parsed, validated and written back into the
        installed archive: installing is not a place where a person's record may vanish.
        A malformed record is refused here with a readable sentence rather than dropped.
        A version-replacing reinstall of a chip that carries no record keeps the record
        already stored under that pack id, so replacing the definition does not wipe the
        notebook written into it.
        """
        manifest, art, record, conversation = _read_archive(raw)
        pack = manifest.pack
        if pack.pack_id in PACKS:
            raise Invalid(f"{pack.pack_id} is a built-in chip; built-in chips cannot be replaced or removed")
        if not PACK_ID_RE.match(pack.pack_id):
            raise Invalid("pack_id must be lowercase letters, digits and underscores, at most 64 characters,"
                          " so that it can name a file inside chips/installed")
        target = self._target(pack.pack_id)
        existing = self._scan().get(pack.pack_id)
        if existing is not None and existing[0].version == pack.version:
            raise Invalid(f"{pack.pack_id} version {pack.version} is already installed;"
                          " remove it first, or install a chip with a different version")
        if existing is not None and existing[4] is not None:
            # The stored chip is intact but its record cannot be parsed, so replacing the
            # file would destroy bytes this build cannot put back. Refused, with the two
            # routes that keep them: export copies the file out, delete removes it.
            raise Invalid(f"{pack.pack_id} is already installed with a record this build cannot"
                          f" read ({existing[4]}); export or delete that chip before installing"
                          " over it, so its bytes are not lost")
        if not _record_has_content(record) and existing is not None:
            # The incoming chip carries no record of its own: the record already in the
            # installed chip is the researcher's work and outlives a version replacement.
            record = read_chip_record(target.read_bytes())
        if not conversation.strip() and existing is not None:
            # The same holds for the conversation: replacing the definition of a chip must
            # not erase the talk that already happened inside it.
            conversation = read_chip_conversation(target.read_bytes())
        blob = _encode(manifest, art, record, conversation)
        try:
            self.installed.mkdir(parents=True, exist_ok=True)
            scratch = target.with_name(target.name + ".part")
            scratch.write_bytes(blob)
            os.replace(scratch, target)
        except OSError as exc:
            raise Invalid(f"The chip could not be written to chips/installed: {exc.strerror or 'write error'}")
        # The freshly installed chip is usable immediately, without a restart: it is
        # the pack the researcher will pick for the next question.
        try:
            from .packs import register_installed
            register_installed(pack)
        except Exception:  # never fail an install because the registry could not update
            pass
        return self.record(pack.pack_id)

    def open_bytes(self, raw: bytes) -> dict:
        """Open an identical installed notebook without treating it as an update."""
        manifest, _art, _record, _conversation = _read_archive(raw)
        pack_id = manifest.pack.pack_id
        if pack_id in PACKS:
            if raw != builtin_chip(pack_id):
                raise Invalid("This file differs from the built-in chip; it cannot replace a built-in")
            return self.record(pack_id)
        if PACK_ID_RE.match(pack_id):
            target = self._target(pack_id)
            if target.is_file():
                try:
                    if target.read_bytes() == raw:
                        return self.record(pack_id)
                except OSError as exc:
                    raise Invalid(f"{pack_id} could not be read: {exc.strerror or 'read error'}") from None
        # Different bytes retain the installer's version/collision protections.
        return self.install_bytes(raw)

    def install_path(self, path, *, open_existing=False) -> dict:
        if not isinstance(path, str) or not path.strip():
            raise Invalid("path must be a non-empty string naming an absolute .chip file")
        named = Path(path).expanduser()
        if not named.is_absolute():
            raise Invalid("The chip path must be absolute; the workbench does not guess a directory")
        resolved = named.resolve()
        if not _inside(resolved, self._source_roots()):
            raise Invalid(f"Refused: {path} is outside the folders a chip may be installed from")
        if not resolved.is_file():
            raise Invalid(f"No chip file at {path}")
        try:
            if resolved.stat().st_size > MAX_SOURCE_BYTES:
                raise Invalid("That file is far too large to be a chip")
            raw = resolved.read_bytes()
        except Invalid:
            raise
        except OSError as exc:
            raise Invalid(f"{path} could not be read: {exc.strerror or 'read error'}")
        return self.open_bytes(raw) if open_existing else self.install_bytes(raw)

    def install_base64(self, encoded, filename=None, *, open_existing=False) -> dict:
        if not isinstance(encoded, str) or not encoded.strip():
            raise Invalid("base64 must be a non-empty string carrying a .chip file")
        if filename is not None:
            if not isinstance(filename, str) or not filename.strip():
                raise Invalid("filename must be non-empty text when it is sent")
            if "/" in filename or "\\" in filename or not filename.lower().endswith(".chip"):
                raise Invalid("filename must be a bare name ending with .chip")
        try:
            raw = base64.b64decode("".join(encoded.split()), validate=True)
        except (binascii.Error, ValueError):
            raise Invalid("base64 is not valid base64 text") from None
        return self.open_bytes(raw) if open_existing else self.install_bytes(raw)

    def delete(self, pack_id) -> dict:
        if not isinstance(pack_id, str) or not pack_id:
            raise Invalid("pack_id is required")
        if pack_id in PACKS:
            raise Invalid(f"{pack_id} is a built-in chip; built-in chips cannot be deleted")
        target = self._target(pack_id)
        if not target.is_file():
            raise KeyError(pack_id)
        try:
            target.unlink()
        except OSError as exc:
            raise Invalid(f"{pack_id} could not be removed: {exc.strerror or 'delete error'}")
        # A removed chip stops being a usable pack at once, so a deleted pack id can
        # never be pinned onto a new question.
        try:
            from .packs import unregister_installed
            unregister_installed(pack_id)
        except Exception:
            pass
        return {"deleted": pack_id, "source": "file"}

    # -- the research record --------------------------------------------------
    def read_record(self, pack_id) -> dict:
        """The record installed in one chip; a chip without a record reads as empty."""
        path = self._record_source(pack_id)
        if not path.is_file():
            # A built-in pack whose sample file was not exported carries no record.
            return Record().model_dump(mode="json")
        try:
            record = read_chip_record(path.read_bytes())
        except OSError as exc:
            raise Invalid(f"{pack_id} could not be read: {exc.strerror or 'read error'}")
        except Invalid as exc:
            # The chip itself is intact and still listed; only its record cannot be parsed.
            raise Invalid(f"{pack_id} is stored with a record this build cannot read: {exc}") from None
        return record.model_dump(mode="json")

    def write_record(self, pack_id, record) -> dict:
        """Rewrites one installed chip in place, keeping its manifest and art.

        Only ``chips/installed/`` is written, and a built-in id is refused exactly as
        ``delete`` refuses it. The new archive is the canonical form this module
        validates, so what lands on disk is what ``read_record`` will return.
        """
        if not isinstance(pack_id, str) or not pack_id:
            raise Invalid("pack_id is required")
        if pack_id in PACKS:
            raise Invalid(f"{pack_id} is a built-in chip; built-in chips cannot be modified")
        checked = _checked_record(record)
        target = self._target(pack_id)
        if not target.is_file():
            raise KeyError(pack_id)
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise Invalid(f"{pack_id} could not be read: {exc.strerror or 'read error'}")
        # One validated pass gives the manifest, the art and the conversation. The
        # conversation lives in the same archive, so rewriting the record must carry it
        # across unchanged: writing a record is not a place where the talk may vanish.
        manifest, art, _record, conversation = _read_archive(raw)
        if manifest.pack.pack_id != pack_id:
            raise Invalid(f"{pack_id} does not hold the pack of that name; refusing to rewrite it")
        try:
            scratch = target.with_name(target.name + ".part")
            scratch.write_bytes(_encode(manifest, art, checked, conversation))
            os.replace(scratch, target)
        except OSError as exc:
            raise Invalid(f"The record could not be written to chips/installed: {exc.strerror or 'write error'}")
        return checked.model_dump(mode="json")

    # -- the conversation log -------------------------------------------------
    def conversation_budget(self, pack_id) -> int:
        """Remaining expanded bytes after canonical manifest, art and record."""
        if pack_id in PACKS:
            raise Invalid(f"{pack_id} is a built-in chip; built-in chips cannot be modified")
        manifest, art, record, _conversation = _read_archive(
            self._record_source(pack_id).read_bytes())
        blob = _encode(manifest, art, record)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            used = sum(info.file_size for info in archive.infolist())
        return min(MAX_CONVERSATION_BYTES, MAX_TOTAL_BYTES - used)

    def read_conversation(self, pack_id) -> dict:
        """The conversation stored inside one chip; a chip without one reads as empty.

        The text is the note itself, unchanged, so a caller shows exactly what the file
        holds. A built-in chip whose sample file was not exported carries no conversation.
        """
        path = self._record_source(pack_id)
        if not path.is_file():
            return {"pack_id": pack_id, "text": "", "bytes": 0}
        try:
            text = read_chip_conversation(path.read_bytes())
        except OSError as exc:
            raise Invalid(f"{pack_id} could not be read: {exc.strerror or 'read error'}")
        except Invalid as exc:
            raise Invalid(f"{pack_id} is stored with a record this build cannot read: {exc}") from None
        return {"pack_id": pack_id, "text": text, "bytes": len(text.encode("utf-8"))}

    def write_conversation(self, pack_id, text) -> dict:
        """Stores ``text`` as ``conversation.txt`` inside one installed chip.

        The manifest, the art and the record are kept exactly as they were, and only
        ``chips/installed/`` is written: a built-in id is refused the way ``delete``
        refuses it. Text that is empty or only whitespace removes the member, so a chip
        never carries an empty log around.
        """
        if not isinstance(pack_id, str) or not pack_id:
            raise Invalid("pack_id is required")
        if pack_id in PACKS:
            raise Invalid(f"{pack_id} is a built-in chip; built-in chips cannot be modified")
        if not isinstance(text, str):
            raise Invalid("text must be a string holding the conversation")
        target = self._target(pack_id)
        if not target.is_file():
            raise KeyError(pack_id)
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise Invalid(f"{pack_id} could not be read: {exc.strerror or 'read error'}")
        manifest, art, record, _conversation = _read_archive(raw)
        if manifest.pack.pack_id != pack_id:
            raise Invalid(f"{pack_id} does not hold the pack of that name; refusing to rewrite it")
        kept = text if text.strip() else None
        try:
            scratch = target.with_name(target.name + ".part")
            scratch.write_bytes(_encode(manifest, art, record, kept))
            os.replace(scratch, target)
        except OSError as exc:
            raise Invalid(f"The conversation could not be written to chips/installed:"
                          f" {exc.strerror or 'write error'}")
        return self.read_conversation(pack_id)

    def _record_source(self, pack_id) -> Path:
        """Which file a record read may look at. A built-in id reads its sample file."""
        if not isinstance(pack_id, str) or not pack_id:
            raise Invalid("pack_id is required")
        if pack_id in PACKS:
            return BUILTIN_DIR / f"{pack_id}.chip"
        target = self._target(pack_id)
        if not target.is_file():
            raise KeyError(pack_id)
        return target

    # -- authoring ------------------------------------------------------------
    def export(self, pack_id) -> dict:
        """A copy of any existing chip as real file bytes, so a person can edit or share it."""
        if not isinstance(pack_id, str) or not pack_id:
            raise Invalid("pack_id is required")
        if pack_id in PACKS:
            raw = builtin_chip(pack_id)
        else:
            found = self._scan().get(pack_id)
            if found is None:
                raise KeyError(pack_id)
            try:
                raw = found[2].read_bytes()
            except OSError as exc:
                raise Invalid(f"{pack_id} could not be read: {exc.strerror or 'read error'}")
        # The archive goes out byte for byte, so exporting a chip whose record this build
        # cannot parse is the recovery path: only the manifest names the file.
        manifest, _art, _raw_record, _raw_conversation = _read_parts(raw)
        return {"filename": f"{manifest.pack.pack_id}-{manifest.pack.version}.chip",
                "base64": base64.b64encode(raw).decode("ascii")}


# ------------------------------------------------------------------ command line
def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.chips", description="Build and export .chip files.")
    subs = parser.add_subparsers(dest="command", required=True)
    build = subs.add_parser("build", help="build one chip from a JSON payload")
    build.add_argument("--from", dest="source", required=True, help="JSON file holding the chip fields")
    build.add_argument("--out", dest="out", required=True, help="where to write the .chip file")
    export = subs.add_parser("export", help="write the built-in packs as .chip files")
    export.add_argument("--dest", dest="dest", default=None, help="output directory (default chips/builtin/)")
    record = subs.add_parser("record", help="read or replace the research record inside a .chip file")
    record_subs = record.add_subparsers(dest="record_command", required=True)
    show = record_subs.add_parser("show", help="print the record a chip carries as JSON")
    show.add_argument("--chip", dest="chip", required=True, help="the .chip file to read")
    put = record_subs.add_parser("put", help="replace the record inside a .chip file")
    put.add_argument("--chip", dest="chip", required=True, help="the .chip file to rewrite")
    put.add_argument("--from", dest="source", required=True, help="JSON file holding the record fields")
    put.add_argument("--out", dest="out", default=None,
                     help="write the result here instead of rewriting --chip in place")
    conversation = subs.add_parser("conversation", help="read or replace the conversation inside a .chip file")
    conversation_subs = conversation.add_subparsers(dest="conversation_command", required=True)
    conversation_show = conversation_subs.add_parser("show", help="print the conversation a chip carries")
    conversation_show.add_argument("--chip", dest="chip", required=True, help="the .chip file to read")
    conversation_put = conversation_subs.add_parser("put", help="replace the conversation inside a .chip file")
    conversation_put.add_argument("--chip", dest="chip", required=True, help="the .chip file to rewrite")
    conversation_put.add_argument("--from", dest="source", required=True, help="text file holding the conversation")
    conversation_put.add_argument("--out", dest="out", default=None,
                                 help="write the result here instead of rewriting --chip in place")
    return parser


def read_payload(path) -> dict:
    """Loads a create-form payload from a JSON file. ``art_png`` may be a base64 string."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Invalid(f"{path} could not be read: {exc.strerror or 'read error'}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Invalid(f"{path} is not JSON: {getattr(exc, 'msg', exc)}")
    if not isinstance(payload, dict):
        raise Invalid(f"{path} must hold a JSON object with the chip fields")
    art = payload.get("art_png")
    if isinstance(art, str):
        try:
            payload["art_png"] = base64.b64decode("".join(art.split()), validate=True)
        except (binascii.Error, ValueError):
            raise Invalid("art_png in the JSON file is not valid base64") from None
    return payload


def write_chip(payload, out) -> Path:
    """Builds a chip from ``payload`` and writes it to ``out``; returns the path."""
    raw = build_chip(payload)
    path = Path(out)
    path.write_bytes(raw)
    return path


def read_record_payload(path) -> dict:
    """Loads a record from a JSON file, so the CLI can inspect and replace one."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Invalid(f"{path} could not be read: {exc.strerror or 'read error'}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Invalid(f"{path} is not JSON: {getattr(exc, 'msg', exc)}")
    except RecursionError:
        raise Invalid(f"{path} is nested too deeply to be read as a chip record") from None
    if not isinstance(payload, dict):
        raise Invalid(f"{path} must hold a JSON object with the record fields")
    return payload


def read_conversation_payload(path) -> str:
    """Loads the conversation text from a plain text file, so a terminal can write one."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise Invalid(f"{path} could not be read: {exc.strerror or 'read error'}")
    except UnicodeDecodeError:
        raise Invalid(f"{path} is not UTF-8 text") from None


def rewrite_chip_record(chip_path, record, out=None) -> Path:
    """Writes a chip with ``record`` replaced, keeping its manifest, art and conversation.

    The output is the canonical archive ``read_chip`` accepts, so a chip a terminal
    wrote installs exactly like one the app built.
    """
    source = Path(chip_path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise Invalid(f"{chip_path} could not be read: {exc.strerror or 'read error'}")
    manifest, art, _record, conversation = _read_archive(raw)
    checked = _checked_record(record)
    path = Path(out) if out is not None else source
    path.write_bytes(_encode(manifest, art, checked, conversation))
    return path


def rewrite_chip_conversation(chip_path, conversation, out=None) -> Path:
    """Writes a chip with ``conversation`` replaced, keeping its manifest, art and record.

    The text is stored exactly as given, so a person can write or read the log in any
    editor. Nothing in it is parsed or run: it is a note, carried byte for byte.
    """
    source = Path(chip_path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise Invalid(f"{chip_path} could not be read: {exc.strerror or 'read error'}")
    manifest, art, record, _conversation = _read_archive(raw)
    if not isinstance(conversation, str):
        raise Invalid("The conversation must be text")
    path = Path(out) if out is not None else source
    path.write_bytes(_encode(manifest, art, record, conversation))
    return path


def main(argv=None) -> int:
    """Runs the CLI and returns an exit code; the parser is a plain function so tests can call it."""
    args = _cli_parser().parse_args(argv)
    try:
        if args.command == "build":
            path = write_chip(read_payload(args.source), args.out)
            manifest, _ = read_chip(path.read_bytes())
            print(path)
            print(manifest.pack.identity()["manifest_sha256"])
            return 0
        if args.command == "record":
            if args.record_command == "show":
                print(json.dumps(read_chip_record(Path(args.chip).read_bytes()).model_dump(mode="json"),
                                 sort_keys=True, indent=2, ensure_ascii=False))
                return 0
            path = rewrite_chip_record(args.chip, read_record_payload(args.source), args.out)
            print(path)
            return 0
        if args.command == "conversation":
            if args.conversation_command == "show":
                print(read_chip_conversation(Path(args.chip).read_bytes()), end="")
                return 0
            path = rewrite_chip_conversation(args.chip, read_conversation_payload(args.source), args.out)
            print(path)
            return 0
        written = export_builtins(args.dest)
    except Invalid as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        return 2
    for path in written:
        print(path)
        print(manifest_sha256(path.read_bytes()))
    return 0


def size_label(size: int) -> str:
    """A byte count as the reader should see it in a refusal sentence.

    The caps are whole megabytes, so a refusal that printed the raw kilobyte division
    would say "102400 KB" where a person expects "100 MB".
    """
    if size >= 1024 * 1024 and size % (1024 * 1024) == 0:
        return f"{size // (1024 * 1024)} MB"
    if size >= 1024 and size % 1024 == 0:
        return f"{size // 1024} KB"
    return f"{size} bytes"


def manifest_sha256(raw: bytes) -> str:
    """The hash of a chip's manifest.json as it sits inside the archive."""
    return hashlib.sha256(_manifest_bytes(read_chip(raw)[0])).hexdigest()


if __name__ == "__main__":  # pragma: no cover - the CLI body is exercised through main()
    raise SystemExit(main())
