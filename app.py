"""Task Insight 화면: 계획 -> 승인 -> 결과.

* kafe 데이터를 한 번만 불러온다 (업로드 화면이 생기면 engine.load_dataset(files)로 교체).
* 승인 전에는 분석 도구가 실행되지 않는다.
* 표와 차트는 같은 ToolResult.rows로 그린다.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

from agent import approve_plan, create_plan, execute_plan
from engine import load_dataset

# 차트 주 값 컬럼 (agent/FRONTEND.md 표와 같음)
MAIN_VALUE = {
    "count_overdue_tasks": "기한 초과(건)", "summarize_by_category": "미완료(건)",
    "calculate_completion_rate": "완료율(%)", "mh_overrun": "초과MH 합계",
    "cost_vs_budget": "비용 초과율(%)", "find_recurring_issues": "발생(건)",
}

st.set_page_config(page_title="Task Insight", layout="wide")
st.title("Task Insight")


@st.cache_resource
def load_kafe():
    folder = Path(__file__).parent / "data" / "kafe"
    res = load_dataset({f"{n}.csv": folder / f"{n}.csv" for n in ("tasks", "employees", "projects")})
    return res.dataset if res.ok else None


ds = load_kafe()
if ds is None:
    st.error("데이터를 불러오지 못했습니다.")
    st.stop()

MONEY_COLS = ["예산", "실적 인건비", "실적 비인건비", "실적 총비용"]
EXAMPLES = [  # 클릭하면 질문 입력창을 채워 주는 보조 기능 (직접 입력이 기본)
    "부서별 미완료 업무 현황은?",
    "기한이 지난 미완료 업무는 몇 건인가?",
    "비용 초과율이 가장 큰 프로젝트는?",
    "완료율이 가장 낮은 프로젝트는?",
]


def show_table(df):
    """표시용만 정리: 비어 있는 값은 '-', 금액은 천 단위 쉼표. 값 자체는 바꾸지 않는다."""
    cfg, shown = {}, df.copy()
    for c in df.columns:
        if df[c].isna().any():
            shown[c] = df[c].map(lambda v: "-" if pd.isna(v) else v).astype(str)
        elif c in MONEY_COLS:
            cfg[c] = st.column_config.NumberColumn(format="localized")
    st.dataframe(shown, hide_index=True, column_config=cfg)


def md(text: str) -> str:
    return text.replace("\n", "  \n")  # st.markdown이 줄바꿈을 살리도록


as_of = st.text_input("기준일 (YYYY-MM-DD)", "2026-11-19")
question = st.text_input("질문", key="question", placeholder="예: 품질팀에서 마감 넘긴 업무는?")
st.caption("자유롭게 질문을 입력하세요. 아래 예시를 누르면 입력창에 채워집니다.")
for col, ex in zip(st.columns(len(EXAMPLES)), EXAMPLES):
    col.button(ex, key=f"ex_{ex}", on_click=lambda e=ex: st.session_state.update(question=e))

if st.button("분석 계획 만들기", disabled=not question):
    st.session_state["plan_resp"] = create_plan(question, ds, as_of)  # 도구 실행 없음
    st.session_state.pop("exec_resp", None)

resp = st.session_state.get("plan_resp")
if resp is not None:
    if resp.status == "plan":
        st.subheader("분석 계획")
        for line in resp.plan.summary.splitlines():
            (st.warning if line.startswith("※") else st.write)(line)  # ※ = 한계·제외 안내 (꼭 표시)
        for s in resp.plan.steps:
            st.write(f"{s.id}. {s.purpose} (도구: {s.tool})")
        if st.button("승인하고 실행"):
            st.session_state["exec_resp"] = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    elif resp.status == "error":
        st.error(resp.message)
    else:  # clarify / unsupported
        st.info(md(resp.message))

out = st.session_state.get("exec_resp")
if out is not None:
    (st.success if out.status == "ok" else st.warning)(out.message)
    st.markdown(md(out.explanation))
    for s in out.steps:
        r = s.result  # contracts.ToolResult
        st.markdown(f"**{s.step_id}. {s.purpose}**")
        if not r.ok:
            st.error(r.error)  # 오류면 숫자를 보여 주지 않는다
            continue
        df = pd.DataFrame(r.rows)
        show_table(df)  # 표
        y = MAIN_VALUE.get(s.tool) or (df.columns[-1] if s.tool == "aggregate" and len(df.columns) else None)
        if s.chart_type == "bar" and y in df.columns:
            st.bar_chart(df, x=df.columns[0], y=y, sort=False)  # 차트 (같은 rows, 표와 같은 순서)
        st.caption(f"기준: {r.basis} | 단위: {r.unit} | 사용 행: {r.used_rows}")
