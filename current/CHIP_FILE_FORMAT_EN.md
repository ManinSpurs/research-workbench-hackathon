# The `.chip` File Format

This document describes one file: a `.chip`. It is self-contained, so a reader who has
this page can build, inspect, install or hand off a chip file without reading any other
document. Every statement below is taken from the code in
`current/rehearsal_build/app/`, cited by path and line, and describes the behaviour as
read from the source. Nothing in this document was confirmed by running a test suite or a
server.

---

## 1. What a chip is, and what it is not

A chip is a single file: a plain ZIP archive that carries a domain definition, and
optionally the research notebook that was written inside it
(`current/rehearsal_build/app/chips.py:1-11`). The name ends in `.chip`, but the bytes are
an ordinary ZIP, so any ZIP tool can list it, and any text editor can open the JSON and
text members.

What a chip carries:

- a required `manifest.json`, holding the domain definition (`pack`) and the optional
  `presentation` block (`chips.py:5-6`, `chips.py:137-141`);
- an optional `chip.png`, a PNG used as label art for that one chip
  (`chips.py:7`, `chips.py:66`);
- an optional `record.json`, the research record: questions, answers, results, code and
  the per-chip checklist (`chips.py:8`, `chips.py:67`);
- an optional `conversation.txt`, the plain-text secretary log: the talk, the decision
  each question reached, and the evidence behind it (`chips.py:9-11`, `chips.py:68`).

What a chip is not:

- It is not a model. It carries no weights, no provider binding and no credentials.
- It is not executable code. The archive is only ever opened for reading, member names
  and sizes are checked before extraction, and nothing in it is executed, imported,
  compiled or evaluated; code kept in a record is data and stays data
  (`chips.py:27-32`).
- It is not a label image. The cartridge body (notch, dot texture, border) is drawn by
  the UI for every chip, so a chip normally carries no art at all and its identity is the
  border colour plus the name, both in the manifest (`chips.py:20-26`).
- It is not encrypted and not a container of nested files: only the four member names
  below are accepted, and an encrypted member is refused (`chips.py:308-320`).

---

## 2. Members and size caps

The four member names are fixed constants (`chips.py:64-69`):

| Member | Required | Per-member cap |
| --- | --- | --- |
| `manifest.json` | yes | 262144 bytes (256 KB) |
| `chip.png` | no | 104857600 bytes (100 MB) |
| `record.json` | no | 104857600 bytes (100 MB) |
| `conversation.txt` | no | 104857600 bytes (100 MB) |

The caps are `MAX_MANIFEST_BYTES = 256 * 1024`, `MAX_ART_BYTES = 100 * 1024 * 1024`,
`MAX_RECORD_BYTES = 100 * 1024 * 1024` and `MAX_CONVERSATION_BYTES = 100 * 1024 * 1024`
(`chips.py:71-74`), selected per name by `_member_cap` (`chips.py:280-288`).

Two further caps apply to the archive as a whole:

- Total uncompressed size: `MAX_TOTAL_BYTES = 100 * 1024 * 1024` (100 MB), summed over the
  declared `file_size` of every member (`chips.py:75`, `chips.py:324-327`).
- Member count: `MAX_MEMBERS = 5` (`chips.py:70`). A chip with more than five entries is
  refused (`chips.py:309-310`). Because only four names are accepted and a repeated name
  is refused (`chips.py:313-315`), a valid chip always has four or fewer members; the
  count cap is the outer bound and is checked first.

One more cap applies to a file being installed from a path: `MAX_SOURCE_BYTES = 100 * 1024
* 1024` (100 MB) on the file on disk, before it is read (`chips.py:76`,
`chips.py:1069-1070`).

The per-member caps and the total cap are now the same number, so one maximum-size member
plus a manifest already exceeds the total. In practice the total cap is what a chip runs
into first. A refusal sentence names its cap through `app/chips.py:size_label`, so
a whole-megabyte cap reads as "100 MB" rather than a raw kilobyte figure.

Compression must be stored or deflate (`chips.py:319-320`). Everything the app writes is
deflated, with fixed timestamps (`1980-01-01 00:00:00`), mode `0600` and a fixed
`create_system`, and members in the fixed order manifest, art, record, conversation, so
the same chip is the same bytes on any machine (`chips.py:520-551`).

