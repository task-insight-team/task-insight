"""질문 → 분석 계획 (AnalysisPlan). 이 모듈은 도구를 실행하지 않는다."""

from __future__ import annotations

import json
import re
from typing import Optional

import pandas as pd

from contracts import AnalysisPlan, PlanStep
from engine import gemini_tool_declarations
from engine.loader import Dataset

from agent.context import build_vocabulary, render_context, show
from agent.guard import MAX_STEPS, Rejected, validate_call
from agent.llm import GeminiClient, LLMClient, LLMError
from agent.prompts import AGENT_TOOLS, CLARIFY_TOOL, LIMIT_TOOL, SYSTEM_PROMPT, user_prompt
from agent.schemas import PlanResponse

MAX_QUESTION_LEN = 500
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUPPORTED_HINT = ("지원하는 분석: 기한 초과 업무, 부서·프로젝트별 현황, 완료율, MH 초과, 예산 대비 비용, "
                  "누락 데이터, 반복 문제유형, 월별 비교, 개수·합계·평균 집계")

PURPOSE = {
    "aggregate": "조건에 맞는 업무 집계",
    "compare_periods": "두 기간 비교",
    "count_overdue_tasks": "기한이 지난 미완료 업무 확인",
    "summarize_by_category": "범주별 업무 현황(전체·완료·미완료) 확인",
    "calculate_completion_rate": "완료율 확인",
    "find_recurring_issues": "반복되는 문제유형 확인",
    "mh_overrun": "계획 대비 실적 MH 초과 확인",
    "cost_vs_budget": "예산 대비 실적 비용 확인",
    "check_missing_data": "확인이 필요한 누락 데이터 확인",
}
AGG_OP = {"sum": "합계", "mean": "평균", "count": "개수"}


def _purpose(tool: str, p: dict) -> str:
    text = PURPOSE.get(tool, tool)
    if tool == "aggregate":
        what = f"{show(p['value_column'])} " if p.get("value_column") else ""
        text = f"{what}{AGG_OP.get(p.get('op'), '')} 집계".strip()
    groups = p.get("group_by")
    groups = groups if isinstance(groups, list) else ([groups] if groups else [])
    if groups:
        text += f" ({'·'.join(show(g) for g in groups)}별)"
    if p.get("project_id"):
        text += f" (프로젝트 {p['project_id']})"
    for f in p.get("filters") or []:
        text += f" [조건: {show(f['column'])} {f['op']} {f.get('value') if f.get('value') is not None else ''}]".replace(" ]", "]")
    return text


def _chart(tool: str, p: dict) -> str:
    grouped = bool(p.get("group_by"))
    if tool in ("mh_overrun", "cost_vs_budget", "find_recurring_issues", "compare_periods"):
        return "bar"
    if tool in ("summarize_by_category", "calculate_completion_rate", "count_overdue_tasks", "aggregate"):
        return "bar" if grouped else "table"
    return "table"


def _safe_text(text: object, fallback: str) -> str:
    """LLM이 쓴 안내문. 숫자가 들어 있으면(지어낸 수치 가능성) 쓰지 않는다."""
    if not isinstance(text, str) or not text.strip() or re.search(r"\d", text):
        return fallback
    return text.strip()[:300]


def _fail(question: str, as_of: str, message: str, model: str = "", status: str = "error") -> PlanResponse:
    return PlanResponse(status=status, question=question, as_of=as_of, message=message, model=model)


