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
from agent.guard import (MAX_STEPS, Rejected, check_question_targets, extract_conditions, fill_ratio_denominator,
                         AGG_FN, generic_mismatch, intent_check,
                         generic_unsupported,
                         missing_concepts, plan_missing, question_columns,
                         visible_columns,
                         step_scope_missing, suggest_filters, validate_call)
from agent.llm import GeminiClient, LLMClient, LLMError
from agent.prompts import AGENT_TOOLS, CLARIFY_TOOL, LIMIT_TOOL, SYSTEM_PROMPT, scope_note, user_prompt
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
    "calculate_ratio": "비율 계산",  # 엔진 PR #38
}
AGG_OP = {"sum": "합계", "mean": "평균", "count": "개수", "max": "최댓값", "min": "최솟값"}  # max·min: 엔진 PR #32


def _purpose(tool: str, p: dict) -> str:
    text = PURPOSE.get(tool, tool)
    if tool == "aggregate":
        what = f"{show(p['value_column'])} " if p.get("value_column") else ""
        text = f"{what}{AGG_OP.get(p.get('op'), '')} 집계".strip()
        if p.get("multiply_by") and p.get("value_column"):  # 곱셈: 계산식과 사용 컬럼을 그대로 보여 준다
            fn = AGG_FN.get(p.get("op"), str(p.get("op")).upper())
            expr = f"{show(p['value_column'])} × {show(p['multiply_by'])}"
            text = (f"{show(p['value_column'])}×{show(p['multiply_by'])} {AGG_OP.get(p.get('op'), '')} 집계 "
                    f"[계산식: {fn}({expr}), 행마다 {expr}를 계산한 뒤 {AGG_OP.get(p.get('op'), '')}, ×100 없음]")
    if tool == "calculate_ratio":  # 승인 전에 비율 종류·분자·분모가 보이게
        kind, num, den = p.get("kind"), p.get("value_column"), p.get("denominator_column")
        if kind == "share":
            text = f"{show(num) if num else '건수'} 비중 계산 [그룹 합계 ÷ 전체 합계]"
        elif kind == "column_ratio" and p.get("percent") is False:  # ×100 하지 않는 나눗셈 (단가 등)
            text = (f"나눗셈 계산 [계산식: SUM({show(num)}) ÷ SUM({show(den)}), ×100 하지 않음, "
                    f"행별 값의 평균 아님]")
        elif kind == "column_ratio":
            text = f"비율 계산 [비율 = {show(num)} 합계 ÷ {show(den)} 합계 × 100]"
        else:
            cond = " / ".join(f"{show(f['column'])} {f['op']} {f.get('value')}" for f in p.get("condition") or [])
            text = f"조건 비율 계산 [조건({cond})에 맞는 건수 ÷ 전체 건수]"
    groups = p.get("group_by")
    groups = groups if isinstance(groups, list) else ([groups] if groups else [])
    if groups:
        month = p.get("period") == "month"
        text += f" ({'·'.join(show(g) + ('(월)' if month else '') for g in groups)}별)"
        if month:
            text += f" [월별 집계: 날짜 {'·'.join(show(g) for g in groups)}을(를) 연-월로 묶음]"
    if p.get("department"):  # 범위 인자를 승인 전 계획에서도 보이게 (결과 설명의 [부서 X]와 같은 정보)
        text += f" (부서 {p['department']})"
    if p.get("project_id"):
        text += f" (프로젝트 {p['project_id']})"
    for f in p.get("filters") or []:
        if f.get("compare_column"):  # 컬럼끼리 비교: 두 컬럼과 연산자를 그대로 보여 준다
            text += f" [조건: {show(f['column'])} {f['op']} {show(f['compare_column'])} (같은 행의 두 컬럼 비교)]"
        else:
            text += f" [조건: {show(f['column'])} {f['op']} {f.get('value') if f.get('value') is not None else ''}]".replace(" ]", "]")
    return text


