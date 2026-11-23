"""Task Insight 화면: 계획 -> 승인 -> 결과.

* 예시(kafe) 데이터 또는 사용자가 올린 CSV를 engine.load_dataset으로 검증·결합한다.
* 승인 전에는 분석 도구가 실행되지 않는다.
* 표와 차트는 같은 ToolResult.rows로 그린다.
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from agent import approve_plan, create_plan, execute_plan
from engine import load_dataset, read_table

# 차트 주 값 컬럼 (agent/FRONTEND.md 표와 같음)
MAIN_VALUE = {
    "count_overdue_tasks": "기한 초과(건)", "summarize_by_category": "미완료(건)",
    "calculate_completion_rate": "완료율(%)", "mh_overrun": "초과MH 합계",
    "cost_vs_budget": "비용 초과율(%)", "find_recurring_issues": "발생(건)",
    "compare_periods": "증감률(%)",
}

st.set_page_config(page_title="Task Insight", layout="wide")
st.title("Task Insight")


KAFE_DIR = Path(__file__).parent / "data" / "kafe"
KIND_NAMES = {"tasks": "업무", "employees": "직원", "projects": "프로젝트", "generic": "일반 CSV"}
SHOW_LEVEL = {"error": st.error, "warning": st.warning, "info": st.info}


@st.cache_resource(show_spinner="파일을 검사하는 중...")
def load_files(files: tuple, drop_duplicates: bool):
    """files = ((파일이름, bytes), ...). 같은 파일이면 다시 검사하지 않는다. 검증·결합은 engine이 한다."""
    res = load_dataset(dict(files), drop_duplicates=drop_duplicates)
    previews = {}  # 파일별 앞 5줄 미리보기 (읽은 그대로, 내부용 '_' 컬럼은 뺀다)
    for name, content in files:
        df, _ = read_table(content, name)
        if df is not None:
            previews[name] = df[[c for c in df.columns if not str(c).startswith("_")]].head(5)
    return res, previews


DTYPE_NAMES = {"number": "숫자", "date": "날짜", "text": "텍스트"}


def issue_text(i) -> str:
    text = i.message
    if i.rows:
        shown = ", ".join(str(r) for r in i.rows[:10])
        text += f" (행 {shown}{' 외' if i.count > 10 or len(i.rows) > 10 else ''})"
    return text


def show_load_report(res, previews):
    """파일별 검증 결과를 보여 준다. 문제가 있으면 펼쳐서 사유를 바로 보여 준다."""
    for r in res.reports:
        warn = sum(i.level == "warning" for i in r.issues)
        mark = "❌" if not r.ok else ("⚠️" if warn else "✅")
        if not r.ok and r.n_rows == 0 and r.n_cols == 0:  # 읽지 못한 파일은 종류·크기를 알 수 없다
            label = f"{mark} {r.filename} · 읽기 실패"
        else:
            label = f"{mark} {r.filename} · {KIND_NAMES.get(r.kind, r.kind)} · {r.n_rows:,}행 {r.n_cols}열"
        with st.expander(label + (f" · 확인 필요 {warn}건" if warn else ""), expanded=(not r.ok or warn > 0)):
            if not r.issues:
                st.write("문제가 발견되지 않았습니다.")
            for i in r.issues:
                SHOW_LEVEL[i.level](issue_text(i))
            if r.columns:
                st.markdown("**컬럼별 자료형**")
                st.dataframe(pd.DataFrame(
                    [{"컬럼": c.name, "자료형": DTYPE_NAMES.get(c.dtype, c.dtype), "빈칸 수": c.missing} for c in r.columns]),
                    hide_index=True)
            if r.filename in previews:
                st.markdown("**데이터 미리보기 (앞 5줄)**")
                st.dataframe(previews[r.filename], hide_index=True)
    for i in res.errors + (res.join.issues if res.join else []):
        SHOW_LEVEL[i.level](issue_text(i))


st.subheader("1. 데이터")
source = st.radio("분석할 데이터", ["예시 데이터 (kafe)", "내 CSV 올리기"], horizontal=True)
if source.startswith("예시"):
    files = tuple((f"{n}.csv", (KAFE_DIR / f"{n}.csv").read_bytes()) for n in ("tasks", "employees", "projects"))
else:
    uploads = st.file_uploader(
        "CSV 파일을 올려 주세요 (업무·직원·프로젝트 파일 각 1개, 또는 일반 CSV 1개 / 파일당 10MB 이하)",
        type="csv", accept_multiple_files=True)
    if not uploads:
        st.info("분석할 CSV 파일을 올려 주세요. 업무 파일만 올려도 분석할 수 있어요 (부서별 분석은 직원 파일, 예산 분석은 세 파일이 모두 필요).")
        st.stop()
    files = tuple((u.name, u.getvalue()) for u in uploads)
drop_dup = st.checkbox("완전히 같은 중복 행은 제거하고 분석", value=False)

load_res, previews = load_files(files, drop_dup)
show_load_report(load_res, previews)
if not load_res.ok:
    st.error("데이터를 불러오지 못했습니다. 위 안내대로 파일을 고쳐서 다시 올려 주세요.")
    st.stop()
ds = load_res.dataset

data_key = (files, drop_dup)  # 데이터가 바뀌면 이전 계획·결과를 지운다
if st.session_state.get("data_key") != data_key:
    st.session_state["data_key"] = data_key
    st.session_state.pop("plan_resp", None)
    st.session_state.pop("exec_resp", None)
    st.session_state.pop("cancelled", None)

st.subheader("2. 질문")

MONEY_COLS = ["예산", "실적 인건비", "실적 비인건비", "실적 총비용"]
KAFE_EXAMPLES = [  # 클릭하면 질문 입력창을 채워 주는 보조 기능 (직접 입력이 기본)
    "부서별 미완료 업무 현황은?",
    "기한이 지난 미완료 업무는 몇 건인가?",
    "비용 초과율이 가장 큰 프로젝트는?",
    "완료율이 가장 낮은 프로젝트는?",
]
SAMPLE_EXAMPLES = ["지역별 매출은?", "전월 대비 매출 변화와 감소 지역은?"]  # month·region·sales 컬럼이 있는 일반 CSV용


def example_questions(ds) -> list[str]:
    """데이터에 맞는 예시 질문만 돌려 준다 (맞지 않는 데이터에는 보여 주지 않는다)."""
    if ds.kind == "standard":
        return KAFE_EXAMPLES
    return SAMPLE_EXAMPLES if {"month", "region", "sales"} <= set(map(str, ds.main.columns)) else []


def show_value(v) -> str:
    return "-" if pd.isna(v) else str(v)


def show_table(df):
    """표시용만 정리: 비어 있는 값은 '-', 금액은 천 단위 쉼표. 값 자체는 바꾸지 않는다."""
    cfg, shown = {}, df.copy()
    for c in df.columns:
        if df[c].isna().any():
            nums = pd.to_numeric(df[c], errors="coerce")
            if nums.notna().sum() and (nums.dropna() % 1 == 0).all():  # 빈칸 때문에 7이 7.0으로 보이지 않게
                shown[c] = nums.map(lambda v: "-" if pd.isna(v) else str(int(v)))
            else:
                shown[c] = df[c].map(show_value)
        elif c in MONEY_COLS:
            cfg[c] = st.column_config.NumberColumn(format="localized")
    st.dataframe(shown, hide_index=True, column_config=cfg)


def md(text: str) -> str:
    return text.replace("\n", "  \n")  # st.markdown이 줄바꿈을 살리도록


as_of = st.text_input("기준일 (YYYY-MM-DD)", "2026-11-19")
question = st.text_input("질문", key="question", placeholder="예: 품질팀에서 마감 넘긴 업무는?")
examples = example_questions(ds)
if examples:
    st.caption("자유롭게 질문을 입력하세요. 아래 예시를 누르면 입력창에 채워집니다.")
    for col, ex in zip(st.columns(len(examples)), examples):
        col.button(ex, key=f"ex_{ex}", on_click=lambda e=ex: st.session_state.update(question=e))
else:
    st.caption("올린 데이터의 컬럼 이름을 넣어 자유롭게 질문해 보세요.")


def cancel_plan():
    """[취소]: 계획과 결과를 지우고 아무것도 실행하지 않는다."""
    st.session_state.pop("plan_resp", None)
    st.session_state.pop("exec_resp", None)
    st.session_state["cancelled"] = True


if st.button("분석 계획 만들기", disabled=not question):
    st.session_state["plan_resp"] = create_plan(question, ds, as_of)  # 도구 실행 없음
    st.session_state.pop("exec_resp", None)
    st.session_state.pop("cancelled", None)

if st.session_state.get("cancelled"):
    st.info("분석 계획을 취소했습니다. 아무것도 실행하지 않았습니다.")

resp = st.session_state.get("plan_resp")
if resp is not None:
    if resp.status == "plan":
        st.subheader("분석 계획")
        for line in resp.plan.summary.splitlines():
            (st.warning if line.startswith("※") else st.write)(line)  # ※ = 한계·제외 안내 (꼭 표시)
        for s in resp.plan.steps:
            st.write(f"{s.id}. {s.purpose} (도구: {s.tool})")
        ok_col, cancel_col, _ = st.columns([1, 1, 4])
        if ok_col.button("승인하고 실행"):
            st.session_state["exec_resp"] = execute_plan(ds, resp.plan, approve_plan(resp.plan))
        cancel_col.button("취소", on_click=cancel_plan)
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
    with st.expander("실행 기록"):
        st.dataframe(pd.DataFrame([{
            "단계": e.step_id, "도구": e.tool,
            "입력": json.dumps(e.params, ensure_ascii=False),
            "결과": "성공" if e.ok else f"실패: {e.error}",
            "사용 행": e.used_rows, "소요(ms)": e.elapsed_ms,
        } for e in out.log]), hide_index=True)
