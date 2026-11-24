"""에이전트 채점: 평가 질문을 실제 에이전트(계획 → 승인 → 실행)로 돌리고, 끝난 뒤 정답과 비교한다.

    $env:GEMINI_API_KEY="..."            # 키는 코드·파일에 쓰지 않는다
    python -m eval.run_eval              # kafe Q1~Q7 + 과제 sample.csv S1~S5
    python -m eval.run_eval --only Q4 S4 --out eval_result.json
    python -m eval.run_eval --set all --md eval_report.md   # 바꿔 말한 질문·답하면 안 되는 질문 포함 + 측정 표

질문 묶음 (--set): core = 평가 질문 Q1~Q7 + S1~S5 (발표용 점수) / paraphrase = 바꿔 말한 질문 /
refuse = 데이터에 없는 것을 묻는 질문 (숫자 없이 지원 불가·되묻기로 끝나야 정답) /
demo = 발표 시연에서 그대로 입력할 질문 (리허설 전 확인: python -m eval.run_eval --set demo) / all = 전부

정답 격리 (CLAUDE.md):
- 1단계(run_questions)는 질문 문장과 데이터만 에이전트에 넘긴다. 정답 파일은 이 단계가 끝날 때까지 열지 않는다.
- 2단계(grade)에서만 tests/fixtures/kafe/evaluation_answers.json, eval/sample_expected.json, eval/demo_expected.json을 읽는다.
  (질문 문장은 docs/kafe_questions.json·eval/sample_questions.json에서 따로 읽는다)
- 채점은 결과 표(ToolResult.rows·value)만 본다. LLM이 쓴 문장은 채점하지 않는다.

승인: 사람이 [승인] 버튼을 누르는 대신 이 스크립트가 approve_plan을 호출한다 (채점 전용).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import approve_plan, create_plan, execute_plan  # noqa: E402
from agent.llm import GeminiClient, LLMError  # noqa: E402
from contracts import ToolResult  # noqa: E402
from engine import load_dataset  # noqa: E402

KAFE_DIR = ROOT / "data" / "kafe"
SAMPLE_CSV = ROOT / "data" / "sample.csv"
GENERAL_DIR = ROOT / "data" / "general"
KAFE_QUESTIONS = ROOT / "docs" / "kafe_questions.json"
KAFE_ANSWERS = ROOT / "tests" / "fixtures" / "kafe" / "evaluation_answers.json"  # 2단계에서만 연다
SAMPLE_QUESTIONS = Path(__file__).with_name("sample_questions.json")  # 과제 sample.csv 질문 문장
SAMPLE_EXPECTED = Path(__file__).with_name("sample_expected.json")  # 그 정답. 2단계에서만 연다
EXTRA_QUESTIONS = Path(__file__).with_name("extra_questions.json")  # 바꿔 말한 질문·답하면 안 되는 질문·시연 질문 (정답 값 없음)
DEMO_EXPECTED = Path(__file__).with_name("demo_expected.json")  # 시연 질문 중 평가 정답에 없는 것. 2단계에서만 연다
GENERAL_QUESTIONS = Path(__file__).with_name("general_questions.json")  # 다른 업종 데이터 질문 (범용성 측정)
GENERAL_EXPECTED = Path(__file__).with_name("general_expected.json")  # 그 정답·종류. 2단계에서만 연다
HOLDOUT_QUESTIONS = Path(__file__).with_name("holdout_questions.json")  # 처음 보는 질문 (수정 후 과적합 확인용)
HOLDOUT_EXPECTED = Path(__file__).with_name("holdout_expected.json")  # 그 정답·종류. 2단계에서만 연다
GENERAL_FILES = {"sales2026": "sales_2026.csv", "inventory": "inventory.csv", "support": "support_tickets.csv"}
SETS = ("core", "paraphrase", "refuse", "demo")  # --set all = 이 묶음들 (발표 전 확인용, 호출 수 유지)
EXTRA_SETS = ("general", "holdout")  # all에 넣지 않고 따로 돌리는 묶음 (Gemini 호출 절약)
# 다른 업종 데이터 묶음: (질문 파일, 정답·종류 파일). 종류(answer·limit·refuse)별로 채점한다
FILE_SETS = {"general": (GENERAL_QUESTIONS, GENERAL_EXPECTED), "holdout": (HOLDOUT_QUESTIONS, HOLDOUT_EXPECTED)}

# Gemini 한도 초과(429): 에이전트 실력과 무관하므로 오답으로 세지 않는다.
# 그 질문만 기다렸다가 다시 돌리고, 그래도 안 되면 '측정 못 함'으로 정답률 분모에서 뺀다.
QUOTA = "quota"  # Run.status: 재시도 후에도 한도 초과
UNMEASURED = "측정 못 함"  # 결과 종류(outcome)
QUOTA_WAIT_S = 45.0  # 분당 한도가 풀릴 때까지 기다리는 시간
QUOTA_RETRIES = 2  # 한도 초과 질문을 다시 돌리는 최대 횟수
QUOTA_STOP_AFTER = 3  # 연속 이만큼의 질문이 끝까지 한도 초과면 일일 한도로 보고 나머지는 실행하지 않는다

KAFE_AS_OF = "2026-11-19"
SAMPLE_AS_OF = "2026-02-28"  # sample.csv 마지막 달. '전월 대비'의 기준 달이 2026-02가 되게 한다
AS_OF = {"kafe": KAFE_AS_OF, "sample": SAMPLE_AS_OF,
         "sales2026": "2026-12-31", "inventory": "2026-11-30", "support": "2026-11-30"}  # 데이터별 기준일


# ---------------------------------------------------------------------------
# 1단계: 에이전트 실행 (정답을 모른다)
# ---------------------------------------------------------------------------
@dataclass
class Question:
    id: str
    text: str
    dataset: str  # "kafe" | "sample"
    as_of: str
    set: str = "core"
    same_as: Optional[str] = None  # 바꿔 말한 질문이면 원래 질문 ID (그 정답 규칙으로 채점)


@dataclass
class Run:
    id: str
    question: str
    status: str  # plan 단계 status (plan·clarify·unsupported·error) 또는 실행 status (ok·partial·error)
    message: str = ""
    calls: list[dict] = field(default_factory=list)  # 실행한 도구와 인자
    results: list[ToolResult] = field(default_factory=list)
    seconds: float = 0.0
    set: str = "core"
    same_as: Optional[str] = None
    stage: str = "plan"  # plan: 계획 단계에서 멈춤 / exec: 승인 후 실행까지 감
    llm_calls: int = 0  # 에이전트가 LLM에 요청한 횟수 (재계획 포함, SDK 내부 재시도 제외)
    model: str = ""
    retries: int = 0  # 한도 초과로 이 질문을 다시 돌린 횟수 (시간·호출 수는 마지막 실행 기준)


def load_questions(sets: tuple = ("core",)) -> list[Question]:
    out: list[Question] = []
    if "core" in sets:
        kafe = json.loads(KAFE_QUESTIONS.read_text(encoding="utf-8"))["questions"]
        sample = json.loads(SAMPLE_QUESTIONS.read_text(encoding="utf-8"))["questions"]
        out += ([Question(q["id"], q["question"], "kafe", KAFE_AS_OF) for q in kafe]
                + [Question(q["id"], q["question"], "sample", SAMPLE_AS_OF) for q in sample])
    extra = [q for q in json.loads(EXTRA_QUESTIONS.read_text(encoding="utf-8"))["questions"] if q["set"] in sets]
    out += [Question(q["id"], q["question"], q["dataset"], AS_OF[q["dataset"]], q["set"], q.get("same_as"))
            for q in extra]
    for name, (qfile, _) in FILE_SETS.items():
        if name in sets:
            rows = json.loads(qfile.read_text(encoding="utf-8"))["questions"]
            out += [Question(q["id"], q["question"], q["dataset"], AS_OF[q["dataset"]], name) for q in rows]
    return out


class _Counting:
    """LLM 호출 수를 센다. 실제 클라이언트는 첫 호출 때 만든다 (키 없음 오류도 create_plan이 처리하도록)."""

    def __init__(self, make: Callable[[], Any]):
        self._make, self._inner, self.calls = make, None, 0
        self.errors: list[str] = []  # 호출 중 난 LLMError 종류. 에이전트가 삼킨 오류(재계획 실패 등)도 여기 남는다

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "")

    def generate(self, system: str, user: str, tools: list[dict]):
        self.calls += 1
        try:
            if self._inner is None:
                self._inner = self._make()
            return self._inner.generate(system, user, tools)
        except LLMError as exc:
            self.errors.append(exc.kind)
            raise


def load_datasets() -> dict:
    kafe = load_dataset({f"{n}.csv": KAFE_DIR / f"{n}.csv" for n in ("tasks", "employees", "projects")})
    sample = load_dataset({"sample.csv": SAMPLE_CSV})
    general = {k: load_dataset({f: GENERAL_DIR / f}) for k, f in GENERAL_FILES.items()}
    assert kafe.ok and sample.ok and all(g.ok for g in general.values()), "데이터를 불러오지 못했습니다"
    return {"kafe": kafe.dataset, "sample": sample.dataset, **{k: g.dataset for k, g in general.items()}}


def _run_once(q: Question, ds: Any, llm_factory: Optional[Callable[[Question], Any]]) -> tuple[Run, bool]:
    """질문 1개를 한 번 실행한다. (결과, 도중에 한도 초과가 있었는지)."""
    start = time.perf_counter()
    llm = _Counting((lambda q=q: llm_factory(q)) if llm_factory else GeminiClient)
    plan = create_plan(q.text, ds, q.as_of, llm)
    meta = {"set": q.set, "same_as": q.same_as, "llm_calls": llm.calls, "model": llm.model}
    if plan.status != "plan":
        run = Run(q.id, q.text, plan.status, plan.message, seconds=time.perf_counter() - start, **meta)
    else:
        out = execute_plan(ds, plan.plan, approve_plan(plan.plan))
        run = Run(q.id, q.text, out.status, out.message,
                  calls=[{"tool": s.tool, "params": s.result.params} for s in out.steps],
                  results=[s.result for s in out.steps], seconds=time.perf_counter() - start,
                  stage="exec", **meta)
    return run, "quota" in llm.errors


def run_questions(questions: list[Question], datasets: dict, llm_factory: Optional[Callable[[Question], Any]] = None,
                  delay: float = 0.0, progress: Optional[Callable[[int, int, "Run"], None]] = None,
                  quota_wait: float = QUOTA_WAIT_S, quota_retries: int = QUOTA_RETRIES,
                  quota_stop_after: int = QUOTA_STOP_AFTER, sleep: Callable[[float], None] = time.sleep) -> list[Run]:
    """질문마다 create_plan → approve_plan → execute_plan. llm_factory는 테스트용 (기본: Gemini).

    한도 초과(429)가 한 번이라도 난 질문은 결과를 버리고 quota_wait초 뒤 처음부터 다시 돌린다 (최대 quota_retries번).
    에이전트가 오류를 삼켜 다른 결과를 냈어도(재계획 호출 실패 등) 다시 돌린다. 그래도 안 되면 status=quota(측정 못 함).
    연속 quota_stop_after개 질문이 측정 못 함이면 일일 한도로 보고 남은 질문은 실행하지 않는다.
    """
    runs: list[Run] = []
    streak = 0
    for i, q in enumerate(questions):
        if streak >= quota_stop_after:
            run = Run(q.id, q.text, QUOTA, f"앞의 {streak}개 질문이 연속으로 한도 초과라 실행하지 않았습니다 (일일 한도 가능성)",
                      set=q.set, same_as=q.same_as)
        else:
            if i and delay:
                sleep(delay)  # 무료 등급 분당 호출 한도
            run, hit = _run_once(q, datasets[q.dataset], llm_factory)
            tries = 0
            while hit and tries < quota_retries:
                tries += 1
                sleep(quota_wait)
                run, hit = _run_once(q, datasets[q.dataset], llm_factory)
            run.retries = tries
            if hit:  # 한도 초과 중에 나온 결과는 믿을 수 없어 버린다
                run = Run(q.id, q.text, QUOTA, f"Gemini 사용 한도 초과로 측정하지 못했습니다 (다시 시도 {tries}회)",
                          set=q.set, same_as=q.same_as, llm_calls=run.llm_calls, model=run.model,
                          seconds=run.seconds, retries=tries)
            streak = streak + 1 if hit else 0
        runs.append(run)
        if progress:
            progress(i + 1, len(questions), runs[-1])
    return runs


def _print_progress(n: int, total: int, run: Run) -> None:
    """실행 중 진행 표시 (정답과 무관한 정보만: 순번·상태·도구·시간)."""
    tools = ", ".join(c["tool"] for c in run.calls) or "-"
    again = f", 한도 초과로 다시 시도 {run.retries}회" if run.retries else ""
    print(f"  [{n}/{total}] {run.id:<6} {run.status:<11} {tools} ({run.seconds:.1f}s{again})", flush=True)


# ---------------------------------------------------------------------------
# 2단계: 채점 (여기서부터 정답을 읽는다)
# ---------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _close(a: Optional[float], b: float, tol: float) -> bool:
    return a is not None and abs(a - b) <= tol


def _columns(rows: list[dict], has: Optional[str]) -> list[str]:
    if not rows:
        return []
    first = next(iter(rows[0]))
    return [c for c in rows[0] if c != first and (has is None or has in c)
            and any(_num(r.get(c)) is not None for r in rows)]


def check(results: list[ToolResult], spec: dict) -> tuple[bool, str]:
    """채점 규칙 하나. 성공한 결과 중 하나라도 맞으면 통과."""
    ok_results = [r for r in results if r.ok]
    kind = spec["type"]
    tol = spec.get("tol", 0.01)

    if kind == "error":  # 계산할 수 없다고 안내해야 정답 (숫자를 만들면 오답)
        if ok_results:
            return False, "계산 불가 질문인데 결과를 냈습니다"
        if any(spec["contains"] in (r.error or "") for r in results):
            return True, "계산 불가 사유 안내"
        return False, f"오류 사유에 '{spec['contains']}'가 없습니다"

    for r in ok_results:
        if kind == "found":  # 결과 값이나 key 행의 숫자 칸 중 하나가 equals (도구를 어떻게 골랐든 화면에 그 숫자가 나오면 됨)
            first = next(iter(r.rows[0])) if r.rows else None
            if spec.get("key") is None and _close(_num(r.value), spec["equals"], tol):
                return True, f"값 {r.value}"
            if spec.get("key") is not None and len(r.rows) == 1 and str(r.rows[0].get(first)) in ("전체", spec["key"]) \
                    and _close(_num(r.value), spec["equals"], tol):
                return True, f"값 {r.value}"  # 조건(필터)으로 key만 계산한 경우
            for x in r.rows:
                if spec.get("key") is not None and str(x.get(first)) != spec["key"]:
                    continue
                if any(_close(_num(v), spec["equals"], tol) for k, v in x.items() if k != first):
                    return True, f"{x.get(first)} 행에 {spec['equals']}"
        elif kind == "row":  # key 행의 특정 컬럼 값
            first = next(iter(r.rows[0])) if r.rows else None
            for c in _columns(r.rows, spec.get("column_has")):
                for x in r.rows:
                    if str(x.get(first)) == spec["key"] and _close(_num(x.get(c)), spec["value"], tol):
                        return True, f"{spec['key']}의 '{c}' {x.get(c)}"
        elif kind == "value":
            if _close(_num(r.value), spec["equals"], tol):
                return True, f"값 {r.value}"
            # 그룹별로 나눠 계산한 경우: 해당 컬럼 합계
            for c in _columns(r.rows, spec.get("sum_column_has")) if spec.get("sum_column_has") else []:
                if _close(sum(_num(x.get(c)) or 0 for x in r.rows), spec["equals"], tol):
                    return True, f"'{c}' 합계 {spec['equals']}"
        elif kind == "mapping":
            first = next(iter(r.rows[0])) if r.rows else None
            for c in _columns(r.rows, spec.get("column_has")):
                got = {str(x[first]): _num(x.get(c)) for x in r.rows}
                want = spec["equals"]
                extra = {k: v for k, v in got.items() if k not in want and v}
                if all(_close(got.get(k), v, tol) for k, v in want.items()) and not extra:
                    return True, f"'{first}'별 '{c}' 일치"
        elif kind == "extreme":
            first = next(iter(r.rows[0])) if r.rows else None
            for c in _columns(r.rows, spec.get("column_has")):
                vals = [(str(x[first]), _num(x.get(c))) for x in r.rows if _num(x.get(c)) is not None]
                best = (max if spec["which"] == "max" else min)(v for _, v in vals)
                ids = [k for k, v in vals if v == best]
                if ids == [spec["id"]] and ("value" not in spec or _close(best, spec["value"], tol)):
                    return True, f"'{c}' {'최대' if spec['which'] == 'max' else '최소'} {spec['id']} ({best})"
    return False, "정답과 일치하는 결과 없음"


def kafe_specs(answers: dict) -> dict[str, list[dict]]:
    """evaluation_answers.json → 채점 규칙. 값은 정답 파일에서 가져온다 (여기에 숫자를 적지 않는다)."""
    a = {x["id"]: x for x in answers["answers"]}
    pct = lambda frac: round(frac * 100, 2)  # 정답 파일은 비율(0.3072), 엔진 결과는 퍼센트(30.72)
    return {
        "Q1": [{"type": "value", "equals": a["Q1"]["answer"], "sum_column_has": "기한 초과"}],
        "Q2": [{"type": "mapping", "equals": a["Q2"]["answer"]}],
        "Q3": [{"type": "extreme", "which": "max", "id": a["Q3"]["answer"], "column_has": "초과"}],
        "Q4": [{"type": "extreme", "which": "max", "id": a["Q4"]["answer"], "column_has": "초과율",
                "value": pct(a["Q4"]["answer_value"]), "tol": 0.5}],
        "Q5": [{"type": "extreme", "which": "min", "id": a["Q5"]["answer"], "column_has": "완료율",
                "value": pct(a["Q5"]["answer_value"])}],
        "Q6": [{"type": "value", "equals": a["Q6"]["answer"]}],
        "Q7": [{"type": "extreme", "which": "max", "id": a["Q7"]["answer"], "value": a["Q7"]["answer_value"]}],
    }


def grade(runs: list[Run]) -> list[dict]:
    specs = kafe_specs(json.loads(KAFE_ANSWERS.read_text(encoding="utf-8")))
    for path in (SAMPLE_EXPECTED, DEMO_EXPECTED):
        specs.update({a["id"]: a["checks"] for a in json.loads(path.read_text(encoding="utf-8"))["answers"]})
    kinds: dict[str, str] = {}
    for name, (_, efile) in FILE_SETS.items():
        if any(r.set == name for r in runs):
            rows = json.loads(efile.read_text(encoding="utf-8"))["answers"]
            specs.update({a["id"]: a["checks"] for a in rows})
            kinds.update({a["id"]: a["kind"] for a in rows})
    report = []
    for run in runs:
        rules = specs.get(run.same_as or run.id, [])
        kind = kinds.get(run.id) if run.set in FILE_SETS else None
        shown_ok = any(r.ok for r in run.results)
        if run.status == QUOTA:  # 에이전트 실력과 무관: 정답도 오답도 아니다 (정답률 분모에서 뺀다)
            ok, reasons = False, [run.message]
        elif kind == "limit" and not shown_ok:
            # 한계 질문: 숫자 없이 정직하게 거절하면 통과 (API 오류는 오답)
            ok = not (run.stage == "plan" and run.status == "error")
            reasons = [f"정직한 거절 ({run.status}): {run.message}" if ok else f"오류 ({run.status}): {run.message}"]
        elif run.set == "refuse" or kind == "refuse":  # 데이터에 없는 것을 물음: 숫자를 내면 오답
            shown = [c["tool"] for c, r in zip(run.calls, run.results) if r.ok]
            if shown:
                ok, reasons = False, [f"답할 수 없는 질문인데 다른 분석을 실행해 결과를 냈습니다 ({', '.join(shown)})"]
            elif run.stage == "plan":
                ok = run.status in ("clarify", "unsupported")  # API 오류는 오답
                reasons = [f"계획 단계에서 안내 ({run.status}): {run.message}"]
            else:
                ok, reasons = True, ["숫자 없이 계산 불가 사유를 안내했습니다"]
        elif not run.results:  # 계획 단계에서 멈춤 (되묻기·지원 불가·오류) 또는 실행 안 함
            # 계산 불가 질문은 계획 단계에서 사유를 안내해도 정답. API 오류는 항상 오답
            ok = (bool(rules) and run.status in ("clarify", "unsupported")
                  and all(s["type"] == "error" and s["contains"] in run.message for s in rules))
            reasons = [f"계획 단계에서 멈춤 ({run.status}): {run.message}"]
        else:
            outcomes = [check(run.results, s) for s in rules]
            ok, reasons = bool(rules) and all(o for o, _ in outcomes), [m for _, m in outcomes]
        report.append({"id": run.id, "set": run.set, "kind": kind, "question": run.question, "pass": ok,
                       "outcome": _outcome(run, ok, kind), "status": run.status, "calls": run.calls,
                       "reason": "; ".join(reasons), "seconds": round(run.seconds, 1),
                       "llm_calls": run.llm_calls, "model": run.model,
                       "measured": run.status != QUOTA, "retries": run.retries})
    return report


def _outcome(run: Run, ok: bool, kind: Optional[str]) -> str:
    """결과 종류: 정답 / 정직한 거절 / 오답 / 대체 분석 / 답하지 못함 / 오류 (범용성 측정 분류용)."""
    shown = any(r.ok for r in run.results)
    if run.status == QUOTA:
        return UNMEASURED
    if run.stage == "plan" and run.status == "error":
        return "오류"
    if ok:
        return "정답" if shown else "정직한 거절"
    if shown:
        return "대체 분석" if (run.set == "refuse" or kind == "refuse") else "오답"
    return "답하지 못함"


def mark(r: dict) -> str:
    """O 정답 / X 오답 / - 측정 못 함."""
    return "O" if r["pass"] else ("X" if r.get("measured", True) else "-")


def score_text(part: list[dict]) -> str:
    """'정답 수/측정한 질문 수'. 한도 초과로 측정 못 한 질문은 분모에서 빼고 따로 적는다."""
    measured = [r for r in part if r.get("measured", True)]
    skipped = len(part) - len(measured)
    return f"{sum(r['pass'] for r in measured)}/{len(measured)}" + (f" (측정 못 함 {skipped})" if skipped else "")


def summary_md(report: list[dict]) -> str:
    """제출용 측정 표 (정확도·시간·LLM 호출 수)."""
    lines = ["| 묶음 | 정답 | 질문당 시간 (평균 / 최대) | LLM 호출 (합계 / 질문당 최대) |", "|---|---|---|---|"]
    for s in (*SETS, *EXTRA_SETS):
        part = [r for r in report if r["set"] == s]
        if not part:
            continue
        sec = [r["seconds"] for r in part]
        calls = [r["llm_calls"] for r in part]
        lines.append(f"| {s} | {score_text(part)} | {sum(sec) / len(sec):.1f}초 / {max(sec):.1f}초 "
                     f"| {sum(calls)}회 / {max(calls)}회 |")
    models = sorted({r["model"] for r in report if r["model"]})
    lines += ["", f"모델: {', '.join(models) or '-'} · 시간은 질문 1개의 계획(LLM)+실행(엔진) 전체"]
    if any(not r.get("measured", True) for r in report):
        lines.append(f"'{UNMEASURED}' = Gemini 사용 한도 초과로 다시 시도해도 실행하지 못한 질문 (정답률 분모에서 제외)")
    for name in FILE_SETS:  # 범용성 측정: 종류(answer·limit·refuse)별 결과 분류
        part_set = [r for r in report if r["set"] == name]
        if not part_set:
            continue
        lines += ["", f"범용성 측정 ({name}): 질문 종류별 결과", "",
                  "| 종류 | 질문 수 | 정답 | 정직한 거절 | 오답 | 대체 분석 | 답하지 못함 | 오류 | 측정 못 함 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for k in ("answer", "limit", "refuse"):
            part = [r for r in part_set if r["kind"] == k]
            if part:
                cnt = {o: sum(r["outcome"] == o for r in part) for o in ("정답", "정직한 거절", "오답", "대체 분석", "답하지 못함", "오류", UNMEASURED)}
                lines.append(f"| {k} | {len(part)} | " + " | ".join(str(v) for v in cnt.values()) + " |")
    lines += ["", "| ID | 질문 | 결과 | 분류 | 도구 | 판정 |", "|---|---|---|---|---|---|"]
    for r in report:
        tools = ", ".join(c["tool"] for c in r["calls"]) or "-"
        reason = r["reason"].replace("|", "/").replace("\n", " ")[:80]
        lines.append(f"| {r['id']} | {r['question']} | {mark(r)} | {r['outcome']} | {tools} | {reason} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 반복 측정: 같은 질문을 여러 번 돌려 흔들리는 질문과 점수 범위를 본다 (Gemini 응답은 매번 조금씩 다를 수 있음)
# ---------------------------------------------------------------------------
def run_repeat(questions: list[Question], datasets: dict, times: int, round_wait: float = 0.0,
               sleep: Callable[[float], None] = time.sleep, **kwargs) -> list[list[dict]]:
    """질문 묶음을 times번 실행·채점한다. 회차마다 채점 결과(report) 목록. kwargs는 run_questions로 넘긴다.

    회차 사이에 round_wait초 쉰다 (무료 한도 보호). 각 회차는 독립이다 (한도 초과 연속 판정도 회차마다 새로).
    """
    reports = []
    for i in range(times):
        if i and round_wait:
            sleep(round_wait)
        reports.append(grade(run_questions(questions, datasets, sleep=sleep, **kwargs)))
    return reports


def repeat_summary(reports: list[list[dict]]) -> dict:
    """회차별 결과를 묶음·질문 단위로 모은다. 측정 못 한 회차는 질문의 분모에서 뺀다."""
    rounds = []
    for rep in reports:
        by_set: dict[str, dict] = {}
        for r in rep:
            b = by_set.setdefault(r["set"], {"passed": 0, "measured": 0, "total": 0})
            b["total"] += 1
            if r.get("measured", True):
                b["measured"] += 1
                b["passed"] += int(r["pass"])
        rounds.append(by_set)
    questions: dict[str, dict] = {}
    for rep in reports:
        for r in rep:
            q = questions.setdefault(r["id"], {"id": r["id"], "set": r["set"], "question": r["question"],
                                               "passes": 0, "measured": 0, "outcomes": []})
            q["outcomes"].append(r["outcome"])
            if r.get("measured", True):
                q["measured"] += 1
                q["passes"] += int(r["pass"])
    for q in questions.values():
        q["flaky"] = 0 < q["passes"] < q["measured"]  # 같은 질문이 어떤 회차엔 맞고 어떤 회차엔 틀림
    sets = sorted({s for rnd in rounds for s in rnd}, key=lambda x: (*SETS, *EXTRA_SETS).index(x)
                  if x in (*SETS, *EXTRA_SETS) else 99)
    per_set = {}
    for s in sets:
        scores = [rnd[s]["passed"] for rnd in rounds if s in rnd]
        measured = [rnd[s]["measured"] for rnd in rounds if s in rnd]
        per_set[s] = {"scores": scores, "measured": measured, "total": max(rnd[s]["total"] for rnd in rounds if s in rnd),
                      "min": min(scores), "max": max(scores), "mean": round(sum(scores) / len(scores), 2)}
    return {"times": len(reports), "sets": per_set, "questions": list(questions.values())}


def repeat_md(summary: dict) -> str:
    """반복 측정 표: 묶음별 회차 점수·평균·범위, 흔들린 질문, 질문별 통과 횟수."""
    n = summary["times"]
    lines = [f"반복 측정 {n}회 (같은 질문·같은 프롬프트, 질문당 {n}번 실행)", "",
             "| 묶음 | 회차별 정답 (측정한 질문 수) | 평균 | 최소~최대 |", "|---|---|---|---|"]
    for s, v in summary["sets"].items():
        each = ", ".join(f"{p}/{m}" for p, m in zip(v["scores"], v["measured"]))
        lines.append(f"| {s} | {each} | {v['mean']:g} / {v['total']} | {v['min']}~{v['max']} |")
    flaky = [q for q in summary["questions"] if q["flaky"]]
    lines += ["", f"흔들린 질문 (어떤 회차엔 맞고 어떤 회차엔 틀림): {len(flaky)}개"]
    lines += [f"- {q['id']} {q['question']} — {q['passes']}/{q['measured']} 정답 ({', '.join(q['outcomes'])})" for q in flaky]
    lines += ["", "| ID | 질문 | 정답 횟수 | 회차별 결과 |", "|---|---|---|---|"]
    for q in summary["questions"]:
        lines.append(f"| {q['id']} | {q['question']} | {q['passes']}/{q['measured']} | {', '.join(q['outcomes'])} |")
    return "\n".join(lines) + "\n"


def _main_repeat(args, sets, questions) -> int:
    print(f"질문 {len(questions)}개 × {args.repeat}회 실행 중 (회차 사이 {args.round_wait:g}초 대기)...", flush=True)
    datasets = load_datasets()
    reports = []
    for i in range(args.repeat):  # 회차마다 진행 표시를 보여 주려고 run_repeat 대신 한 회차씩
        if i and args.round_wait:
            time.sleep(args.round_wait)
        print(f"[{i + 1}/{args.repeat}회차]", flush=True)
        reports += run_repeat(questions, datasets, 1, delay=args.delay, progress=_print_progress,
                              quota_wait=args.quota_wait, quota_retries=args.quota_retries)
    summary = repeat_summary(reports)
    print()
    for s, v in summary["sets"].items():
        print(f"{s}: 회차별 정답 {', '.join(f'{p}/{m}' for p, m in zip(v['scores'], v['measured']))} "
              f"(평균 {v['mean']:g}/{v['total']}, 범위 {v['min']}~{v['max']})")
    flaky = [q for q in summary["questions"] if q["flaky"]]
    print(f"흔들린 질문 {len(flaky)}개" + "".join(f"\n  {q['id']} {q['passes']}/{q['measured']} {q['question']}"
                                                  for q in flaky))
    print(f"LLM 호출 합계 {sum(r['llm_calls'] for rep in reports for r in rep)}회")
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rounds": reports}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    if args.md:
        Path(args.md).write_text(repeat_md(summary), encoding="utf-8")
    return 0 if all(r["pass"] for rep in reports for r in rep) else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 채점 (실제 Gemini 호출)")
    ap.add_argument("--only", nargs="*", help="이 ID만 실행 (예: Q1 Q4 S4)")
    ap.add_argument("--delay", type=float, default=2.0, help="질문 사이 대기 초 (무료 한도 보호)")
    ap.add_argument("--out", help="결과를 JSON으로 저장할 경로")
    ap.add_argument("--set", default="core", choices=[*SETS, *EXTRA_SETS, "all"],
                    help="질문 묶음 (기본 core = 발표용 점수, all = core·paraphrase·refuse·demo, general·holdout은 따로)")
    ap.add_argument("--md", help="측정 표(정확도·시간·호출 수)를 마크다운으로 저장할 경로")
    ap.add_argument("--quota-wait", type=float, default=QUOTA_WAIT_S, help="한도 초과 질문을 다시 돌리기 전 대기 초")
    ap.add_argument("--quota-retries", type=int, default=QUOTA_RETRIES, help="한도 초과 질문을 다시 돌리는 최대 횟수")
    ap.add_argument("--repeat", type=int, default=1, help="같은 묶음을 이 횟수만큼 반복 측정 (2 이상이면 질문별 정답 횟수·흔들린 질문 표시)")
    ap.add_argument("--round-wait", type=float, default=10.0, help="반복 측정에서 회차 사이 대기 초")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    sets = SETS if args.set == "all" else (args.set,)
    questions = [q for q in load_questions(sets) if not args.only or q.id in args.only]
    if args.repeat > 1:
        return _main_repeat(args, sets, questions)
    print(f"질문 {len(questions)}개 실행 중 (질문 사이 {args.delay:g}초 대기, 끝난 뒤 한꺼번에 채점)...", flush=True)
    runs = run_questions(questions, load_datasets(), delay=args.delay, progress=_print_progress,  # 1단계: 정답 모름
                         quota_wait=args.quota_wait, quota_retries=args.quota_retries)
    print()
    report = grade(runs)  # 2단계: 정답 비교

    for r in report:
        tools = ", ".join(c["tool"] for c in r["calls"]) or "-"
        print(f"{mark(r)} {r['id']:<3} {r['question']}\n    도구: {tools} | {r['reason']} ({r['seconds']}s)")
    passed = sum(r["pass"] for r in report)
    print()
    for s in sets:
        part = [r for r in report if r["set"] == s]
        if part:
            print(f"{s}: 정답 {score_text(part)}")
            if s in FILE_SETS:
                counts = {}
                for r in part:
                    counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
                print("  분류: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"전체: 정답 {score_text(report)} · LLM 호출 {sum(r['llm_calls'] for r in report)}회")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(summary_md(report), encoding="utf-8")
    return 0 if passed == len(report) else 1  # 측정 못 한 질문이 있어도 1 (전부 맞았다고 볼 수 없음)


if __name__ == "__main__":
    raise SystemExit(main())