def _chart(tool: str, p: dict, roles: dict | None = None) -> str:
    grouped = bool(p.get("group_by"))
    groups = p.get("group_by") if isinstance(p.get("group_by"), list) else [p.get("group_by")]
    if tool in ("aggregate", "calculate_ratio") and (p.get("period") == "month" or (
            roles and any(roles.get(g) in ("date_day", "date_month") for g in groups if g))):
        return "line"  # 날짜 순서 묶음 → 선그래프
    if tool == "calculate_ratio":
        return "bar" if grouped else "table"
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
    missing_targets = check_question_targets(question, vocab)
    if missing_targets:  # 없는 대상은 LLM에 묻기 전에 바로 되묻는다 (비슷한 값으로 바꿔 답하지 않음)
        return PlanResponse(status="clarify", question=question, as_of=as_of, rejected=missing_targets,
                            message="질문에 나온 대상을 데이터에서 찾을 수 없습니다. " + " ".join(missing_targets))
    lacking = missing_concepts(question, vocab)
    if lacking:  # 질문에 필요한 컬럼이 데이터에 없으면 LLM을 부르지 않고, 다른 분석으로 대신하지도 않는다
        cols = ", ".join(visible_columns(vocab)) or "(표시할 수 있는 컬럼 없음)"
        return PlanResponse(status="unsupported", question=question, as_of=as_of, rejected=[f"필요한 정보 없음: {', '.join(lacking)}"],
                            message=(f"현재 데이터에는 {cols} 컬럼만 있어 '{', '.join(lacking)}' 정보가 필요한 분석을 할 수 없습니다. "
                                     "다른 분석으로 대신하지 않습니다. 해당 컬럼이 있는 데이터를 올리거나 질문을 바꿔 주세요."))
    qc = question_columns(question, vocab)
    cols_text = ", ".join(visible_columns(vocab)) or "(표시할 수 있는 컬럼 없음)"
    if qc.missing_terms:  # 질문이 요구한 정보(고객·상품·연봉 등)의 컬럼이 없음 → 다른 컬럼으로 대신하지 않음
        return PlanResponse(status="unsupported", question=question, as_of=as_of,
                            rejected=[f"필요한 정보 없음: {', '.join(qc.missing_terms)}"],
                            message=(f"현재 데이터에는 '{', '.join(qc.missing_terms)}' 정보(컬럼)가 없어 이 분석을 할 수 없습니다. "
                                     f"사용할 수 있는 컬럼: {cols_text}. 다른 분석으로 대신하지 않습니다."))
    not_yet = generic_unsupported(question, vocab, qc)
    if not_yet:  # 현재 엔진에 없는 연산 → 비슷한 다른 계산으로 대신하지 않고 안내 (LLM 호출 없음)
        return PlanResponse(status="unsupported", question=question, as_of=as_of, rejected=not_yet,
                            message=("이 질문에는 현재 분석 도구로 정확히 계산할 수 없는 부분이 있습니다: " + "; ".join(not_yet)
                                     + f". 다른 계산으로 대신하지 않습니다. 사용할 수 있는 컬럼: {cols_text}."))
    if qc.unknown_groups:  # 'X별'의 X가 어느 컬럼인지 확실하지 않음 → 추측하지 않고 되묻기
        return PlanResponse(status="clarify", question=question, as_of=as_of,
                            rejected=[f"집계 기준 불명확: {', '.join(qc.unknown_groups)}"],
                            message=(f"'{', '.join(qc.unknown_groups)}' 기준에 해당하는 컬럼을 찾을 수 없습니다. "
                                     f"사용할 수 있는 컬럼({cols_text}) 중 어느 것으로 나눌지 알려 주세요."))
    conditions = extract_conditions(question, vocab)
    base_prompt = user_prompt(render_context(ds, vocab), question)
    try:
        llm = llm or GeminiClient()
        reply = llm.generate(SYSTEM_PROMPT, base_prompt, gemini_tool_declarations() + AGENT_TOOLS)
    except LLMError as exc:
        return _fail(question, as_of, exc.user_message, getattr(llm, "model", "") if llm else "")
    except Exception:  # noqa: BLE001 - 어떤 경우에도 가짜 답 대신 실패를 알린다
        return _fail(question, as_of, LLMError.MESSAGES["unknown"], getattr(llm, "model", "") if llm else "")
    model = getattr(llm, "model", "")

    built = _build(reply, vocab, conditions, qc, question)
    if built["uncovered"] or (not built["steps"] and built["hints"]):
        # 질문 조건이 빠졌거나 계산 의도·필수 인자가 틀린 단계가 있으면 이유를 알려 주고 1번만 다시 계획하게 한다
        feedback = f"{base_prompt}\n\n[이전 계획 검토 결과] "
        if built["uncovered"]:
            labels = ", ".join(c.label for c in built["uncovered"])
            feedback += (f"질문의 조건({labels})이 분석 인자에 반영되지 않았다. "
                         "모든 조건을 aggregate의 filters(또는 cost_vs_budget의 project_id 등)로 반드시 반영하라. "
                         f"aggregate를 쓴다면 filters 예: {json.dumps(suggest_filters(conditions, as_of), ensure_ascii=False)}. "
                         f"{scope_note()} ")
        if built["hints"]:
            feedback += ("다음 문제로 이전 계획을 실행할 수 없다: " + "; ".join(built["hints"][:4])
                         + ". 질문이 요구한 계산(합계·비율·비교·월별)과 필요한 인자를 모두 채워 다시 계획하라. ")
        feedback += "조건을 반영할 수 있는 도구가 없으면 전체 결과를 내지 말고 note_limitation으로 이유를 밝혀라."
        try:
            retry = _build(llm.generate(SYSTEM_PROMPT, feedback, gemini_tool_declarations() + AGENT_TOOLS), vocab, conditions, qc, question)
            if retry["steps"] or not built["steps"]:
                built = retry
        except Exception:  # noqa: BLE001 - 재시도 실패 시 첫 결과(조건 누락 단계는 이미 제외됨)를 쓴다
            pass

    steps, rejected, notices, clarify = built["steps"], built["rejected"], built["notices"], built["clarify"]
    if steps and built["limited"] and vocab.kind != "standard":
        # 일반 CSV에서 AI가 '답할 수 없다'고 하면서 다른 분석을 붙인 경우: 대체 분석을 내보내지 않는다
        cols = ", ".join(visible_columns(vocab)) or "(표시할 수 있는 컬럼 없음)"
        return PlanResponse(status="unsupported", question=question, as_of=as_of, notices=notices, model=model,
                            rejected=[f"{s.purpose}: 질문과 다른 대체 분석이라 제외" for s in steps],
                            message=(" ".join(notices) + f"\n현재 데이터의 컬럼: {cols}. 다른 분석으로 대신하지 않습니다."))
    if steps:
        notices = notices + [f"계획에서 제외한 분석: {r}" for r in rejected]
        summary = f"다음 {len(steps)}단계 분석을 실행합니다: " + " / ".join(s.purpose for s in steps)
        summary += "".join(f"\n※ {n}" for n in notices)
        plan = AnalysisPlan(question=question, steps=steps, summary=summary)
        return PlanResponse(status="plan", question=question, as_of=as_of, plan=plan, notices=notices,
                            rejected=rejected, model=model, message="아래 분석 계획을 확인하고 승인해 주세요.")
    if built["uncovered"]:
        labels = ", ".join(c.label for c in built["uncovered"])
        return PlanResponse(status="unsupported", question=question, as_of=as_of, notices=notices, rejected=rejected,
                            model=model, message=(f"질문의 조건({labels})을 반영해 계산할 수 있는 분석을 만들지 못했습니다. "
                                                  "조건을 뺀 전체 결과는 임의로 보여 드리지 않습니다. "
                                                  "조건별 개수(업무 수)는 계산할 수 있으니 질문을 바꿔 보시거나, "
                                                  "이 조건이 필요한 분석은 데이터 엔진 개선 후 지원됩니다."))
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


