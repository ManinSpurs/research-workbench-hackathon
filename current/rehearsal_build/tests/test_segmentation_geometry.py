"""Geometry validation for segmentation boxes. Offline: no network, no image decode."""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai.segmentation_geometry import validate_box, validate_objects  # noqa: E402

W, H = 640, 480


def test_clean_box_in_bounds():
    out = validate_box({'x1': 10, 'y1': 20, 'x2': 100, 'y2': 200}, W, H)
    assert out == {'in_bounds': True, 'clipped_box': {'x1': 10, 'y1': 20, 'x2': 100, 'y2': 200},
                   'degenerate': False, 'out_of_range': False}


def test_grounding_record_box_checked_against_source_dims():
    rec = {'frame_index': 0, 'object_id': 1, 'box': {'x1': 0, 'y1': 0, 'x2': W, 'y2': H, 'w': 9999, 'h': 9999}}
    out = validate_objects([rec], W, H)
    assert len(out) == 1 and out[0]['in_bounds'] is True
    assert out[0]['clipped_box'] == {'x1': 0, 'y1': 0, 'x2': W, 'y2': H}


def test_box_clipped_not_trusted_frame_size():
    out = validate_box({'x1': 600, 'y1': 400, 'x2': 900, 'y2': 700}, W, H)
    assert out['in_bounds'] is False and out['out_of_range'] is True
    assert out['clipped_box'] == {'x1': 600, 'y1': 400, 'x2': W, 'y2': H}


def test_negative_origin_clipped():
    out = validate_box({'x1': -5, 'y1': -5, 'x2': 10, 'y2': 10}, W, H)
    assert out['in_bounds'] is False and out['out_of_range'] is True
    assert out['clipped_box'] == {'x1': 0, 'y1': 0, 'x2': 10, 'y2': 10}


def test_nan_and_inf_rejected():
    for bad in (float('nan'), float('inf'), -float('inf')):
        out = validate_box({'x1': bad, 'y1': 0, 'x2': 10, 'y2': 10}, W, H)
        assert out == {'in_bounds': False, 'clipped_box': None, 'degenerate': True, 'out_of_range': True}


def test_zero_area_degenerate():
    out = validate_box({'x1': 50, 'y1': 50, 'x2': 50, 'y2': 90}, W, H)
    assert out['in_bounds'] is False and out['degenerate'] is True and out['out_of_range'] is False
    assert out['clipped_box'] == {'x1': 50, 'y1': 50, 'x2': 50, 'y2': 90}


def test_zero_height_degenerate():
    out = validate_box({'x1': 10, 'y1': 20, 'x2': 60, 'y2': 20}, W, H)
    assert out['in_bounds'] is False and out['degenerate'] is True


def test_inverted_corners_rejected():
    out = validate_box({'x1': 100, 'y1': 20, 'x2': 10, 'y2': 200}, W, H)
    assert out == {'in_bounds': False, 'clipped_box': None, 'degenerate': True, 'out_of_range': True}
    out = validate_box({'x1': 10, 'y1': 200, 'x2': 100, 'y2': 20}, W, H)
    assert out['clipped_box'] is None and out['in_bounds'] is False


def test_missing_and_non_dict_rejected():
    for bad in (None, 'box', {'x1': 1, 'y1': 2}, {'x1': 0, 'y1': 0, 'x2': 'a', 'y2': 1},
                {'x1': True, 'y1': 0, 'x2': 1, 'y2': 1}):
        out = validate_box(bad, W, H)
        assert out['in_bounds'] is False and out['clipped_box'] is None and out['out_of_range'] is True


def test_validate_objects_order_and_mixed():
    objs = [{'box': {'x1': 1, 'y1': 1, 'x2': 5, 'y2': 5}},
            {'box': {'x1': -1, 'y1': -1, 'x2': 5, 'y2': 5}},
            {'box': {'x1': 9, 'y1': 9, 'x2': 1, 'y2': 1}}]
    out = validate_objects(objs, W, H)
    assert [o['in_bounds'] for o in out] == [True, False, False]
    assert out[2]['clipped_box'] is None


def test_validate_objects_never_invents_masks():
    out = validate_objects([{'box': {'x1': 1, 'y1': 1, 'x2': 5, 'y2': 5}, 'mask': None}], W, H)
    assert 'mask' not in out[0] and set(out[0]) == {'in_bounds', 'clipped_box', 'degenerate', 'out_of_range'}


def test_bad_source_dims_raise():
    for bad_w, bad_h in ((0, H), (W, 0), (-1, H), (float('nan'), H), (W, float('inf')), (True, H)):
        try:
            validate_objects([], bad_w, bad_h)
        except ValueError:
            pass
        else:
            raise AssertionError('expected ValueError for dims %r x %r' % (bad_w, bad_h))
    try:
        validate_objects('not-a-list', W, H)
    except ValueError:
        pass
    else:
        raise AssertionError('expected ValueError for non-list objects')


def test_math_constants_rejected_as_non_finite():
    out = validate_box({'x1': math.inf, 'y1': 0, 'x2': 1, 'y2': 1}, W, H)
    assert out['clipped_box'] is None and out['in_bounds'] is False
