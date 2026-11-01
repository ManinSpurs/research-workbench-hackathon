# Research canvas contract

## Authority and representation

The canvas is a researcher-authored view over a pinned question and selected dataset catalog. It is not a generated image, model rationale, verified causal graph or automatic scientific conclusion. Core scope and deletion rules apply equally to the canvas and its previous draft.

Registered column existence is checked by ordinary code. Meaning, temporal order, causal direction, unit compatibility and source applicability remain NOT_VERIFIED. Missing linkage does not prove absent data. Do not silently map approval_date to final approval or a permit issuance date to construction start. Users explicitly select mappings and write their assumptions.

The existing model-design panel remains labeled as a proposal. Saving a canvas is not accepting that proposal, changing the estimand or recording an adopt/defer/reject decision. The original question is always displayed above the canvas.

## API and storage

GET `/questions/{qid}/canvas` returns the pinned pack context, original question, selected dataset columns, current revision, enriched nodes, previous saved draft and input fingerprint. Reading an unsaved canvas returns revision zero without inserting a database row.

PUT `/questions/{qid}/canvas` accepts `expected_revision` and 2 to 8 nodes. Each node has a nonempty label, nullable dataset_id and variable, and an optional note. The UI exposes three social-science slots or five engineering slots. Bound variables must match an actual column in a selected dataset; dataset and variable must be both null or both present. Extra fields cannot declare a node verified.

Use the current revision on each save. A stale save returns conflict and keeps the newer draft intact. The UI retains the unsaved editor content after a failed save; reopen the saved question to reload deliberately. There is no automatic retry that overwrites another edit.

The SQLite research_canvases table stores question_id, revision, body_json and previous_json. Only the immediately previous saved draft is retained. It is not a complete audit log. The table is included in question deletion and session-only cleanup. No external graph database or image store is introduced.

Registered input hashes and pinned pack identity are checked before reads and writes. The current single backend worker performs validation and write without awaiting another task between them. Multiple backend workers remain unsupported, as in the inherited harness.

## Interaction and limitations

Native buttons give keyboard access to nodes, and SVG supplies connectors and linkage dots. Click a node to inspect the registered source excerpt and highlight the file-specific profile row. Dynamic text is escaped before HTML insertion. Source instructions remain data.

Question entry, link editor, previous-draft comparison and saved questions use expandable sections. Switching packs clears the active canvas view and reloads the chosen pack's remembered work. It does not rebind an existing question or reset model allowances.

No calendar-scaled axis, elapsed-time computation, automated variable mapping, arbitrary node creation, model-generated layout or image analysis is implemented. Engineering nodes represent measurement components, not a time sequence. General literature maps and other research features remain roadmap items.