`chip.png`, when present, must be a PNG: it has to start with the 8-byte PNG signature and
end with the `IEND` chunk trailer, and be longer than 20 bytes (`chips.py:80-81`,
`chips.py:368-369`, `chips.py:335-336`). The check is a signature check, not a full PNG
decode.

---

## 3. `manifest.json`

`manifest.json` is UTF-8 JSON holding one object (`chips.py:260-278`). Its model is
(`chips.py:137-141`):

```python
class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal[1]
    pack: DomainPack
    presentation: Presentation = Presentation()
```

- `format`: required, and must be exactly the integer `1` (`FORMAT = 1`,
  `chips.py:64`). A boolean or any other value is refused before model validation with
  `manifest.json declares format {declared!r}; this build reads chip format 1`
  (`chips.py:248-258`).
- `pack`: required, an object holding the `DomainPack` fields (`chips.py:253-256`).
- `presentation`: optional; when absent it defaults to an empty presentation
  (`chips.py:141`).

Unknown fields are refused at every level: `Manifest`, `DomainPack`, `Presentation` and
all record models set `extra="forbid"` (`chips.py:138`, `packs.py:11`, `chips.py:124`,
`chips.py:151` and following), so a manifest with an extra key is refused with the
pydantic message summarised into one sentence (`chips.py:240-246`, `chips.py:269-278`).

### 3.1 `pack`: the `DomainPack` fields

The model is `current/rehearsal_build/app/packs.py:10-23`:

| Field | Type | Required in `manifest.json` | Notes |
| --- | --- | --- | --- |
| `pack_id` | `str` | yes | Install also requires it to match `^[a-z0-9][a-z0-9_]{0,63}$` (`chips.py:83`, `chips.py:1018-1020`); it names the file inside `chips/installed/` |
| `version` | `str` | yes | Build form requires `^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$` (`chips.py:100`, `chips.py:564-566`) |
| `title` | `str` | yes | Build form requires non-empty text, at most 120 characters (`MAX_TITLE_CHARS`, `chips.py:101`, `chips.py:567-571`) |
| `instructions` | `str` | yes | Build form requires non-empty text (`chips.py:572-574`) |
| `required_context` | `tuple[str, ...]` | yes | JSON array of strings |
| `checks` | `tuple[str, ...]` | yes | JSON array of strings |
| `allowed_tools` | `tuple[Literal["registered_evidence_read"], ...]` | yes | The only accepted element value is the string `registered_evidence_read` (`packs.py:18`) |
| `implementation` | `Literal["instruction_pack"]` | no, defaults to `instruction_pack` | The only accepted value (`packs.py:19`) |

`required_context`, `checks` and `allowed_tools` have no default in `DomainPack`, so a
manifest read from disk must carry all three (`packs.py:16-19`). The chip *build* path
defaults them to empty lists when a form payload omits them (`chips.py:615-617`).

The length and format limits on `pack_id`, `version` and `title`, and the requirement
that `instructions` be non-empty, are enforced by the chip *build* path,
`_field_problem` (`chips.py:558-590`), which runs for a create-form payload and for
`POST /chips/build` and the CLI `build` subcommand. The three
string lists (`required_context`, `checks`, `allowed_tools`) get no length cap there
either: `_field_problem` only requires each of them to be a list of non-empty strings
(`chips.py:575-580`). A chip
read from disk is validated by `DomainPack` itself, which declares no `max_length` on
these fields (`packs.py:10-20`). The regex checks on `pack_id` at install
(`chips.py:1018-1020`) apply in every case.

`DomainPack.identity()` returns `{"pack_id", "pack_version", "manifest_sha256"}`, where
`manifest_sha256` is the SHA-256 of the pack object dumped with sorted keys and compact
separators (`packs.py:21-24`). This is a different value from the hash of the whole
`manifest.json` member, which is what `build_result` reports
(`app/chips.py:build_result`, `app/chips.py:manifest_sha256`).

### 3.2 `presentation`

`Presentation` is optional as a whole and strict about its own fields
(`chips.py:117-134`):

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `tagline` | `str` | `""` | at most 200 characters (`MAX_TAGLINE_CHARS`, `chips.py:102`) |
| `color` | `str` | `"#4a5568"` (`DEFAULT_COLOR`, `chips.py:114`) | must match `^#[0-9a-fA-F]{6}$` (`chips.py:84`); stored lowercased (`chips.py:129-134`); refusal text is `color must be a "#rrggbb" hex color, for example "#2f5d8a"` |
| `glyph` | `str` | `""` | at most 2 characters (`MAX_GLYPH_CHARS`, `chips.py:103`); the length is measured in characters, so a single non-ASCII symbol counts as one |

