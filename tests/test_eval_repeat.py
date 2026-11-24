"""채점 스크립트 반복 측정(--repeat): 같은 질문을 여러 번 돌려 흔들리는 질문과 점수 범위를 본다."""

import json

import pytest

from agent import FakeLLM, LLMError
from eval import run_eval as ev
from tests.test_eval_grader import RIGHT

WRONG_Q1 = ("calculate_completion_rate", {})  # Q1(기한 초과)에 엉뚱한 도구 → 오답


@pytest.fixture(scope="module")
def datasets():
    return ev.load_datasets()


@pytest.fixture
def fake_key(monkeypatch):
    """CLI(main)가 시작 전에 키를 확인하는 경우(#53)에도 가짜 LLM으로 돌 수 있게. 키 확인이 없으면 아무것도 안 함."""
    monkeypatch.setattr(ev, "find_api_key", lambda: "test-key", raising=False)


def core(ids):
    return [q for q in ev.load_questions() if q.id in ids]


def by_round(plan):
    """plan[질문ID] = 회차별 응답 목록. 회차는 그 질문을 몇 번째 실행하는지로 센다."""
    seen: dict[str, int] = {}

    def factory(q):
        n = seen.get(q.id, 0)
        seen[q.id] = n + 1
        item = plan[q.id][min(n, len(plan[q.id]) - 1)]
        return FakeLLM(item, item) if not isinstance(item, Exception) else FakeLLM(item)
    return factory


def test_flaky_question_is_detected(datasets):
    right, wrong = FakeLLM.calls(RIGHT["Q1"]), FakeLLM.calls(WRONG_Q1)
    factory = by_round({"Q1": [right, wrong, right], "Q2": [FakeLLM.calls(RIGHT["Q2"])]})
    waits = []
    reports = ev.run_repeat(core({"Q1", "Q2"}), datasets, 3, round_wait=5, sleep=waits.append, llm_factory=factory)
    s = ev.repeat_summary(reports)
    q = {x["id"]: x for x in s["questions"]}
    assert (q["Q1"]["passes"], q["Q1"]["measured"], q["Q1"]["flaky"]) == (2, 3, True)
    assert (q["Q2"]["passes"], q["Q2"]["measured"], q["Q2"]["flaky"]) == (3, 3, False)
    assert s["sets"]["core"]["scores"] == [2, 1, 2] and s["sets"]["core"]["mean"] == 1.67
    assert (s["sets"]["core"]["min"], s["sets"]["core"]["max"]) == (1, 2)
    assert waits == [5, 5]  # 회차 사이에만 대기
    md = ev.repeat_md(s)
    assert "반복 측정 3회" in md and "| core | 2/2, 1/2, 2/2 |" in md and "흔들린 질문 (어떤 회차엔 맞고 어떤 회차엔 틀림): 1개" in md
    assert "Q1" in md and "2/3" in md


def test_quota_round_is_not_counted(datasets):
    """한도 초과로 측정 못 한 회차는 그 질문의 분모에서 빠진다 (흔들림으로 보지 않음)."""
    right = FakeLLM.calls(RIGHT["Q1"])
    factory = by_round({"Q1": [right, LLMError("quota", "429"), right]})
    reports = ev.run_repeat(core({"Q1"}), datasets, 2, sleep=lambda s: None, llm_factory=factory, quota_retries=0)
    q = ev.repeat_summary(reports)["questions"][0]
    assert (q["passes"], q["measured"], q["flaky"]) == (1, 1, False)
    assert q["outcomes"] == ["정답", ev.UNMEASURED]


def test_cli_repeat_writes_summary(datasets, monkeypatch, tmp_path, fake_key):
    factory = by_round({"Q1": [FakeLLM.calls(RIGHT["Q1"]), FakeLLM.calls(WRONG_Q1)]})
    real = ev.run_questions
    monkeypatch.setattr(ev, "run_questions", lambda qs, ds, **k: real(qs, ds, factory, **{**k, "delay": 0}))
    out, md = tmp_path / "r.json", tmp_path / "r.md"
    code = ev.main(["--only", "Q1", "--repeat", "2", "--round-wait", "0", "--out", str(out), "--md", str(md)])
    assert code == 1  # 한 번이라도 틀리면 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["times"] == 2 and len(data["rounds"]) == 2
    assert data["summary"]["questions"][0]["flaky"]
    assert "흔들린 질문" in md.read_text(encoding="utf-8")


def test_repeat_one_keeps_old_output(datasets, monkeypatch, tmp_path, fake_key):
    """--repeat를 안 주면 결과 파일 형식은 지금 그대로 (질문 목록)."""
    real = ev.run_questions
    monkeypatch.setattr(ev, "run_questions",
                        lambda qs, ds, **k: real(qs, ds, lambda q: FakeLLM(FakeLLM.calls(RIGHT["Q1"])), **{**k, "delay": 0}))
    out = tmp_path / "one.json"
    assert ev.main(["--only", "Q1", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data[0]["id"] == "Q1"
