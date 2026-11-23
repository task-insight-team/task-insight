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
from agent.llm import GeminiClient  # noqa: E402
from contracts import ToolResult  # noqa: E402
from engine import load_dataset  # noqa: E402

KAFE_DIR = ROOT / "data" / "kafe"
SAMPLE_CSV = ROOT / "data" / "sample.csv"
KAFE_QUESTIONS = ROOT / "docs" / "kafe_questions.json"
KAFE_ANSWERS = ROOT / "tests" / "fixtures" / "kafe" / "evaluation_answers.json"  # 2단계에서만 연다
SAMPLE_QUESTIONS = Path(__file__).with_name("sample_questions.json")  # 과제 sample.csv 질문 문장
SAMPLE_EXPECTED = Path(__file__).with_name("sample_expected.json")  # 그 정답. 2단계에서만 연다
EXTRA_QUESTIONS = Path(__file__).with_name("extra_questions.json")  # 바꿔 말한 질문·답하면 안 되는 질문·시연 질문 (정답 값 없음)
DEMO_EXPECTED = Path(__file__).with_name("demo_expected.json")  # 시연 질문 중 평가 정답에 없는 것. 2단계에서만 연다
SETS = ("core", "paraphrase", "refuse", "demo")

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


def load_questions(sets: tuple = ("core",)) -> list[Question]:
    out: list[Question] = []
    if "core" in sets:
        kafe = json.loads(KAFE_QUESTIONS.read_text(encoding="utf-8"))["questions"]
        sample = json.loads(SAMPLE_QUESTIONS.read_text(encoding="utf-8"))["questions"]
        out += ([Question(q["id"], q["question"], "kafe", KAFE_AS_OF) for q in kafe]
                + [Question(q["id"], q["question"], "sample", SAMPLE_AS_OF) for q in sample])
    extra = [q for q in json.loads(EXTRA_QUESTIONS.read_text(encoding="utf-8"))["questions"] if q["set"] in sets]
    out += [Question(q["id"], q["question"], q["dataset"], KAFE_AS_OF if q["dataset"] == "kafe" else SAMPLE_AS_OF,
                     q["set"], q.get("same_as")) for q in extra]
    return out


class _Counting:
    """LLM 호출 수를 센다. 실제 클라이언트는 첫 호출 때 만든다 (키 없음 오류도 create_plan이 처리하도록)."""

    def __init__(self, make: Callable[[], Any]):
        self._make, self._inner, self.calls = make, None, 0

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "")

    def generate(self, system: str, user: str, tools: list[dict]):
        self.calls += 1
        if self._inner is None:
            self._inner = self._make()
        return self._inner.generate(system, user, tools)


def load_datasets() -> dict:
    kafe = load_dataset({f"{n}.csv": KAFE_DIR / f"{n}.csv" for n in ("tasks", "employees", "projects")})
    sample = load_dataset({"sample.csv": SAMPLE_CSV})
    assert kafe.ok and sample.ok, "데이터를 불러오지 못했습니다"
    return {"kafe": kafe.dataset, "sample": sample.dataset}


def run_questions(questions: list[Question], datasets: dict, llm_factory: Optional[Callable[[Question], Any]] = None,
                  delay: float = 0.0, progress: Optional[Callable[[int, int, "Run"], None]] = None) -> list[Run]:
    """질문마다 create_plan → approve_plan → execute_plan. llm_factory는 테스트용 (기본: Gemini)."""
    runs = []
    for i, q in enumerate(questions):
        if i and delay:
            time.sleep(delay)  # 무료 등급 분당 호출 한도
        start = time.perf_counter()
        ds = datasets[q.dataset]
        llm = _Counting((lambda q=q: llm_factory(q)) if llm_factory else GeminiClient)
        plan = create_plan(q.text, ds, q.as_of, llm)
        meta = {"set": q.set, "same_as": q.same_as, "llm_calls": llm.calls, "model": llm.model}
        if plan.status != "plan":
            runs.append(Run(q.id, q.text, plan.status, plan.message, seconds=time.perf_counter() - start, **meta))
        else:
            out = execute_plan(ds, plan.plan, approve_plan(plan.plan))
            runs.append(Run(q.id, q.text, out.status, out.message,
                            calls=[{"tool": s.tool, "params": s.result.params} for s in out.steps],
                            results=[s.result for s in out.steps], seconds=time.perf_counter() - start,
                            stage="exec", **meta))
        if progress:
            progress(i + 1, len(questions), runs[-1])
    return runs