`color` is the chip's border colour. Because it is lowercased on validation, `#2F5D8A`
and `#2f5d8a` are the same stored value.

### 3.3 An illustrative minimal manifest

The following object is constructed from the models above and is **illustrative only**;
it is not copied from any chip file that exists in this tree.

```json
{
  "format": 1,
  "pack": {
    "pack_id": "example_topic",
    "version": "0.1.0",
    "title": "Example Topic",
    "instructions": "Answer only what the recorded evidence supports.",
    "required_context": ["research_question"],
    "checks": ["dataset_specific_columns"],
    "allowed_tools": ["registered_evidence_read"],
    "implementation": "instruction_pack"
  },
  "presentation": {
    "tagline": "An example chip",
    "color": "#4a5568",
    "glyph": "Ex"
  }
}
```

---

## 4. `record.json`

`record.json` is optional. When present it is UTF-8 JSON holding one object with five
lists, all optional and empty by default (`chips.py:199-207`). The list names are
`RECORD_LISTS = ("questions", "answers", "results", "code", "harness")`
(`chips.py:407`). A member that is not a list is refused with `record.json {key} must be a
list`, and a list longer than `MAX_RECORD_ENTRIES = 5000` entries is refused with
`record.json {key} holds {n} entries, more than the 5000 entries allowed in one list`
(`chips.py:77`, `chips.py:410-422`). The cap is per list, not for the whole record.

Shared caps: `MAX_RECORD_TEXT_CHARS = 100_000` for long text fields and
`MAX_CODE_SOURCE_CHARS = 200_000` for code source (`chips.py:78-79`). Every field has a
default, so an entry may be `{}`; every model refuses unknown fields.

`questions[]`: one question asked inside this chip (`chips.py:149-158`)

| Field | Cap |
| --- | --- |
| `question_id` | 128 |
| `text` | 100000 |
| `working_text` | 100000 |
| `asked_at` | 64 |
| `pack_version` | 64 |
| `manifest_sha256` | 128 |

`answers[]`: the researcher's recorded answer (`chips.py:161-168`)

| Field | Cap |
| --- | --- |
| `question_id` | 128 |
| `kind` | 64 |
| `value` | 100000 |
| `answered_at` | 64 |

`results[]`: an analysis result kept as the JSON string the app already produces
(`chips.py:171-177`)

| Field | Cap |
| --- | --- |
| `question_id` | 128 |
| `body_json` | 100000 |
| `created_at` | 64 |

`code[]`: analysis code kept as text (`chips.py:180-187`)

| Field | Cap |
| --- | --- |
| `name` | 256 |
| `language` | 64 |
| `source` | 200000 |
| `created_at` | 64 |

`harness[]`: one accumulated per-chip rule or checklist entry (`chips.py:190-196`)

| Field | Cap |
| --- | --- |
| `key` | 256 |
| `value` | 100000 |
| `added_at` | 64 |

A listed code entry is **data**. It is never run, imported, compiled or evaluated; the
module carries the text in and out byte for byte, exactly like a note
(`chips.py:29-32`, `chips.py:180-181`).

Two structural rules that surprise readers:

- A record whose only content is empty entries, such as `{"questions": [{}]}`, counts as
  no record: `_record_has_content` returns false and the member is not written
  (`chips.py:478-488`, `chips.py:533-536`).
- The written form is canonical JSON (sorted keys, indent 2), which is commonly 1.15 to
  3.4 times larger than the bytes that arrived, so the canonical bytes are re-checked
  against the 100 MB record cap before anything is written
  (`chips.py:458-476`). A record that reads in can therefore be refused on write with a
  sentence naming its size and that cap.

---

## 5. `conversation.txt`

### 5.1 What it is

`conversation.txt` is optional plain UTF-8 text, stored verbatim. It is never parsed,
never evaluated and never rewritten line by line; it is a note the researcher can open in
any editor (`chips.py:373-384`). A member that is not valid UTF-8 is refused with
`conversation.txt is not UTF-8 text` (`chips.py:381-384`).

