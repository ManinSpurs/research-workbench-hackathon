"""Response-declared segmentation source dimensions. Mock transport only, no network.

Covers: PNG/JPEG fixtures generated in-test register as real decodable
images (input validation); the detections-JSON response declares
image_dimensions 4x3, and validate_objects runs against those RESPONSE dims
(origin 'response_image_dimensions') — a box outside the declared 4x3 frame
is rejected/clipped, a box inside passes; the absent-image path emits no
fabricated fields. Masks are never decoded (NOT_IMPLEMENTED retained).

NOTE: the original "server_decoded local-byte dimensions" intent is
obsolete — the live path no longer decodes local image bytes (see the
RETIRED helpers in app/ai/service.py, kept only for compat). The honest
new-contract equivalent asserted here is response-declared dimensions.
"""
import asyncio
import base64
import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from app.ai import registry, service
from app.ai.provider import MetaProvider
from app.ai.service import _apply_server_source_dimensions, _decode_image_dimensions
from test_dot_safety import h  # noqa: F401  (shared loopback-guarded harness fixture)


def _tiny_png(width, height):
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)

    def chunk(typ, data):
        return (struct.pack('>I', len(data)) + typ + data
                + struct.pack('>I', zlib.crc32(typ + data) & 0xFFFFFFFF))

    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IEND', b'')


def _tiny_jpeg(width, height):
    return (b'\xff\xd8\xff\xc0\x00\x0b\x08' + struct.pack('>HH', height, width)
            + b'\x01\x01\x11\x00\xff\xd9')


def _detections_payload():
    # First box lies outside the response-declared 4x3 frame; the second is
    # inside both the declared frame and any 4x3 source. Bboxes are
    # top-left+size per the documented contract (x2/y2 = x/y + w/h).
    return {'image_dimensions': {'width': 4, 'height': 3},
            'detections': [{'id': 'det-1', 'object_type': 'building',
                            'bbox': {'x': 5, 'y': 1, 'width': 4, 'height': 7},
                            'confidence': 0.9, 'source_prompt_type': 'text'},
                           {'id': 'det-2', 'object_type': 'building',
                            'bbox': {'x': 1, 'y': 1, 'width': 1, 'height': 1},
                            'confidence': 0.8, 'source_prompt_type': 'text'}]}


def _handler():
    def handle(req):
        if req.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': m} for m in registry.MODELS if registry.MODELS[m].endpoint]})
        return httpx.Response(200, json=_detections_payload())

    return handle


def _svc(h, monkeypatch, raw_image, name, mime):
    monkeypatch.setattr(service.secrets, 'key', lambda: 'test-not-real-key')
    svc = service.AIService(h, MetaProvider(httpx.MockTransport(_handler()),
                                            key_reader=lambda: 'test-not-real-key'))
    pid = h.register_project('fx')['project_id']
    qid = asyncio.run(h.create_question(pid, 'What does est_cost measure?'))['question_id']
    svc.configure(True, 150)
    asyncio.run(svc.verify_account())
    asset = svc.register_asset(qid, {'name': name, 'mime': mime,
                                     'encoded': base64.b64encode(raw_image).decode()})
    prep = svc.prepare(qid, {'task': 'segment', 'assets': [asset['id']], 'text': 'building'})
    result = asyncio.run(svc.execute(prep['job_id'], prep['preview_hash'], True))
    return result['outputs']