def _print_progress(n: int, total: int, run: Run) -> None:
    """실행 중 진행 표시 (정답과 무관한 정보만: 순번·상태·도구·시간)."""
    tools = ", ".join(c["tool"] for c in run.calls) or "-"
    print(f"  [{n}/{total}] {run.id:<6} {run.status:<11} {tools} ({run.seconds:.1f}s)", flush=True)


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
    for path in (SAMPLE_EXPECTED, DEMO_EXPECTED):
        specs.update({a["id"]: a["checks"] for a in json.loads(path.read_text(encoding="utf-8"))["answers"]})
    report = []
    for run in runs:
        rules = specs.get(run.same_as or run.id, [])
        if run.set == "refuse":  # 데이터에 없는 것을 물음: 숫자를 내면 오답
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
        report.append({"id": run.id, "set": run.set, "question": run.question, "pass": ok, "status": run.status,
                       "calls": run.calls, "reason": "; ".join(reasons), "seconds": round(run.seconds, 1),
                       "llm_calls": run.llm_calls, "model": run.model})
    return report


def summary_md(report: list[dict]) -> str:
    """제출용 측정 표 (정확도·시간·LLM 호출 수)."""
    lines = ["| 묶음 | 정답 | 질문당 시간 (평균 / 최대) | LLM 호출 (합계 / 질문당 최대) |", "|---|---|---|---|"]
    for s in SETS:
        part = [r for r in report if r["set"] == s]
        if not part:
            continue
        sec = [r["seconds"] for r in part]
        calls = [r["llm_calls"] for r in part]
        lines.append(f"| {s} | {sum(r['pass'] for r in part)}/{len(part)} | {sum(sec) / len(sec):.1f}초 / {max(sec):.1f}초 "
                     f"| {sum(calls)}회 / {max(calls)}회 |")
    models = sorted({r["model"] for r in report if r["model"]})
    lines += ["", f"모델: {', '.join(models) or '-'} · 시간은 질문 1개의 계획(LLM)+실행(엔진) 전체",
              "", "| ID | 질문 | 결과 | 도구 | 판정 |", "|---|---|---|---|---|"]
    for r in report:
        tools = ", ".join(c["tool"] for c in r["calls"]) or "-"
        reason = r["reason"].replace("|", "/").replace("\n", " ")[:80]
        lines.append(f"| {r['id']} | {r['question']} | {'O' if r['pass'] else 'X'} | {tools} | {reason} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 채점 (실제 Gemini 호출)")
    ap.add_argument("--only", nargs="*", help="이 ID만 실행 (예: Q1 Q4 S4)")
    ap.add_argument("--delay", type=float, default=2.0, help="질문 사이 대기 초 (무료 한도 보호)")
    ap.add_argument("--out", help="결과를 JSON으로 저장할 경로")
    ap.add_argument("--set", default="core", choices=[*SETS, "all"], help="질문 묶음 (기본 core = 발표용 점수)")
    ap.add_argument("--md", help="측정 표(정확도·시간·호출 수)를 마크다운으로 저장할 경로")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    sets = SETS if args.set == "all" else (args.set,)
    questions = [q for q in load_questions(sets) if not args.only or q.id in args.only]
    print(f"질문 {len(questions)}개 실행 중 (질문 사이 {args.delay:g}초 대기, 끝난 뒤 한꺼번에 채점)...", flush=True)
    runs = run_questions(questions, load_datasets(), delay=args.delay, progress=_print_progress)  # 1단계: 정답 모름
    print()
    report = grade(runs)  # 2단계: 정답 비교

    for r in report:
        tools = ", ".join(c["tool"] for c in r["calls"]) or "-"
        print(f"{'O' if r['pass'] else 'X'} {r['id']:<3} {r['question']}\n    도구: {tools} | {r['reason']} ({r['seconds']}s)")
    passed = sum(r["pass"] for r in report)
    print()
    for s in sets:
        part = [r for r in report if r["set"] == s]
        if part:
            print(f"{s}: 정답 {sum(r['pass'] for r in part)}/{len(part)}")
    print(f"전체: 정답 {passed}/{len(report)} · LLM 호출 {sum(r['llm_calls'] for r in report)}회")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(summary_md(report), encoding="utf-8")
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