A chip without the member reads as empty text: `read_chip_conversation` returns `""`
(`chips.py:401-403`), and `ChipStore.read_conversation` returns
`{"pack_id": ..., "text": "", "bytes": 0}` when the chip file is absent or carries no log
(`chips.py:1164-1179`). A chip with an empty log is not an error anywhere.

Writing empty or whitespace-only text removes the member instead of leaving an empty log:
`write_conversation` keeps the text only when `text.strip()` is non-empty and passes
`None` otherwise (`chips.py:1205-1208`), and `_encode` writes the member
only when the text is non-blank (`chips.py:537-539`).

The text is capped at `MAX_CONVERSATION_BYTES` measured on the encoded bytes, which is
the same measure the reader applies, so text accepted here always reads back out
(`chips.py:387-398`).

The common archive writer also checks the combined uncompressed member size and the
finished archive size before replacing an installed file. A refused rewrite leaves
the previous chip bytes intact. For generated logs, `ChipStore.conversation_budget`
subtracts the canonical manifest, art and record sizes from the total allowance.
`compose_and_store` fits the log into that remaining space with an explicit truncation
notice. If even that notice cannot fit, it refuses without changing the chip.
Directly submitted text is not silently truncated.

### 5.2 Which operations keep it

- Rewriting the record keeps it: `write_record` reads the existing archive in one
  validated pass and writes `_encode(manifest, art, checked, conversation)` with the
  conversation carried across unchanged (`chips.py:1130-1163`). The CLI equivalent
  `rewrite_chip_record` does the same (`chips.py:1330-1346`).
- Installing over an existing chip id keeps it: when the incoming archive carries no
  conversation text, `install_bytes` re-reads the stored conversation and keeps it, so
  replacing a definition does not erase the talk (`chips.py:1037-1040`).
- Export keeps it, and in fact brings it up to date first. See section 8.
- Rewriting the record or the conversation never changes the manifest or the art
  (`chips.py:1130-1163`, `chips.py:1181-1213`).

### 5.3 How the composed log is built

The app composes the log itself in `current/rehearsal_build/app/chip_conversation.py`.
`compose` reads what the app already stored and lays it out as text; it makes no model
call, opens no socket, writes no file and invents no entry
(`chip_conversation.py:1-20`, `chip_conversation.py:208-227`).

Stored tables read:

- `secretary_turns`, filtered by `project_id`, and by `pack_id` narrowed to
  `(pack_id=? OR pack_id IS NULL)` when a chip is named, ordered by `created_at, rowid`
  (`chip_conversation.py:46-51`);
- `questions`, either all questions of the project in `rowid` order, or, when a chip is
  named, the questions joined to `question_contexts` on that `pack_id`
  (`chip_conversation.py:75-86`);
- `decisions`, the latest row per question ordered by `rowid` descending
  (`chip_conversation.py:88-91`);
- `evidence`, all rows per question in `rowid` order (`chip_conversation.py:93-96`);
- `projects`, only to print the project name (`chip_conversation.py:102-105`).

Turn selection and the turn limit: the query applies **no row limit**. The docstring
states explicitly that the log reads the table directly instead of the paged review
reader, because a log that dropped its earliest exchanges would misrepresent the record
(`chip_conversation.py:33-40`). The only bound on length is the byte cap in section 5.4.
Turns with `status` equal to `discarded` are not printed; they are counted instead and
the count is written as `{n} turn(s) were set aside and are not printed in this record.`
(`chip_conversation.py:52-60`, `chip_conversation.py:133-137`).

Section order: a header block naming the chip and the project
(`chip_conversation.py:108-119`), then `CONVERSATION`, then `DECISIONS`, then `EVIDENCE`
(`chip_conversation.py:208-227`). If there is neither a turn nor a question, the log
stops after the conversation section with `Nothing has been recorded for this chip yet.`
(`chip_conversation.py:28`, `chip_conversation.py:220-223`). A question with no recorded
decision is absent from the decisions section, and when no question has a decision the
section says `No decision has been recorded for a question in this chip.`
(`chip_conversation.py:141-158`).

A decision whose reason was never recorded is written with the code's constant verbatim:
`NO_REASON = "Reason not recorded"`, on the line `reason: Reason not recorded`
(`chip_conversation.py:29`, `chip_conversation.py:150-153`). Absent evidence is written as
`- source: not recorded` or `  excerpt: not recorded` with its two leading spaces
(`chip_conversation.py:170-177`).

