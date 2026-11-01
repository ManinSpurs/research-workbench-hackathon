"""Pure geometry validation for segmentation boxes. Offline, no I/O, no masks.

Coordinate convention (matches grounding.segmentation in app/ai/grounding.py):
  boxes are absolute source-pixel coordinates with keys
  x1, y1, x2, y2 plus the model-declared frame size w, h, where the parser
  already required 0 <= x1 <= x2 <= w and 0 <= y1 <= y2 <= h. x1/y1 is the
  top-left corner, x2/y2 the bottom-right corner, units are source pixels
  (not physical area).

This module re-checks that geometry against the caller-supplied source image
width/height (e.g. decoded source image dimensions). The inline w/h carried
by each box is the model's own frame claim and is NOT trusted here: the
authoritative reference is the width/height passed in. Masks are never
decoded, generated or invented here; raster mask decoding stays
NOT_IMPLEMENTED.
"""
from __future__ import annotations
import math

_BOX_KEYS = ('x1', 'y1', 'x2', 'y2')


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clip(value, lo, hi):
    return max(lo, min(value, hi))


def _check_dims(width, height):
    for name, dim in (('width', width), ('height', height)):
        if not _finite_number(dim) or dim <= 0:
            raise ValueError('Source image %s must be a positive finite number' % name)


def validate_box(box, width, height):
    """Check one box dict against source image dimensions.

    Returns exactly {'in_bounds', 'clipped_box', 'degenerate',
    'out_of_range'} where clipped_box holds {x1, y1, x2, y2} normalized to
    source-pixel coordinates clipped into [0, width] x [0, height], or None
    when the input is missing, non-finite or inverted (nothing sane to clip).
    in_bounds is True only for finite, correctly ordered, non-degenerate
    boxes fully inside the source frame.
    """
    _check_dims(width, height)
    if (not isinstance(box, dict) or any(k not in box for k in _BOX_KEYS)
            or any(not _finite_number(box[k]) for k in _BOX_KEYS)):
        return {'in_bounds': False, 'clipped_box': None, 'degenerate': True, 'out_of_range': True}
    x1, y1, x2, y2 = (box['x1'], box['y1'], box['x2'], box['y2'])
    if x2 < x1 or y2 < y1:
        # Inverted corners: ordering is invalid, so no clipped box is offered.
        return {'in_bounds': False, 'clipped_box': None, 'degenerate': True, 'out_of_range': True}
    degenerate = (x2 == x1) or (y2 == y1)
    out_of_range = x1 < 0 or y1 < 0 or x2 > width or y2 > height
    clipped = {'x1': _clip(x1, 0, width), 'y1': _clip(y1, 0, height),
               'x2': _clip(x2, 0, width), 'y2': _clip(y2, 0, height)}
    return {'in_bounds': (not out_of_range) and (not degenerate),
            'clipped_box': clipped, 'degenerate': degenerate, 'out_of_range': out_of_range}


def validate_objects(objects, width, height):
    """Check a list of segmentation objects against source image dimensions.

    Each item may be a grounding.segmentation record (a dict carrying a
    'box' dict) or a bare box dict {x1, y1, x2, y2}. Returns one
    validate_box-style dict per item, in order. Raises ValueError for a
    non-list input or non-positive/non-finite image dimensions.
    """
    _check_dims(width, height)
    if not isinstance(objects, list):
        raise ValueError('objects must be a list')
    results = []
    for obj in objects:
        box = obj.get('box') if isinstance(obj, dict) else None
        if box is None and isinstance(obj, dict) and all(k in obj for k in _BOX_KEYS):
            box = obj
        results.append(validate_box(box, width, height))
    return results
