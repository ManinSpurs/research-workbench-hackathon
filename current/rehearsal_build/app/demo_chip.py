"""Reproducible fictional, mid-progress notebook distributed with review builds."""
from . import chips
from .packs import PACKS


def demo_bytes():
    pack = PACKS["social_science"].model_copy(update={
        "pack_id": "sample_permit_notebook", "title": "Sample: fictional permit research"})
    record = chips.Record.model_validate({
        "questions": [
            {"question_id": "population", "text": "Use residential permits only in the fictional sample?"},
            {"question_id": "measure", "text": "Report processing time descriptively, not as a causal effect?"},
            {"question_id": "waiting", "text": "Please choose whether withdrawn permits belong in the denominator."}],
        "answers": [
            {"question_id": "population", "kind": "adopt",
             "value": "Residential permits match the sample study population; commercial permits are out of scope."},
            {"question_id": "measure", "kind": "adopt",
             "value": "The fictional sample has no identification strategy for causal claims."}],
        "harness": [
            {"key": "Next", "value": "Confirm the denominator before computing summary statistics."}],
    })
    return chips._encode(
        chips.Manifest(format=1, pack=pack,
                       presentation=chips.Presentation(tagline="Fictional sample data, mid-progress",
                                                       color="#2f5d8a", glyph="Σ")),
        None, record,
        "SAMPLE DATA ONLY. This is a fictional research notebook, not empirical evidence.\n"
        "Researcher: Limit the study to residential permits.\n"
        "Secretary: Saved. Processing times will be descriptive, not causal.\n"
        "Secretary: Waiting on you: should withdrawn permits count in the denominator?\n")


if __name__ == "__main__":
    (chips.BUILTIN_DIR / "sample_permit_notebook.chip").write_bytes(demo_bytes())