### 5.4 Deterministic truncation at the cap

The output is deterministic: the same records always produce the same bytes. When the
joined text exceeds the cap, `_fit` cuts it at a line boundary and appends a note naming
the limit (`chip_conversation.py:183-206`):

```
[This log was truncated at the 100 MB chip conversation limit; the records in the app still hold the full history.]
```

The budget is the cap minus the encoded length of that note; whole lines are appended
while they fit, the result is right-stripped and the note is appended
(`chip_conversation.py:194-206`). Nothing is elided silently: the end of the log is
missing and the note says so.

---

## 6. Refusal rules

Most refusals raise `Invalid`, which the HTTP layer turns into status 400 with error code
`invalid_chip` and the message as delivered (`chips.py:110-111`,
`current/rehearsal_build/app/api.py:556-565`). A missing chip is the exception: the store
raises `KeyError`, which the same guard answers as status 404.

Archive level (`chips.py:290-338`):

| Condition | Message |
| --- | --- |
| not bytes, or empty | `This chip is empty` |
| not a readable ZIP | `This file is not a chip: it is not a readable ZIP archive` |
| ZIP with no entries | `This chip is empty: it has no members` |
| more entries than the count cap | `A chip holds at most 5 members; this one has {n}` |
| member name repeated | `The chip repeats the member {name!r}` |
| encrypted member bit set | `An encrypted chip member is refused; chips are never encrypted` |
| compression other than stored/deflate | `{name} must use stored or deflate compression` |
| member over its own cap | `{name} is larger than the {size_label(cap)} limit for that member` |
| members together over the total cap | `The chip expands to more than the 100 MB total limit` |
| no `manifest.json` | `A chip must contain manifest.json` |
| `chip.png` fails the signature check | `chip.png is not a PNG image` |
| a member the ZIP names cannot be read | `{name} could not be read out of the chip archive` (`chips.py:234-235`) |
| a path install where no file exists | `No chip file at {path}` (`chips.py:1068`) |
| an empty `pack_id` passed to the store | `pack_id is required` (`chips.py:998`, `chips.py:1095`, `chips.py:1138`, `chips.py:1190`, `chips.py:1218`, `chips.py:1230`) |

Member names, checked by `_member_name` in this order (`chips.py:211-227`):

| Condition | Message |
| --- | --- |
| name is not a string, or is empty | `A chip member has an empty name` |
| name contains a backslash | `A chip member name may not contain a backslash: {name!r}` |
| name starts with `/` or is absolute | `A chip member name may not be an absolute path: {name!r}` |
| name ends with `/` (a directory entry) | `A chip may not contain directories` |
| any path part is empty, `.` or `..` | `A chip member name may not traverse directories: {name!r}` |
| name is not one of the four | `Unknown chip member {name!r}; a chip holds only manifest.json, chip.png, record.json and conversation.txt` |

Because the directory check runs before the traversal check, an entry named `folder/`
is refused as a directory, while `a//b` and `../x` are refused as traversal. A name such
as `sub/record.json` passes the traversal check and is then refused as an unknown member.

Payload and model level:

- `manifest.json` that is not UTF-8 JSON: `manifest.json is not UTF-8 JSON`
  (`chips.py:263-264`); nesting too deep: `manifest.json is nested too deeply to be read
  as a chip manifest` (`chips.py:267-268`); not an object: `manifest.json must be a JSON
  object` (`chips.py:269-270`).
- A schema mismatch is summarised as `manifest.json does not match the chip manifest: ...`
  with at most the first three field locations (`chips.py:240-246`, `chips.py:276-277`).
- `record.json` has the parallel messages for decode, nesting and object shape, plus
  `record.json does not match the chip record: ...` (`chips.py:424-445`).
- Install from a path: the path must be a non-empty absolute string
  (`chips.py:1058-1063`), must resolve inside the workbench data folder, the app folder,
  the home folder or the temp folder (`chips.py:907-914`, `chips.py:1065-1066`, with
  message `Refused: {path} is outside the folders a chip may be installed from`), must
  exist, and must be at most the 100 MB source cap, else `That file is far too large to be
  a chip` (`chips.py:1067-1072`).
- Install from base64: the text must decode as strict base64, and an optional `filename`
  must be a bare name ending in `.chip` with no slash or backslash
  (`chips.py:1079-1092`).