def create_plan(question: str, ds: Optional[Dataset], as_of: str, llm: Optional[LLMClient] = None) -> PlanResponse:
    """사용자 질문으로 분석 계획을 만든다. 도구는 실행하지 않는다 (실행은 승인 후 execute_plan)."""
    question = (question or "").strip()
    if not question:
        return _fail(question, as_of, "질문을 입력해 주세요.")
    if len(question) > MAX_QUESTION_LEN:
        return _fail(question[:MAX_QUESTION_LEN], as_of, f"질문은 {MAX_QUESTION_LEN}자 이내로 입력해 주세요.")
    if not isinstance(as_of, str) or not DATE_RE.match(as_of) or pd.isna(pd.to_datetime(as_of, errors="coerce")):
        return _fail(question, str(as_of), "기준일은 YYYY-MM-DD 형식으로 입력해 주세요.")
    if ds is None:
        return _fail(question, as_of, "분석할 데이터를 먼저 불러와 주세요.")

    vocab = build_vocabulary(ds, as_of)
    try:
        llm = llm or GeminiClient()
        reply = llm.generate(SYSTEM_PROMPT, user_prompt(render_context(ds, vocab), question),
                             gemini_tool_declarations() + AGENT_TOOLS)
    except LLMError as exc:
        return _fail(question, as_of, exc.user_message, getattr(llm, "model", "") if llm else "")
    except Exception:  # noqa: BLE001 - 어떤 경우에도 가짜 답 대신 실패를 알린다
        return _fail(question, as_of, LLMError.MESSAGES["unknown"], getattr(llm, "model", "") if llm else "")
    model = getattr(llm, "model", "")

    calls = [c for c in reply.calls if isinstance(c, dict) and isinstance(c.get("name"), str)]
    clarify = [c for c in calls if c["name"] == CLARIFY_TOOL]
    limits = [c for c in calls if c["name"] == LIMIT_TOOL]
    tool_calls = [c for c in calls if c["name"] not in (CLARIFY_TOOL, LIMIT_TOOL)]

    notices = []
    for c in limits:
        msg = _safe_text((c.get("args") or {}).get("reason"), "이 질문의 일부는 현재 데이터로 판단하거나 예측할 수 없습니다.")
        if msg not in notices:
            notices.append(msg)

    steps, rejected, seen = [], [], set()
    for c in tool_calls:
        try:
            params, notes = validate_call(c["name"], c.get("args") or {}, vocab)
        except Rejected as exc:
            rejected.append(str(exc))
            continue
        key = (c["name"], json.dumps(params, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        if len(steps) >= MAX_STEPS:
            rejected.append(f"{c['name']}: 분석 단계는 최대 {MAX_STEPS}개까지라 제외했습니다.")
            continue
        notices += [n for n in notes if n not in notices]
        steps.append(PlanStep(id=f"s{len(steps) + 1}", tool=c["name"], params=params,
                              purpose=_purpose(c["name"], params), chart_type=_chart(c["name"], params)))

    if steps:
        notices += [f"계획에서 제외한 분석: {r}" for r in rejected]
        summary =f"다음 {len(steps)}단계 분석을 실행합니다: " + " / ".join(s.purpose for s in steps)
        summary += "".join(f"\n※ {n}" for n in notices)
        plan = AnalysisPlan(question=question, steps=steps, summary=summary)
        return PlanResponse(status="plan", question=question, as_of=as_of, plan=plan, notices=notices,
                            rejected=rejected, model=model, message="아래 분석 계획을 확인하고 승인해 주세요.")
    if clarify and not rejected:
        q = _safe_text((clarify[0].get("args") or {}).get("question"), "어떤 대상(부서·프로젝트·직원)과 기준으로 분석할지 조금 더 구체적으로 알려 주세요.")
        return PlanResponse(status="clarify", question=question, as_of=as_of, message=q, notices=notices, model=model)
    if rejected:
        return PlanResponse(status="clarify", question=question, as_of=as_of, notices=notices, rejected=rejected,
                            model=model, message="요청한 분석을 데이터에 맞게 만들 수 없습니다. " + " ".join(rejected))
    if notices:
        return PlanResponse(status="unsupported", question=question, as_of=as_of, notices=notices, model=model,
                            message=" ".join(notices) + f"\n{SUPPORTED_HINT}")
    return _fail(question, as_of, "AI가 분석 계획을 만들지 못했습니다. 질문을 바꿔 다시 시도해 주세요. " + SUPPORTED_HINT, model)
