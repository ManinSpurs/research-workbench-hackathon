"""Dataset and matched-evaluation contracts; all model outputs are scripted."""
import asyncio
import json

import pytest

from app import compare
from app.harness import Conflict
from app.results import VariableRef, RefinedQuestion
from test_dot_safety import h, StubAdapter


@pytest.mark.parametrize("dataset,name,selected,valid", [
    ("nyc/dobnow_2024_subset.csv", "filing_date", "nyc/dobnow_2024_subset.csv", False),
    ("nyc/bis_2024_subset.csv", "filing_date", "nyc/dobnow_2024_subset.csv", False),
    (None, "filing_date", "nyc/bis_2024_subset.csv", False),
    ("not-registered.csv", "filing_date", "nyc/bis_2024_subset.csv", False),
    ("nyc/dobnow_2024_subset.csv", "issued_date", "nyc/dobnow_2024_subset.csv", True),
    ("nyc/bis_2024_subset.csv", "filing_date", "nyc/bis_2024_subset.csv", True),
])
def test_actual_variables_are_checked_in_selected_file(h, dataset, name, selected, valid):
    async def scenario():
        pid = h.register_project("nyc")["project_id"]
        q = await h.create_question(pid, "Can I use this interval?", dataset_ids=[selected])
        original = h.adapter.first_step
        async def proposal(*args):
            step, history, usage = await original(*args)
            step.design.variables = [VariableRef(name=name, dataset_id=dataset, kind="actual", role="start")]
            return step, history, usage
        h.adapter.first_step = proposal
        result = await h.run(q["question_id"])
        if valid:
            assert result["result_kind"] == "ClarificationNeeded"
        else:
            assert result["outcome"] == "invalid_variables" and result["design"] is None
            assert h.db.execute("SELECT count(*) FROM designs").fetchone()[0] == 0
            assert h.db.execute("SELECT count(*) FROM clarifications").fetchone()[0] == 0
    asyncio.run(scenario())


def test_dobnow_scope_and_sources_do_not_borrow_bis(h):
    async def scenario():
        pid = h.register_project("nyc")["project_id"]
        q = await h.create_question(pid, "Can I compute filing time using DOB NOW?")
        assert h.question_dataset_ids(q["question_id"]) == ["nyc/dobnow_2024_subset.csv"]
        packet = h.input_packet(pid, h.question_dataset_ids(q["question_id"]))
        assert "filing_date" not in packet["datasets"][0]["columns"]
        assert packet["allowed_sources"] == ["DD-DOBNOW-2018"]
        original = h.adapter.first_step
        async def proposal(*args):
            step, history, usage = await original(*args)
            step.design.variables = [VariableRef(name="issued_date", dataset_id="nyc/dobnow_2024_subset.csv", kind="actual", role="end")]
            return step, history, usage
        h.adapter.first_step = proposal
        result = await h.run(q["question_id"])
        calls = list(h.adapter.calls)
        blocked = await h.respond(result["cq_id"], "use_source", "DD-BIS-2019")
        assert blocked["outcome"] == "blocked" and blocked["cq_status"] == "pending"
        assert h.adapter.calls == calls
    asyncio.run(scenario())


def test_unknown_dataset_rejected_before_model_call(h):
    async def scenario():
        pid = h.register_project("fx")["project_id"]
        with pytest.raises(Conflict):
            await h.create_question(pid, "A question?", dataset_ids=["outside.csv"])
        assert h.adapter.calls == []
    asyncio.run(scenario())


def test_eval_checks_process_and_accepts_both_e09_outcomes():
    e01 = next(c for c in compare.CASES if c["id"] == "E01")
    assert not all(compare.process_checks(e01, "supported", 1).values())
    e09 = next(c for c in compare.CASES if c["id"] == "E09")
    assert all(compare.process_checks(e09, "partial", 0).values())
    assert all(compare.process_checks(e09, "insufficient", 0).values())