- A `pack_id` that would write outside `chips/installed/`:
  `Refused: that pack_id would write outside chips/installed` (`chips.py:996-1001`).

---

## 7. Working with a chip from a terminal

The CLI is `python -m app.chips`, defined by the argparse setup at `chips.py:1249-1275`
and dispatched by `main` at `chips.py:1367-1402`. Subcommands are required, and each
group requires its own subcommand; a refusal prints `error: ...` to standard error and
exits with code 2 (`chips.py:1393-1398`).

`build`: build one chip from a JSON payload (`chips.py:1252-1254`)

| Flag | Required | Meaning |
| --- | --- | --- |
| `--from` (dest `source`) | yes | JSON file holding the chip fields |
| `--out` (dest `out`) | yes | where to write the `.chip` file |

It prints the written path and then the `manifest_sha256` of the pack's
`DomainPack.identity()`, not the hash of the whole `manifest.json` member
(`chips.py:1374-1375`, `packs.py:21-24`). The payload may contain `pack_id`, `version`,
`title`, `instructions`, `required_context`, `checks`, `allowed_tools`, `implementation`,
`tagline`, `color`, `glyph`, `presentation` and `art_png`; any other field is refused
(`BUILD_FIELDS`, `chips.py:105-107`, `chips.py:596-599`). `art_png` may be PNG bytes or
base64 text carrying PNG bytes (`chips.py:1278-1295`, `chips.py:623-635`).

`export`: write the built-in packs as `.chip` files (`chips.py:1255-1257`)

| Flag | Required | Meaning |
| --- | --- | --- |
| `--dest` | no | output directory, default `chips/builtin/` |

It prints each path and the `manifest_sha256` of that chip's whole `manifest.json`
(`app/chips.py:main`, `app/chips.py:manifest_sha256`).

`record show`: print the record a chip carries as JSON (`chips.py:1259-1261`)

| Flag | Required | Meaning |
| --- | --- | --- |
| `--chip` | yes | the `.chip` file to read |

`record put`: replace the record inside a chip file (`chips.py:1262-1266`)

| Flag | Required | Meaning |
| --- | --- | --- |
| `--chip` | yes | the `.chip` file to rewrite |
| `--from` (dest `source`) | yes | JSON file holding the record fields |
| `--out` | no | write the result here instead of rewriting `--chip` in place |

`conversation show`: print the conversation a chip carries (`chips.py:1268-1270`)

| Flag | Required | Meaning |
| --- | --- | --- |
| `--chip` | yes | the `.chip` file to read |

`conversation put`: replace the conversation inside a chip file (`chips.py:1271-1275`)

| Flag | Required | Meaning |
| --- | --- | --- |
| `--chip` | yes | the `.chip` file to rewrite |
| `--from` (dest `source`) | yes | text file holding the conversation |
| `--out` | no | write the result here instead of rewriting `--chip` in place |

The CLI works on chip files as files. It does not touch `chips/installed/` and does not
consult the built-in registry: `record put` and `conversation put` rewrite the archive
they are given, keeping the manifest, art and the other member
(`chips.py:1330-1365`).

---

## 8. HTTP routes

Every route below is defined in the chip block of `current/rehearsal_build/app/api.py`.
Errors are mapped by `chipguard`: `Invalid` becomes 400
`invalid_chip`, a `Conflict` becomes 409 `conflict`, and a `KeyError` becomes 404
`not_found` with the key as the message (`api.py:556-565`).