def test_png_source_dimensions_attached_and_truthful(h, monkeypatch):
    out = _svc(h, monkeypatch, _tiny_png(4, 3), 'scene.png', 'image/png')
    assert len(out['objects']) == 2
    outside, inside = out['objects']
    for obj in (outside, inside):
        assert obj['source_image_width'] == 4
        assert obj['source_image_height'] == 3
        assert obj['source_dimensions_origin'] == 'response_image_dimensions'
        assert set(obj['geometry']) == {'in_bounds', 'clipped_box', 'degenerate', 'out_of_range'}
        # The grounding record's NOT_IMPLEMENTED marker string is retained;
        # no raster is ever decoded.
        assert obj['mask_raster_decode'] == 'NOT_IMPLEMENTED'
    # Outside the response-declared 4x3 frame (corners 5,1 -> 9,8).
    assert outside['geometry']['in_bounds'] is False
    assert outside['geometry']['out_of_range'] is True
    assert outside['geometry']['clipped_box'] == {'x1': 4, 'y1': 1, 'x2': 4, 'y2': 3}
    # Inside the declared frame: check passes.
    assert inside['geometry'] == {'in_bounds': True,
                                  'clipped_box': {'x1': 1, 'y1': 1, 'x2': 2, 'y2': 2},
                                  'degenerate': False, 'out_of_range': False}
    assert inside['box'] == {'x1': 1, 'y1': 1, 'x2': 2, 'y2': 2, 'w': 4, 'h': 3}
    assert out['source_asset']['sha256']
    assert out['kind'] == 'segmentation_geometry_not_generated_video'


def test_jpeg_source_dimensions_attached(h, monkeypatch):
    out = _svc(h, monkeypatch, _tiny_jpeg(4, 3), 'scene.jpg', 'image/jpeg')
    outside = out['objects'][0]
    assert (outside['source_image_width'], outside['source_image_height']) == (4, 3)
    assert outside['source_dimensions_origin'] == 'response_image_dimensions'
    assert outside['geometry']['in_bounds'] is False
    assert outside['geometry']['clipped_box'] == {'x1': 4, 'y1': 1, 'x2': 4, 'y2': 3}


def test_header_decoder_unit_shapes():
    # The live segmentation path no longer calls these retired local-decode
    # helpers (dims come from the response's image_dimensions). This test
    # pins only their pass-through contract — no fabricated fields when
    # bytes are missing/undecodable — without resurrecting server-dimensions
    # behavior as live logic.
    assert _decode_image_dimensions(_tiny_png(4, 3), 'image/png') == (4, 3)
    assert _decode_image_dimensions(_tiny_jpeg(4, 3), 'image/jpeg') == (4, 3)
    gif = b'GIF89a' + struct.pack('<HH', 2, 2) + b'\x00' * 10
    assert _decode_image_dimensions(gif, 'image/gif') == (2, 2)
    assert _decode_image_dimensions(b'not-an-image', 'image/png') == (None, None)
    assert _decode_image_dimensions(b'', 'image/png') == (None, None)
    assert _decode_image_dimensions(None, 'image/png') == (None, None)


def test_absent_image_path_emits_no_fabricated_dims():
    # Pins the retired helper's pass-through contract only (see note
    # above): objects pass through untouched when bytes are missing or
    # undecodable. This does not rewire the live segmentation path, which
    # uses response image_dimensions exclusively.
    objects = [{'frame_index': 0, 'object_id': 1,
                'box': {'x1': 1, 'y1': 1, 'x2': 2, 'y2': 2, 'w': 10, 'h': 10}}]
    # Corrupt base64: no bytes available server-side.
    out = _apply_server_source_dimensions(
        [dict(objects[0])], {'id': 'a', 'meta': {'kind': 'image', 'mime': 'image/png'},
                             'encoded': '!!!not-base64!!!'})
    assert out == objects and 'source_image_width' not in out[0]
    # Valid base64 but undecodable as an image.
    out = _apply_server_source_dimensions(
        [dict(objects[0])], {'id': 'a', 'meta': {'kind': 'image', 'mime': 'image/png'},
                             'encoded': base64.b64encode(b'not-an-image').decode()})
    assert out == objects and 'source_dimensions_origin' not in out[0]
    # Missing encoded payload entirely.
    out = _apply_server_source_dimensions(
        [dict(objects[0])], {'id': 'a', 'meta': {'kind': 'image', 'mime': 'image/png'}})
    assert out == objects and 'geometry' not in out[0]
    # Non-image asset: untouched.
    out = _apply_server_source_dimensions(
        [dict(objects[0])], {'id': 'a', 'meta': {'kind': 'text'}, 'encoded': 'eA=='})
    assert out == objects
