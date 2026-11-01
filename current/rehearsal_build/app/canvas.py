"""Deterministic, researcher-authored canvas. No model calls or scientific inference."""
import json
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field

class Node(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: Annotated[str, Field(min_length=1, max_length=100)]
    dataset_id: str | None
    variable: str | None
    note: Annotated[str, Field(max_length=500)] = ""

class Edge(BaseModel):
    """A researcher-drawn relation between two node indexes. Never verified."""
    model_config = ConfigDict(extra="forbid", strict=True)
    from_index: Annotated[int, Field(ge=0)]
    to_index: Annotated[int, Field(ge=0)]
    relation: Annotated[str, Field(min_length=1, max_length=60)]
    note: Annotated[str, Field(max_length=500)] = ""

class CanvasEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: Annotated[int, Field(ge=0)]
    # A mind map needs at least one relationship, so the lower bound stays 2.
    nodes: Annotated[list[Node], Field(min_length=2, max_length=8)]
    # Default empty: canvases saved before edges existed, and PUT bodies without
    # an edges key, keep working.
    edges: Annotated[list[Edge], Field(max_length=16)] = []


RELATION_STATUS = "RESEARCHER_ASSERTED"

NOTICE = ("Links and sequence are researcher-authored assumptions. Linked columns and registered excerpts do not establish meaning, timing, causality, units or applicability. An unmapped node does not prove a variable is absent."
          " A drawn edge is the researcher's own assertion about how two nodes relate. Its relation_status is RESEARCHER_ASSERTED, which records authorship only; no registered evidence is attached to an edge and an edge does not establish a verified relationship, timing, magnitude or causal claim.")


def _stored(body):
    """Normalize a persisted canvas body. Bodies written before edges existed
    have no 'edges' key and must read as an empty list."""
    return {"nodes": body.get("nodes", []), "edges": body.get("edges") or []}


def catalog(h, qid):
    q=h.question(qid)
    h._checkpoint(qid)
    packet=h.input_packet(q["project_id"], h.question_dataset_ids(qid))
    return q,packet


def read(h,qid):
    q,packet=catalog(h,qid)
    pin=h.question_context(qid)
    row=h.db.execute("SELECT * FROM research_canvases WHERE question_id=?",(qid,)).fetchone()
    if row:
        body=_stored(json.loads(row["body_json"]))
        previous=_stored(json.loads(row["previous_json"])) if row["previous_json"] else None
        revision=row["revision"]
    else:
        labels=(["Measured quantity", "Unit", "Test condition", "Instrument", "Uncertainty"]
                if pin["pack_id"]=="engineering_measurement" else ["Start event", "Intermediate event", "Target event"])
        body={"nodes":[{"label":label,"dataset_id":None,"variable":None,"note":""} for label in labels],"edges":[]}
        previous=None; revision=0
    nodes=[]
    for node in body["nodes"]:
        evidence=[e for e in packet["sources"] if e["dataset_id"]==node["dataset_id"] and e["field"]==node["variable"]]
        nodes.append({**node,"status":"column_linked" if node["variable"] else "unmapped",
                      "evidence":evidence,"interpretation_status":"NOT_VERIFIED"})
    # Edges carry no evidence: a drawn relation is asserted by the researcher only.
    edges=[{**edge,"relation_status":RELATION_STATUS} for edge in body["edges"]]
    return {"question_id":qid,"context":pin,"revision":revision,"question":q["original_text"],
            "layout":"measurement_links" if pin["pack_id"]=="engineering_measurement" else "event_sequence",
            "nodes":nodes,"edges":edges,"previous":previous,"datasets":packet["datasets"],
            "input_sha256":packet["registered_bundle_sha256"],"model_calls":0,
            "notice":NOTICE}


def save(h,qid,payload):
    from .harness import Conflict
    try:
        edit=CanvasEdit.model_validate(payload)
    except ValueError as exc:
        raise Conflict("Invalid canvas edit: provide 2 to 8 labeled nodes, at most 16 researcher edges each with a 1 to 60 character relation, and a nonnegative expected_revision") from exc
    q,packet=catalog(h,qid)
    selected={d["dataset_id"]:set(d["columns"]) for d in packet["datasets"]}
    for n in edit.nodes:
        if (n.variable is None)!=(n.dataset_id is None):
            raise Conflict("A linked variable requires both dataset_id and variable")
        if n.variable is not None and n.variable not in selected.get(n.dataset_id,set()):
            raise Conflict("Canvas variable is not an actual column in this question's selected dataset")
    seen=set()
    for edge in edit.edges:
        if edge.from_index==edge.to_index:
            raise Conflict(f"Canvas edge cannot link node {edge.from_index} to itself; choose two different nodes")
        for index in (edge.from_index,edge.to_index):
            if index>=len(edit.nodes):
                raise Conflict(f"Canvas edge refers to node {index} but this canvas has {len(edit.nodes)} nodes; use an index from 0 to {len(edit.nodes)-1} or refresh the canvas")
        triple=(edge.from_index,edge.to_index,edge.relation)
        if triple in seen:
            raise Conflict(f"Duplicate canvas edge from node {edge.from_index} to node {edge.to_index} labeled '{edge.relation}'; change the relation or remove one of them")
        seen.add(triple)
    old=h.db.execute("SELECT * FROM research_canvases WHERE question_id=?",(qid,)).fetchone()
    revision=old["revision"] if old else 0
    if revision!=edit.expected_revision:
        raise Conflict("Canvas changed; reload before saving")
    body=json.dumps({"nodes":[n.model_dump() for n in edit.nodes],
                     "edges":[e.model_dump() for e in edit.edges]},ensure_ascii=False)
    # No await between scope validation and write. One backend worker owns this DB.
    h.db.execute("INSERT INTO research_canvases VALUES(?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET revision=excluded.revision, body_json=excluded.body_json, previous_json=excluded.previous_json",
                 (qid,revision+1,body,old["body_json"] if old else None))
    h.db.commit()
    return read(h,qid)
