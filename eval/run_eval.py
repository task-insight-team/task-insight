"""에이전트 채점: 평가 질문을 실제 에이전트(계획 → 승인 → 실행)로 돌리고, 끝난 뒤 정답과 비교한다.

    $env:GEMINI_API_KEY="..."            # 키는 코드·파일에 쓰지 않는다
    python -m eval.run_eval              # kafe Q1~Q7 + 과제 sample.csv S1~S5
    python -m eval.run_eval --only Q4 S4 --out eval_result.json

정답 격리 (CLAUDE.md):
- 1단계(run_questions)는 질문 문장과 데이터만 에이전트에 넘긴다. 정답 파일은 이 단계가 끝날 때까지 열지 않는다.
- 2단계(grade)에서만 tests/fixtures/kafe/evaluation_answers.json과 eval/sample_expected.json을 읽는다.
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
from contracts import ToolResult  # noqa: E402
from engine import load_dataset  # noqa: E402

KAFE_DIR = ROOT / "data" / "kafe"
SAMPLE_CSV = ROOT / "data" / "sample.csv"
KAFE_QUESTIONS = ROOT / "docs" / "kafe_questions.json"
KAFE_ANSWERS = ROOT / "tests" / "fixtures" / "kafe" / "evaluation_answers.json"  # 2단계에서만 연다
SAMPLE_QUESTIONS = Path(__file__).with_name("sample_questions.json")  # 과제 sample.csv 질문 문장
SAMPLE_EXPECTED = Path(__file__).with_name("sample_expected.json")  # 그 정답. 2단계에서만 연다

KAFE_AS_OF = "2026-11-19"
SAMPLE_AS_OF = "2026-02-28"  # sample.csv 마지막 달. '전월 대비'의 기준 달이 2026-02가 되게 한다


# ---------------------------------------------------------------------------
# 1단계: 에이전트 실행 (정답을 모른다)
# ---------------------------------------------------------------------------
@dataclass
class Question:
    id: str
    text: str
    dataset: str  # "kafe" | "sample"
    as_of: str


@dataclass
class Run:
    id: str
    question: str
    status: str  # plan 단계 status (plan·clarify·unsupported·error) 또는 실행 status (ok·partial·error)
    message: str = ""
    calls: list[dict] = field(default_factory=list)  # 실행한 도구와 인자
    results: list[ToolResult] = field(default_factory=list)
    seconds: float = 0.0


def load_questions() -> list[Question]:
    kafe = json.loads(KAFE_QUESTIONS.read_text(encoding="utf-8"))["questions"]
    sample = json.loads(SAMPLE_QUESTIONS.read_text(encoding="utf-8"))["questions"]
    return ([Question(q["id"], q["question"], "kafe", KAFE_AS_OF) for q in kafe]
            + [Question(q["id"], q["question"], "sample", SAMPLE_AS_OF) for q in sample])


def load_datasets() -> dict:
    kafe = load_dataset({f"{n}.csv": KAFE_DIR / f"{n}.csv" for n in ("tasks", "employees", "projects")})
    sample = load_dataset({"sample.csv": SAMPLE_CSV})
    assert kafe.ok and sample.ok, "데이터를 불러오지 못했습니다"
    return {"kafe": kafe.dataset, "sample": sample.dataset}


def run_questions(questions: list[Question], datasets: dict, llm_factory: Optional[Callable[[Question], Any]] = None,
                  delay: float = 0.0) -> list[Run]:
    """질문마다 create_plan → approve_plan → execute_plan. llm_factory는 테스트용 (기본: Gemini)."""
    runs = []
    for i, q in enumerate(questions):
        if i and delay:
            time.sleep(delay)  # 무료 등급 분당 호출 한도
        start = time.perf_counter()
        ds = datasets[q.dataset]
        plan = create_plan(q.text, ds, q.as_of, llm_factory(q) if llm_factory else None)
        if plan.status != "plan":
            runs.append(Run(q.id, q.text, plan.status, plan.message, seconds=time.perf_counter() - start))
            continue
        out = execute_plan(ds, plan.plan, approve_plan(plan.plan))
        runs.append(Run(q.id, q.text, out.status, out.message,
                        calls=[{"tool": s.tool, "params": s.result.params} for s in out.steps],
                        results=[s.result for s in out.steps], seconds=time.perf_counter() - start))
    return runs


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
        if kind == "value":
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
    specs.update({a["id"]: a["checks"] for a in json.loads(SAMPLE_EXPECTED.read_text(encoding="utf-8"))["answers"]})
    report = []
    for run in runs:
        rules = specs.get(run.id, [])
        if not run.results:  # 계획 단계에서 멈춤 (되묻기·지원 불가·오류) 또는 실행 안 함
            # 계산 불가 질문은 계획 단계에서 사유를 안내해도 정답. API 오류는 항상 오답
            ok = (bool(rules) and run.status in ("clarify", "unsupported")
                  and all(s["type"] == "error" and s["contains"] in run.message for s in rules))
            reasons = [f"계획 단계에서 멈춤 ({run.status}): {run.message}"]
        else:
            outcomes = [check(run.results, s) for s in rules]
            ok, reasons = bool(rules) and all(o for o, _ in outcomes), [m for _, m in outcomes]
        report.append({"id": run.id, "question": run.question, "pass": ok, "status": run.status,
                       "calls": run.calls, "reason": "; ".join(reasons), "seconds": round(run.seconds, 1)})
    return report


# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 채점 (실제 Gemini 호출)")
    ap.add_argument("--only", nargs="*", help="이 ID만 실행 (예: Q1 Q4 S4)")
    ap.add_argument("--delay", type=float, default=2.0, help="질문 사이 대기 초 (무료 한도 보호)")
    ap.add_argument("--out", help="결과를 JSON으로 저장할 경로")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    questions = [q for q in load_questions() if not args.only or q.id in args.only]
    runs = run_questions(questions, load_datasets(), delay=args.delay)  # 1단계: 정답 모름
    report = grade(runs)  # 2단계: 정답 비교

    for r in report:
        tools = ", ".join(c["tool"] for c in r["calls"]) or "-"
        print(f"{'O' if r['pass'] else 'X'} {r['id']:<3} {r['question']}\n    도구: {tools} | {r['reason']} ({r['seconds']}s)")
    passed = sum(r["pass"] for r in report)
    print(f"\n정답 {passed}/{len(report)}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