def test_eval_inputs_budgets_and_timing_are_independent(monkeypatch, tmp_path):
    packets, basics, created = [], [], []
    class Recorder(StubAdapter):
        async def first_step(self, question, context):
            packets.append(json.loads(context))
            return await super().first_step(question, context)
        async def basic(self, question, materials):
            basics.append((question, json.loads(materials)))
            return await super().basic(question, materials)
    case = {"id": "MATCHED", "kind": "fx", "q": "Can I use the cost field?", "expect": "supported"}
    monkeypatch.setattr(compare, "CASES", [case])
    def factory():
        adapter = Recorder()
        created.append(adapter)
        return adapter
    out = asyncio.run(compare.run_all(str(tmp_path / "paired.db"), repetitions=2, adapter_factory=factory))
    assert out["quality_evaluation"] == "NOT_RUN" and out["accuracy"] is None
    assert len(created) == 4
    assert all(question == case["q"] for question, _ in basics)
    assert packets == [packet for _, packet in basics]
    assert out["cases"][0]["execution_order"] != out["cases"][1]["execution_order"]
    for row in out["cases"]:
        assert row["match"] and row["basic_arm"] == "NOT_SCORED"
        assert row["input_packet_sha256"] == compare.fingerprint(row["input_packet"])
        assert row["database_paths"]["basic"] != row["database_paths"]["harness"]
        h_arm, b_arm = row["arms"]["harness"], row["arms"]["basic"]
        assert h_arm["budget"]["reserved_requests"] > b_arm["budget"]["reserved_requests"]
        assert h_arm["budget"]["limits_json"] == b_arm["budget"]["limits_json"]
        assert h_arm["wall_seconds"] >= h_arm["model_call_seconds"] >= 0
        assert b_arm["wall_seconds"] >= b_arm["model_call_seconds"] >= 0
        assert row["cost_usd"] is None and row["evidence_correctness"] == "NOT_SCORED"


def test_baseline_gets_original_question_without_harness_refinement(monkeypatch, tmp_path):
    seen = []
    class Refiner(StubAdapter):
        async def refine(self, question):
            _, usage = await super().refine(question)
            return RefinedQuestion(refined_text="What is the cost?", kept_constraints=[]), usage
        async def basic(self, question, materials):
            seen.append(question)
            return await super().basic(question, materials)
    case = {"id": "RAW", "kind": "fx", "q": "Could you clarify the cost?", "expect": "refined_question", "refine_only": True}
    monkeypatch.setattr(compare, "CASES", [case])
    out = asyncio.run(compare.run_all(str(tmp_path / "raw.db"), adapter_factory=Refiner))
    assert seen == [case["q"]] and out["cases"][0]["harness_requests"] == 1


def test_harness_exhaustion_does_not_prevent_baseline(monkeypatch, tmp_path):
    class Failing(StubAdapter):
        async def first_step(self, *args):
            raise TimeoutError("scripted failure")
    monkeypatch.setattr(compare, "CASES", [{"id": "FAIL", "kind": "fx", "q": "Can I use cost?", "expect": "supported"}])
    out = asyncio.run(compare.run_all(str(tmp_path / "failure.db"), adapter_factory=Failing))
    row = out["cases"][0]
    assert row["arms"]["harness"]["status"] == "failed"
    assert row["arms"]["basic"]["status"] == "completed" and row["basic_requests"] == 1
    assert not row["match"]


def test_dictionary_word_is_not_a_source_citation(h):
    async def scenario():
        pid = h.register_project("fx")["project_id"]
        q = await h.create_question(pid, "Can I use approval_date?")
        async def wrong(*args):
            _, usage = await StubAdapter().basic(*args)
            return "The dictionary confirms approval_date is construction start.", usage
        h.adapter.basic = wrong
        result = await h.shadow(q["question_id"])
        assert "no source cited" in result["flags"]
        assert "never evidence" in result["note"]
    asyncio.run(scenario())


def test_real_muse_stays_blocked_in_harness_and_eval(h, monkeypatch, tmp_path):
    async def scenario():
        pid = h.register_project("fx")["project_id"]
        h.adapter.label = "muse"
        with pytest.raises(Conflict, match="real Muse is blocked"):
            await h.create_question(pid, "A question?")
        assert h.adapter.calls == []
        def real_label():
            adapter = StubAdapter()
            adapter.label = "muse"
            return adapter
        with pytest.raises(Conflict, match="real Muse is disabled"):
            await compare.run_all(str(tmp_path / "forbidden.db"), adapter_factory=real_label)
    asyncio.run(scenario())