def _build(reply, vocab, conditions, qc=None, question: str = "") -> dict:
    """LLM 응답 → 검증된 단계.

    - 단계마다: 질문의 범위 조건(부서·프로젝트·직원·상태·기간)을 종류별로 하나 이상 반영해야 한다 (아니면 제외).
    - 계획 전체: 모든 조건(지표·기한 초과 포함)을 어느 단계든 반영해야 한다 (아니면 계획 전체를 내보내지 않음).
    """
    calls = [c for c in reply.calls if isinstance(c, dict) and isinstance(c.get("name"), str)]
    clarify = [c for c in calls if c["name"] == CLARIFY_TOOL]
    limits = [c for c in calls if c["name"] == LIMIT_TOOL]
    tool_calls = [c for c in calls if c["name"] not in (CLARIFY_TOOL, LIMIT_TOOL)]

    notices: list[str] = []
    for c in limits:
        msg = _safe_text((c.get("args") or {}).get("reason"), "이 질문의 일부는 현재 데이터로 판단하거나 예측할 수 없습니다.")
        if msg not in notices:
            notices.append(msg)

    steps, rejected, seen, uncovered, hints = [], [], set(), [], []
    for c in tool_calls:
        try:
            params, notes = validate_call(c["name"], c.get("args") or {}, vocab)
        except Rejected as exc:
            rejected.append(str(exc))
            continue
        if c["name"] == "calculate_ratio":  # 분모가 빠졌고 후보가 하나뿐이면 채워서 승인 화면에 분모를 명시 (여럿이면 되묻기)
            params = fill_ratio_denominator(params, vocab)
        mismatch = generic_mismatch(c["name"], params, qc, vocab) if qc is not None else []
        hints += [m for m in mismatch if m.startswith("질문의 묶음 기준") and m not in hints]  # 빠진 'X별'은 재계획으로 고칠 수 있음
        if qc is not None:  # 계산 의도(합계↔비율, 비교 대상, 월별)가 질문과 같은지
            wrong, intent_notes = intent_check(c["name"], params, question, qc, vocab)
            mismatch += wrong
            hints += [w for w in wrong if w not in hints]  # 재계획 때 무엇을 고쳐야 하는지 알려 준다
            notes = notes + [n for n in intent_notes if n not in notes]
        if mismatch:  # 질문에 없는 컬럼으로 조용히 바꾼 계획은 실행 가능한 계획으로 내보내지 않는다
            rejected.append(f"{_purpose(c['name'], params)}: " + "; ".join(mismatch))
            continue
        lacking = step_scope_missing(c["name"], params, conditions, vocab)
        if lacking:
            uncovered += [x for x in lacking if x not in uncovered]
            rejected.append(f"{_purpose(c['name'], params)}: 질문 조건({', '.join(x.label for x in lacking)})이 "
                            "반영되지 않아 제외했습니다.")
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
                              purpose=_purpose(c["name"], params), chart_type=_chart(c["name"], params, vocab.roles)))
    unmet = plan_missing(steps, conditions, vocab) if steps else []
    if unmet:  # 질문이 요구한 지표·범위를 계획 전체가 다루지 못하면 승인 대상으로 내보내지 않는다
        uncovered += [x for x in unmet if x not in uncovered]
        rejected += [f"{s.purpose}: 질문 조건({', '.join(x.label for x in unmet)})을 계획이 다루지 않아 제외했습니다."
                     for s in steps]
        steps = []
    return {"steps": steps, "rejected": rejected, "notices": notices, "clarify": clarify, "uncovered": uncovered,
            "limited": bool(limits), "hints": hints}
