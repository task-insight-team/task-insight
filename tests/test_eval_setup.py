"""채점 스크립트: Gemini 설정 오류(키 없음·잘못된 키·없는 모델)는 그 자리에서 멈추고 '측정 못 함'으로 센다.

키 없이 돌면 질문마다 '오류'(오답)로 끝나 측정이 쓸모없어지므로, 시작 전에 키를 확인하고,
실행 중 설정 오류가 나면 남은 질문은 실행하지 않는다. 일시 오류(서버·시간 초과)는 지금처럼 그 질문만 '오류'.
"""

import pytest

from agent import FakeLLM, LLMError
from eval import run_eval as ev
from tests.test_eval_grader import RIGHT


@pytest.fixture(scope="module")
def datasets():
    return ev.load_datasets()


def core(ids):
    return [q for q in ev.load_questions() if q.id in ids]


def counting(script_by_id):
    """질문별 FakeLLM. 만들어진 횟수(= 실행한 질문 수)를 .made에 남긴다."""
    def factory(q):
        factory.made.append(q.id)
        return FakeLLM(*script_by_id(q))
    factory.made = []
    return factory


def test_missing_key_stops_at_first_question(datasets):
    factory = counting(lambda q: [LLMError("no_api_key")])
    runs = ev.run_questions(core({"Q1", "Q2", "Q3"}), datasets, factory, sleep=lambda s: None)
    report = ev.grade(runs)
    assert factory.made == ["Q1"]  # 2·3번째 질문은 LLM을 부르지 않음
    assert [r.status for r in runs] == [ev.SETUP] * 3
    assert all(r["outcome"] == ev.UNMEASURED and not r["measured"] and not r["pass"] for r in report)
    assert "키" in runs[0].message and "남은 질문은 실행하지 않았습니다" in runs[2].message


def test_wrong_key_midway_keeps_earlier_results(datasets):
    script = {"Q1": [FakeLLM.calls(RIGHT["Q1"])], "Q2": [LLMError("auth", "403")], "Q3": [FakeLLM.calls(RIGHT["Q3"])]}
    factory = counting(lambda q: script[q.id])
    report = ev.grade(ev.run_questions(core(set(script)), datasets, factory, sleep=lambda s: None))
    by = {r["id"]: r for r in report}
    assert by["Q1"]["outcome"] == "정답"
    assert by["Q2"]["outcome"] == ev.UNMEASURED and by["Q3"]["outcome"] == ev.UNMEASURED
    assert factory.made == ["Q1", "Q2"]
    assert ev.score_text(report) == "1/1 (측정 못 함 2)"


def test_transient_errors_do_not_stop_the_run(datasets):
    script = {"Q1": [LLMError("server", "503")], "Q2": [FakeLLM.calls(RIGHT["Q2"])]}
    factory = counting(lambda q: script[q.id])
    report = ev.grade(ev.run_questions(core(set(script)), datasets, factory, sleep=lambda s: None))
    by = {r["id"]: r for r in report}
    assert by["Q1"]["outcome"] == "오류" and by["Q1"]["measured"]  # 일시 오류는 지금처럼 오답
    assert by["Q2"]["outcome"] == "정답" and factory.made == ["Q1", "Q2"]


def test_cli_refuses_to_start_without_key(monkeypatch, capsys):
    monkeypatch.setattr(ev, "find_api_key", lambda: None)
    monkeypatch.setattr(ev, "run_questions", lambda *a, **k: pytest.fail("키가 없는데 실행함"))
    assert ev.main(["--only", "Q1"]) == 2
    err = capsys.readouterr().err
    assert "GEMINI_API_KEY" in err and "Read-Host" in err


def test_cli_starts_with_key(monkeypatch):
    called = []
    monkeypatch.setattr(ev, "find_api_key", lambda: "test-key")
    monkeypatch.setattr(ev, "run_questions", lambda *a, **k: called.append(1) or [])
    assert ev.main(["--only", "NONE"]) == 0 and called == [1]