| Method and path | Returns or accepts | Built-in refusal |
| --- | --- | --- |
| `GET /chips` | listing `{"chips": [...]}` of built-ins plus every readable installed chip (`chips.py:961-965`) | none |
| `GET /chips/{pack_id}` | one chip's row plus `pack`, `format` and `identity`; unknown id is 404 (`chips.py:967-980`) | none |
| `GET /chips/{pack_id}/projects` | projects registered under that chip, newest first, with question counts; unknown id is 404 (`harness.py:265-286`) | none |
| `POST /chips/{pack_id}/workspace` | no body required; opens the sole linked project or creates an empty notebook, returning `{project_id, name, kind, pack_id, created}`; reopening returns `created: false`, unknown chips return 404 and multiple linked projects return 409 (`app/harness.py:open_chip_workspace`) | none; built-in conversation writes remain refused |
| `GET /chips/{pack_id}/record` | the record object; a chip without `record.json` reads as the empty record (`chips.py:1115-1128`) | none (reads the built-in sample file if exported) |
| `PUT /chips/{pack_id}/record` | body `{"record": {...}}`; only that one field is accepted (`api.py:594-610`) | refused, `{pack_id} is a built-in chip; built-in chips cannot be modified` (`chips.py:1139-1140`) |
| `GET /chips/{pack_id}/conversation` | `{"pack_id", "text", "bytes"}`; a chip without a log reads as empty text (`chips.py:1164-1179`) | none |
| `PUT /chips/{pack_id}/conversation` | body `{"text": "..."}`; only that one field, and `text` is required (`api.py:619-632`) | refused with the same "cannot be modified" sentence (`chips.py:1190-1192`) |
| `POST /chips/{pack_id}/conversation/compile` | body `{"project_id": "..."}`; composes the log from stored records and writes it into that chip, returning the text, byte count, `model_calls: 0` and `stored: true` (`chip_conversation.py:229-243`) | refused by the store it writes through (`chip_conversation.py:233-238`) |
| `GET /chips/{pack_id}/art` | the PNG bytes with `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`; a chip with no art is 404 (`chips.py:982-994`) | none (built-in art comes from the exported sample file) |
| `POST /chips/install` | body `{"path": "..."}` or `{"base64": "...", "filename": "..."}`, not both; extra fields refused (`api.py:661-677`) | refused at install: `{pack_id} is a built-in chip; built-in chips cannot be replaced or removed` (`chips.py:1016-1017`) |
| `POST /chips/open` | same file payload as install; validates the archive, reopens byte-identical installed files without rewriting, otherwise retains the installer's collision/version checks (`app/chips.py:open_bytes`) | only exact code-produced built-in archive bytes may open; modified built-in archives are refused |
| `POST /chips/build` | a create-form payload; answers `{pack_id, version, filename, base64, manifest_sha256, identity}` and writes nothing to disk (`chips.py:652-668`) | none |
| `POST /chips/export/{pack_id}` | `{"filename", "base64"}` of the chip file (`chips.py:1227-1247`) | not rewritten; a built-in is regenerated from code by `builtin_chip` rather than read from the sample on disk (`chips.py:808-816`, `chips.py:1231-1232`) |
| `POST /chips/art/preview` | a label preview for a payload: `{label_png_base64, text, width, height}` (`chips.py:800-806`) | none |
| `DELETE /chips/{pack_id}` | `{"deleted": pack_id, "source": "file"}`; an id naming no file is 404 (`chips.py:1093-1113`) | refused, `{pack_id} is a built-in chip; built-in chips cannot be deleted` |

The export route auto-saves the conversation before handing out bytes
(`api.py:683-705`). For a chip that is not built-in it first calls
`chip_conversation.store_on_export`, which finds every project whose `pack_id` matches,
composes the log and writes it into the chip (`chip_conversation.py:245-266`,
`api.py:695-703`). Because one chip file holds one `conversation.txt`, the write happens
only when exactly one project works under that chip. With two or more projects nothing is
written and the export is refused with:

```
{len(conversations)} projects are working under {pack_id}, and the chip holds one conversation.txt. Export it from a project that has its own chip, or name the project whose log this file should carry
```

The reason is stated in the route comment: the file leaves the app, and whatever it does
not carry cannot be recovered from inside it (`api.py:686-693`).

---

## 9. Built-in chips

Two packs ship with the app and are built into the code, not read from disk:
`social_science` and `engineering_measurement`, version `1.0.0`, defined in
`current/rehearsal_build/app/packs.py:26-34` and collected into
`PACKS = {p.pack_id: p for p in (SOCIAL, ENGINEERING)}` (`packs.py:36`). Their
presentation values are also in code: colour `#2f5d8a` with glyph `Σ` for
`social_science`, colour `#8a5a2f` with glyph `Ω` for `engineering_measurement`, each with
its own tagline (`chips.py:90-98`).

Where their record comes from: the built-in listing is derived from `app.packs`, never
from files on disk (`chips.py:882-896`), so the two chips stay listed even when their
exported sample files do not exist. A record or conversation read for a built-in id goes
to the sample file `chips/builtin/<pack_id>.chip`; when that file is absent the read
returns the empty record or empty text (`chips.py:1115-1128`, `chips.py:1215-1225`,
`chips.py:1164-1179`). `python -m app.chips export` writes those sample files
(`chips.py:819-829`).

