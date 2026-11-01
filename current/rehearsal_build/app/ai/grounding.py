"""Structural provenance validation, explicitly separate from semantic correctness."""
import json
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from . import segmentation_geometry
from .. import release_scope

class Claim(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    text:str=Field(min_length=1,max_length=4000)
    evidence_ids:list[str]=Field(max_length=20)
    kind:Literal["fact","interpretation","proposal"]
class ProposedVariable(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    name:str
    dataset_id:str|None
    kind:Literal["actual","derived","unverified"]
class ResearchResult(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    answer:str=Field(max_length=16000)
    claims:list[Claim]=Field(max_length=30)
    unresolved:list[str]=Field(max_length=30)
    proposed_variables:list[ProposedVariable]=Field(max_length=20)
    next_action:str

def validate(text,packet,assets):
    result=ResearchResult.model_validate_json(text)
    source_by_id={e['evidence_id']:e for e in packet['sources']}
    observed={e['evidence_id'] for e in packet['sources']}|{a['id'] for a in assets}
    errors=[];total=0;linked=0
    # Release-year applicability uses the same shared rule as the legacy
    # harness path, so both read a note's declared release identically (the
    # optional trailing 'only' and letter case make no difference).
    question_release=release_scope.question_release(packet.get('release'))
    scope_states={};mismatched=set()
    catalog={d['dataset_id']:set(d['columns']) for d in packet['datasets']}
    for c in result.claims:
        if c.kind not in ('fact','interpretation','proposal'):errors.append('Unknown claim kind')
        for eid in c.evidence_ids:
            total+=1
            if eid not in observed:errors.append('Unobserved evidence ID: '+eid)
            else:
                linked+=1
                source=source_by_id.get(eid)
                if source:
                    scope=release_scope.year_applicability(source.get('notes',''),packet.get('release'))
                    scope_states[eid]=scope['status']
                    if scope['status']==release_scope.MISMATCH:
                        # A note scoped to another release is a scope warning
                        # only: it never decides whether the claim's meaning is
                        # true, and legitimate counterexamples remain possible.
                        mismatched.add(eid)
                        errors.append('Cited source explicitly targets another release')
        if c.kind=='fact' and not c.evidence_ids:errors.append('Factual claim lacks evidence linkage')
    for v in result.proposed_variables:
        if v.kind=='actual' and v.name not in catalog.get(v.dataset_id,set()):errors.append('Actual variable not present in selected dataset')
    # Aggregate year applicability. A missing/unusable question release is
    # reported as NOT_CHECKED, never as a pass: no comparison was made.
    if question_release is None:applicability=release_scope.NOT_CHECKED
    elif mismatched:applicability=release_scope.MISMATCH
    elif any(s==release_scope.MATCH for s in scope_states.values()):applicability=release_scope.MATCH
    else:applicability=release_scope.UNDECLARED
    return result.model_dump(),{'structural_valid':not errors,'errors':errors,'citation_count':total,
        'invalid_citation_count':total-linked,'citation_error_rate':(total-linked)/total if total else None,
        'linked_claim_fraction':sum(bool(c.evidence_ids) for c in result.claims)/len(result.claims) if result.claims else None,
        'release_year_applicability':{'status':applicability,'question_release':question_release,
                                      'mismatched_evidence_ids':sorted(mismatched)},
        'semantic_correctness':'NOT_SCORED','false_citation_rate':'HUMAN_REVIEW_REQUIRED'}

#: Documented SAM 3.1 interactive-segmentation detection fields carried
#: through honestly (Meta Model API docs, 2026-10-26). Confidence IS returned
#: on this endpoint (unlike object-presence grounding). Geometries/masks are
#: carried only when the payload provides them; raster mask decode stays
#: NOT_IMPLEMENTED.
def _detection_box(det):
    """Convert one documented bbox {x, y, width, height} (top-left origin +
    size) into the internal corner geometry {x1, y1, x2, y2, w, h}, where
    w/h are the response's image_dimensions. Returns None for a missing,
    non-finite, boolean, or negative-dimension box. Zero-area boxes convert
    (downstream geometry validation marks them degenerate); x2/y2 follow
    from x/y + width/height exactly (no rounding, no clamping here)."""
    if not isinstance(det,dict):return None
    box=det.get('bbox')
    if not isinstance(box,dict):return None
    dims=det.get('_image_dims')
    if not (isinstance(dims,tuple) and len(dims)==2):return None
    w,h=dims
    vals=box.get('x'),box.get('y'),box.get('width'),box.get('height')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in vals):return None
    import math
    if any(not math.isfinite(v) for v in vals):return None
    x,y,bw,bh=vals
    if bw<0 or bh<0:return None
    return {'x1':x,'y1':y,'x2':x+bw,'y2':y+bh,'w':w,'h':h}


def detections(response,source_asset=None):
    """Map the documented interactive-segmentation detections payload.

    Expected shape: top-level dict with ``image_dimensions`` {width, height}
    and ``detections``[]; each detection carries ``id``, ``object_type``,
    ``bbox`` {x, y, width, height} (top-left + size), ``confidence`` and
    ``source_prompt_type``, plus optional ``geometries`` polygon rings.
    Server dimensions come from the response's ``image_dimensions``; each
    corner box is checked with ``segmentation_geometry.validate_objects``
    against those true dimensions (same semantics as before: boxes only,
    masks never decoded, NOT_IMPLEMENTED retained). Returns a list of
    objects on success (possibly empty for zero detections). Raises
    ValueError — failing closed, never an invented box — on a non-dict
    response, missing/non-positive image dimensions, a missing/non-list
    detections array, or any malformed detection/bbox entry.
    """
    if not isinstance(response,dict):raise ValueError('Segmentation response is not a detections payload')
    dims=response.get('image_dimensions')
    if not isinstance(dims,dict):raise ValueError('Segmentation response has no image_dimensions')
    width,height=dims.get('width'),dims.get('height')
    if isinstance(width,bool) or isinstance(height,bool) or not isinstance(width,(int,float)) or not isinstance(height,(int,float)):raise ValueError('Segmentation image_dimensions are not numeric')
    import math
    if not math.isfinite(width) or not math.isfinite(height) or width<=0 or height<=0:raise ValueError('Segmentation image_dimensions are not positive finite numbers')
    dets=response.get('detections')
    if not isinstance(dets,list):raise ValueError('Segmentation response has no detections array')
    records=[]
    for det in dets:
        if not isinstance(det,dict):raise ValueError('Segmentation detection is not an object')
        det=dict(det);det['_image_dims']=(width,height)
        box=_detection_box(det)
        if box is None:raise ValueError('Segmentation detection has a malformed bbox')
        entry={'box':box}
        for key in ('id','object_type','confidence','source_prompt_type'):
            if key in det:entry[key]=det[key]
        conf=entry.get('confidence')
        if 'confidence' in entry and (isinstance(conf,bool) or not isinstance(conf,(int,float)) or not math.isfinite(conf)):raise ValueError('Segmentation detection has non-finite confidence')
        if 'geometries' in det and det['geometries'] is not None:
            if not isinstance(det['geometries'],list):raise ValueError('Segmentation detection geometries are not a list')
            entry['geometries']=det['geometries']
        entry['mask_raster_decode']='NOT_IMPLEMENTED'
        entry['units']='source_pixels'
        entry['source_dimensions_origin']='response_image_dimensions'
        records.append(entry)
    annotated=segmentation_geometry.validate_objects(records,width,height)
    out=[]
    for rec,check in zip(records,annotated):
        rec['source_image_width']=width;rec['source_image_height']=height
        rec['geometry']=check;out.append(rec)
    return out


def segmentation(text):
    """RETIRED live path: invented streamed token grammar.

    The ``<Nf>K<|box;...|><|mask;...|>`` format matched neither documented
    contract (interactive segmentation uses POST /segmentation/interactive
    returning a detections JSON payload; object-presence grounding uses
    /chat/completions with {"x","y","w","h"} boxes) and is no longer the
    live path — ``detections()`` above is. Kept only so pre-existing
    references outside the owned files fail loudly rather than silently;
    new code must not call it.
    """
    raise ValueError('Retired SAM token grammar: use grounding.detections on the documented detections payload')