What this means for a reader: a built-in id names a shipped sample, so it is refused
wherever a *stored* chip is required. It cannot be replaced or installed over
(`chips.py:1016-1017`), its record and conversation cannot be written
(`chips.py:1139-1140`, `chips.py:1190-1192`), it cannot be deleted
(`chips.py:1096-1097`), and `store_on_export` never rewrites it, so an export of a
built-in is produced by `builtin_chip` from the pack definition and the built-in
presentation values, not read from the sample file on disk (`api.py:683-693`,
`chips.py:808-816`). `ChipStore._scan` also skips
any installed file whose `pack_id` collides with a built-in (`chips.py:916-941`), and
`load_installed_packs` keeps the built-in definition in that case
(`chips.py:831-846`).

---

## 10. Common mistakes

Each item names the rule that causes the refusal.

1. **Zipping a folder and renaming it.** A folder entry is stored in the ZIP as a name
   ending in `/`, which is refused as `A chip may not contain directories`
   (`chips.py:220-221`). Build the archive from the four member files, or use
   `python -m app.chips build`.
2. **Adding a `README` or any extra member.** Only the four names are accepted; anything
   else is `Unknown chip member ...` (`chips.py:225-227`).
3. **Nesting the members in a subfolder.** `sub/manifest.json` is not one of the four
   names, so the chip is refused as an unknown member (`chips.py:225-227`). That refusal
   comes first, so the archive never reaches the separate missing-`manifest.json` check
   (`chips.py:328-329`); the message a person sees names the unknown member.
4. **Sending an encrypted ZIP.** `An encrypted chip member is refused; chips are never
   encrypted` (`chips.py:317-318`).
5. **Using bzip2 or another compression method.** Only stored and deflate are accepted
   (`chips.py:319-320`).
6. **Typing `"format": "1"` or `"format": true`.** `format` is the integer literal `1`;
   anything else is refused before model validation (`chips.py:249-251`).
7. **Adding a field the model does not know.** `extra="forbid"` on the manifest, the pack,
   the presentation and every record entry means an extra key is a refusal, not an
   ignored value (`chips.py:138`, `packs.py:11`, `chips.py:125`, `chips.py:152`).
8. **Writing `"color": "2f5d8a"` or a three-digit hex.** The value must match
   `^#[0-9a-fA-F]{6}$`, otherwise `color must be a "#rrggbb" hex color, for example
   "#2f5d8a"` (`chips.py:84`, `chips.py:129-134`).
9. **Using a three-character glyph.** The glyph cap is two characters
   (`chips.py:103`, `chips.py:127`).
10. **Trying to replace a built-in.** `social_science` and `engineering_measurement` are
    refused on install, on record write, on conversation write and on delete
    (`chips.py:1016-1017`, `chips.py:1139-1140`, `chips.py:1190-1192`,
    `chips.py:1096-1097`). Use a new `pack_id`.
11. **Reinstalling the same version.** An installed chip with the same `pack_id` and the
    same `version` is refused with `{pack_id} version {version} is already installed;
    remove it first, or install a chip with a different version`
    (`chips.py:1023-1025`).
12. **Installing over a chip whose record cannot be read.** Refused, so unreadable bytes
    are not destroyed; export or delete that chip first (`chips.py:1026-1032`).
13. **Assuming a `chip.png` that is not a PNG will be ignored.** It is refused as
    `chip.png is not a PNG image`; the member must carry the PNG signature and the `IEND`
    trailer (`chips.py:335-336`, `chips.py:368-369`).
14. **Pointing `POST /chips/install` at a file outside the allowed roots.** The path must
    resolve inside the data folder, the app folder, the home folder or the temp folder
    (`chips.py:907-914`, `chips.py:1065-1066`).
15. **Expecting an empty conversation write to leave an empty member.** Empty or
    whitespace-only text removes `conversation.txt` instead of storing a blank log
    (`chips.py:1205-1208`, `chips.py:537-539`).
16. **Expecting a hand-written record with only empty entries to survive a rewrite.**
    A record with no non-default entry is treated as no record and is not written
    (`chips.py:478-488`).
17. **Expecting many projects under one chip to export with all their logs.** One file
    holds one log; with two or more projects the export is refused rather than picking one
    (`api.py:694-702`, `chip_conversation.py:245-266`).
