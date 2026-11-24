"""Task Insight 화면: 계획 -> 승인 -> 결과.

* 예시(kafe) 데이터 또는 사용자가 올린 CSV를 engine.load_dataset으로 검증·결합한다.
* 승인 전에는 분석 도구가 실행되지 않는다.
* 표와 차트는 같은 ToolResult.rows로 그린다.
"""
import html as _html
import json
import math
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agent import approve_plan, create_plan, execute_plan
from engine import Dataset, load_dataset, read_table

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


ROOT = Path(__file__).parent


def code_version() -> str:
    """엔진·에이전트 코드의 지문. 배포로 코드가 바뀌면 값이 달라져, 오래된 캐시·계획을 버리는 기준이 된다."""
    parts = [ROOT / "contracts.py", *sorted((ROOT / "engine").glob("*.py")), *sorted((ROOT / "agent").glob("*.py"))]
    return "|".join(f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in parts if p.exists())


# st.cache_resource는 만든 객체를 그대로 보관해서, 배포 후 모듈이 다시 로드돼도 옛 클래스의 Dataset이 남는다.
# st.cache_data는 꺼낼 때마다 현재 코드의 클래스로 복원하고, code_ver가 바뀌면 새로 만든다.
@st.cache_data(show_spinner="파일을 검사하는 중...", max_entries=4)
def _load_files(files: tuple, drop_duplicates: bool, code_ver: str):
    """files = ((파일이름, bytes), ...). 같은 파일·같은 코드면 다시 검사하지 않는다. 검증·결합은 engine이 한다."""
    res = load_dataset(dict(files), drop_duplicates=drop_duplicates)
    previews = {}  # 파일별 앞 5줄 미리보기 (읽은 그대로, 내부용 '_' 컬럼은 뺀다)
    for name, content in files:
        df, _ = read_table(content, name)
        if df is not None:
            previews[name] = df[[c for c in df.columns if not str(c).startswith("_")]].head(5)
    return res, previews


def load_files(files: tuple, drop_duplicates: bool):
    """항상 '현재 코드의' Dataset을 돌려준다. 오래된 객체가 섞여 있으면 캐시를 비우고 다시 읽는다."""
    ver = code_version()
    res, previews = _load_files(files, drop_duplicates, ver)
    if res.ok and not isinstance(res.dataset, Dataset):
        _load_files.clear()
        res, previews = _load_files(files, drop_duplicates, ver)
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

data_key = (files, drop_dup, code_version())  # 데이터나 코드가 바뀌면 이전 계획·결과를 지운다 (옛 객체로 승인·실행하지 않도록)
if st.session_state.get("data_key") != data_key:
    st.session_state["data_key"] = data_key
    st.session_state.pop("plan_resp", None)
    st.session_state.pop("exec_resp", None)
    st.session_state.pop("cancelled", None)

st.subheader("2. 질문")
st.toggle("그림 움직임", value=True, key="motion", help="끄면 움직임 없는 그림으로 표시합니다.")

MONEY_COLS = ["예산", "실적 인건비", "실적 비인건비", "실적 총비용"]
KAFE_EXAMPLES = [  # 발표용 질문 5개. 클릭하면 질문 입력창을 채워 주는 보조 기능 (직접 입력이 기본)
    "부서별 미완료 업무 현황은?",
    "계획 대비 실적 MH 초과가 가장 큰 프로젝트는?",
    "예산 대비 비용 초과율이 가장 높은 프로젝트는?",
    "품질팀에서 기한이 지났는데 완료되지 않은 업무는 몇 건이야?",
    "담당자나 기한이 비어 있어 확인이 필요한 업무는?",
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


def num_label(v) -> str:
    return "" if pd.isna(v) else f"{v:,.2f}".rstrip("0").rstrip(".")


def motion_on() -> bool:
    """'그림 움직임' 스위치(기본 켜짐). 꺼져 있으면 움직임 없는 기존 그림을 쓴다."""
    return bool(st.session_state.get("motion", True))


def html_block(s: str):
    """<style>·<svg>가 필요해서 st.html(제거됨) 대신 markdown을 쓴다. 우리가 만든 문자열만 넣고, 값은 모두 _esc로 이스케이프한다."""
    st.markdown(s, unsafe_allow_html=True)


# 움직임은 CSS 애니메이션만 쓴다 (스크립트·외부 라이브러리 없음). 색은 currentColor라 밝은/어두운 테마에 모두 맞는다.
MOTION_CSS = """<style>
.ti-title{font-weight:600;font-size:15px;margin:.2rem 0 .2rem;opacity:.9}
.ti-svg{width:100%;height:auto;display:block;overflow:visible;margin-bottom:.8rem}
.ti-svg text{fill:currentColor;font-size:12px}
.ti-b{transform-box:view-box;transform:scaleY(0);animation:ti-grow .9s cubic-bezier(.16,1,.3,1) forwards}
.ti-v,.ti-p{opacity:0;animation:ti-fade .5s ease-out forwards}
.ti-l{stroke-dasharray:var(--len);stroke-dashoffset:var(--len);animation:ti-draw 1.3s ease-out .1s forwards}
@keyframes ti-grow{to{transform:scaleY(1)}}
@keyframes ti-fade{to{opacity:1}}
@keyframes ti-draw{to{stroke-dashoffset:0}}
@property --n{syntax:'<integer>';inherits:false;initial-value:0}
@keyframes ti-cnt{from{--n:0}}
.ti-card{padding:.3rem 0 .6rem}
.ti-card .k{font-size:14px;opacity:.75}
.ti-card .n{font-size:2.6rem;font-weight:600;line-height:1.25}
.ti-count{counter-reset:ti-n var(--n);animation:ti-cnt 1.1s ease-out}
.ti-count::after{content:counter(ti-n)}
@media (prefers-reduced-motion:reduce){.ti-b,.ti-v,.ti-p,.ti-l,.ti-count{animation:none!important;transform:none!important;opacity:1!important;stroke-dashoffset:0!important}}
</style>"""
BAR_COLOR = "#4c78ff"


def _esc(v) -> str:
    return _html.escape(str(v), quote=True)


def _nice_ticks(lo: float, hi: float, n: int = 5):
    span = (hi - lo) or 1.0
    mag = 10 ** math.floor(math.log10(span / n))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if span / step <= n:
            break
    t0, t1 = math.floor(lo / step) * step, math.ceil(hi / step) * step
    ticks, v = [], t0
    while v <= t1 + step * 1e-6:
        ticks.append(round(v, 10))
        v += step
    return ticks, t0, t1


def _frame(cats, vals, xlabel, ylabel):
    """막대·선이 함께 쓰는 틀(눈금·0선·축 이름). (그림 안쪽 좌표 함수, 눈금 SVG, 폭·높이 등)를 돌려준다."""
    W, H, L, R, T = 800, 380, 70, 20, 18
    B = 78 if len(cats) > 8 else 54
    nums = [v for v in vals if v is not None]
    ticks, t0, t1 = _nice_ticks(min([0.0] + nums), max([0.0] + nums))
    ph = H - B - T
    ymap = lambda v: T + (t1 - v) / ((t1 - t0) or 1.0) * ph
    parts = []
    for t in ticks:
        y = ymap(t)
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="currentColor" stroke-opacity=".15"/>'
                     f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" opacity=".7">{_esc(num_label(t))}</text>')
    y0 = ymap(0)
    parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y0:.1f}" y2="{y0:.1f}" stroke="currentColor" stroke-width="2" stroke-opacity=".8"/>')  # 0 기준선
    parts.append(f'<text x="16" y="{T + ph / 2:.0f}" text-anchor="middle" opacity=".7" transform="rotate(-90 16 {T + ph / 2:.0f})">{_esc(ylabel)}</text>')
    parts.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 6}" text-anchor="middle" opacity=".7">{_esc(xlabel)}</text>')
    return dict(W=W, H=H, L=L, R=R, B=B, ymap=ymap, y0=y0, grid="".join(parts), band=(W - L - R) / max(len(cats), 1))


def _cat_label(cx, f, c, n) -> str:
    c = c if len(c) <= 10 else c[:9] + "…"
    y = f["H"] - f["B"] + 16
    rot = f' transform="rotate(-35 {cx:.1f} {y})" text-anchor="end"' if n > 8 else ' text-anchor="middle"'
    return f'<text x="{cx:.1f}" y="{y}"{rot} opacity=".8">{_esc(c)}</text>'


def svg_bar(cats: list, vals: list, title: str, xlabel: str, ylabel: str) -> str:
    f, n, out = _frame(cats, vals, xlabel, ylabel), len(cats), []
    bw = min(f["band"] * 0.62, 70)
    for i, (c, v) in enumerate(zip(cats, vals)):
        cx = f["L"] + f["band"] * (i + 0.5)
        out.append(_cat_label(cx, f, c, n))
        if v is None:
            continue
        yv = f["ymap"](v)
        top, h = min(yv, f["y0"]), max(abs(yv - f["y0"]), 1)
        out.append(f'<rect class="ti-b" x="{cx - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="2" fill="{BAR_COLOR}" '
                   f'style="transform-origin:{cx:.1f}px {f["y0"]:.1f}px;animation-delay:{i * 55}ms"><title>{_esc(c)}: {_esc(num_label(v))}</title></rect>')
        ly = yv - 6 if v >= 0 else yv + 16
        out.append(f'<text class="ti-v" x="{cx:.1f}" y="{ly:.1f}" text-anchor="middle" style="animation-delay:{i * 55 + 500}ms">{_esc(num_label(v))}</text>')
    return (f'{MOTION_CSS}<div class="ti-title">{_esc(title)}</div>'
            f'<svg class="ti-svg" viewBox="0 0 {f["W"]} {f["H"]}" role="img" aria-label="{_esc(title)}">{f["grid"]}{"".join(out)}</svg>')


def svg_line(cats: list, vals: list, title: str, xlabel: str, ylabel: str) -> str:
    f, n, out = _frame(cats, vals, xlabel, ylabel), len(cats), []
    pts = [(f["L"] + f["band"] * (i + 0.5), f["ymap"](v), c, v) for i, (c, v) in enumerate(zip(cats, vals)) if v is not None]
    for i, c in enumerate(cats):
        out.append(_cat_label(f["L"] + f["band"] * (i + 0.5), f, c, n))
    if len(pts) >= 2:
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y, _, _ in pts)
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])) + 2
        out.append(f'<path class="ti-l" d="{d}" fill="none" stroke="{BAR_COLOR}" stroke-width="3" stroke-linejoin="round" style="--len:{length:.0f}"/>')
    for i, (x, y, c, v) in enumerate(pts):
        dl = 300 + int(1000 * i / max(len(pts), 1))
        out.append(f'<circle class="ti-p" cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{BAR_COLOR}" style="animation-delay:{dl}ms"><title>{_esc(c)}: {_esc(num_label(v))}</title></circle>'
                   f'<text class="ti-v" x="{x:.1f}" y="{y - 10:.1f}" text-anchor="middle" style="animation-delay:{dl + 200}ms">{_esc(num_label(v))}</text>')
    return (f'{MOTION_CSS}<div class="ti-title">{_esc(title)}</div>'
            f'<svg class="ti-svg" viewBox="0 0 {f["W"]} {f["H"]}" role="img" aria-label="{_esc(title)}">{f["grid"]}{"".join(out)}</svg>')


def _series(df: pd.DataFrame, x: str, y: str):
    vals = pd.to_numeric(df[y], errors="coerce")
    return df[x].astype(str).tolist(), [None if pd.isna(v) else float(v) for v in vals]


def big_number(label: str, value, unit: str):
    """숫자 카드. 움직임이 켜져 있고 0~9999 정수면 0에서 올라가고, 아니면 기존 st.metric."""
    num = pd.to_numeric(value, errors="coerce")
    if motion_on() and pd.notna(num) and float(num).is_integer() and 0 <= num <= 9999:
        try:
            html_block(f'{MOTION_CSS}<div class="ti-card"><div class="k">{_esc(label)}</div>'
                    f'<div class="n"><span class="ti-count" style="--n:{int(num)}"></span> {_esc(unit)}</div></div>')
            return
        except Exception:  # noqa: BLE001 - 움직임이 안 되는 환경이면 기존 카드로
            pass
    st.metric(label, f"{num_label(value)} {unit}".strip())


def draw_bar(df: pd.DataFrame, x: str, y: str, title: str, key: str):
    """막대차트: 표와 같은 rows·같은 순서. 음수는 0선 아래로, 0 기준선을 굵게, 막대 위에 값을 적는다."""
    if motion_on():  # 막대가 0에서 자라는 움직임 (실패하면 아래 기존 그림)
        try:
            html_block(svg_bar(*_series(df, x, y), title, x, y))
            return
        except Exception:  # noqa: BLE001
            pass
    vals = pd.to_numeric(df[y], errors="coerce")
    cats = df[x].astype(str).tolist()
    fig = go.Figure(go.Bar(x=cats, y=vals, text=vals.map(num_label), textposition="outside", cliponaxis=False))
    fig.add_hline(y=0, line_width=2, line_color="gray")  # 0 기준선
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=cats, title_text=x)
    fig.update_yaxes(title_text=y, zeroline=True, zerolinewidth=2, zerolinecolor="gray")  # 값 이름(단위 포함)을 축에 표시
    fig.update_layout(title_text=title, showlegend=False, margin=dict(t=60, b=40, l=10, r=10))
    st.plotly_chart(fig, width="stretch", key=key)


def mh_note(rows: list[dict]):
    """MH 초과 분석: 양의 초과분 합계와 실적-계획 순증가는 다른 값이라 함께 구분해 보여 준다 (모두 표의 값으로 계산)."""
    top = rows[0] if rows else {}
    need = ("계획MH", "실적MH", "초과MH 합계")
    if all(isinstance(top.get(k), (int, float)) for k in need):
        name = next(iter(top.values()))
        st.info(
            f"※ **초과MH 합계 {num_label(top['초과MH 합계'])}MH**는 업무별로 계획보다 더 쓴 시간(양의 초과분)만 더한 값입니다. "
            f"{name}의 **계획 {num_label(top['계획MH'])}MH → 실적 {num_label(top['실적MH'])}MH 순증가 "
            f"{num_label(top['실적MH'] - top['계획MH'])}MH**(실적−계획)와는 서로 다른 값입니다 (덜 쓴 업무가 있으면 순증가가 더 작습니다).")


def kpi_visual(tool: str, r, df: pd.DataFrame, key: str) -> bool:
    """표로만 나오던 두 결과(기한 초과 업무 목록, 빠진 정보)에 숫자 카드와 막대차트를 더한다.
    값은 모두 같은 ToolResult(rows, value)에서 가져온다. 그릴 수 없으면 False를 돌려줘 표만 보여 준다."""
    if df.empty:
        return False
    if tool == "count_overdue_tasks" and {"업무ID", "초과 일수"} <= set(df.columns):
        total = r.value if r.value is not None else len(df)
        big_number("기한이 지난 미완료 업무", total, r.unit)
        draw_bar(df, "업무ID", "초과 일수", "업무별 기한 초과 일수 — 초과 일수(일)", key)
        return True
    if tool == "check_missing_data" and {"업무ID", "컬럼"} <= set(df.columns):
        total = r.value if r.value is not None else df["업무ID"].nunique()
        big_number("확인이 필요한 업무", total, r.unit)
        by = df.groupby("컬럼", sort=False).size().rename("건수").reset_index()
        draw_bar(by, "컬럼", "건수", "빈칸 종류별 건수 — 건수(건)", key)
        return True
    return False


TIME_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")  # 2026-01 / 2026-01-05 처럼 월·날짜 순서인 첫 컬럼
SKIP_COLS = {"원본 행", "행", "비고", "순위"}  # 그림의 값으로 쓰지 않는 컬럼
PREFER = ("(%)", "(건)", "MH", "일수", "합계", "개수", "평균", "증감")  # 값 컬럼을 고를 때 먼저 보는 이름 (단, "값("으로 시작하는 나눗셈 결과는 이보다 먼저)
MAX_BARS = 30  # 한 그림에 그리는 최대 행 수 (표에는 전체가 있다)


def numeric_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns[1:] if c not in SKIP_COLS and pd.to_numeric(df[c], errors="coerce").notna().any()]


def pick_value_column(tool: str, df: pd.DataFrame):
    """그릴 값 컬럼: 도구별 주 컬럼 > 범용 합계 도구의 마지막 컬럼 > 이름 규칙 > 마지막 숫자 컬럼."""
    ok = numeric_cols(df)
    main = MAIN_VALUE.get(tool)
    if main in ok:
        return main
    if tool == "aggregate" and len(df.columns) and df.columns[-1] in ok:
        return df.columns[-1]
    for c in ok:  # 나눗셈 결과 '값(매출액÷판매수량)'이 합계(매출액)·합계(판매수량)보다 먼저 그려져야 한다
        if str(c).startswith("값("):
            return c
    for key in PREFER:
        for c in ok:
            if key in str(c):
                return c
    return ok[-1] if ok else None


def draw_line(df: pd.DataFrame, x: str, y: str, title: str, key: str):
    if motion_on():  # 선이 왼쪽부터 그려지는 움직임 (실패하면 아래 기존 그림)
        try:
            html_block(svg_line(*_series(df, x, y), title, x, y))
            return
        except Exception:  # noqa: BLE001
            pass
    vals = pd.to_numeric(df[y], errors="coerce")
    cats = df[x].astype(str).tolist()
    fig = go.Figure(go.Scatter(x=cats, y=vals, mode="lines+markers+text", text=vals.map(num_label),
                               textposition="top center", cliponaxis=False))
    if vals.min() < 0:
        fig.add_hline(y=0, line_width=2, line_color="gray")  # 음수가 있으면 0 기준선
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=cats, title_text=x)
    fig.update_yaxes(title_text=y)
    fig.update_layout(title_text=title, showlegend=False, margin=dict(t=60, b=40, l=10, r=10))
    st.plotly_chart(fig, width="stretch", key=key)


def draw_pie(df: pd.DataFrame, x: str, y: str, title: str, key: str):
    vals = pd.to_numeric(df[y], errors="coerce")
    fig = go.Figure(go.Pie(labels=df[x].astype(str).tolist(), values=vals, sort=False, textinfo="label+value+percent"))
    fig.update_layout(title_text=f"{title}", margin=dict(t=60, b=20, l=10, r=10))
    st.plotly_chart(fig, width="stretch", key=key)


def auto_visual(s, r, df: pd.DataFrame, key: str) -> bool:
    """어떤 결과든 숫자가 있으면 그림으로 보여 준다 (한 줄=숫자 카드, 월·날짜 순서=선, 그 밖=막대, 계획이 원을 고르면 원).
    값은 모두 같은 ToolResult(value, rows)에서 가져온다. 숫자가 없으면 False를 돌려줘 표만 보여 준다."""
    if df.empty or len(df.columns) == 0:
        return False
    cols = numeric_cols(df)
    if len(df) == 1:  # 한 줄 결과: 숫자 카드
        if r.value is not None:
            big_number(s.purpose, r.value, r.unit)
            return True
        shown = cols[:4]
        if not shown:
            return False
        for col, c in zip(st.columns(len(shown)), shown):
            col.metric(str(c), num_label(pd.to_numeric(df[c].iloc[0], errors="coerce")))
        return True
    y = pick_value_column(s.tool, df)
    x = df.columns[0]
    if y is None or y == x:
        return False
    part = df.head(MAX_BARS)
    title = f"{s.purpose} — {y}"
    vals = pd.to_numeric(part[y], errors="coerce")
    if part[x].astype(str).map(lambda v: bool(TIME_RE.match(v))).all():
        draw_line(part, x, y, title, key)  # 월·날짜 순서
    elif s.chart_type == "pie" and 2 <= len(part) <= 10 and (vals.dropna() > 0).all() and vals.notna().any():
        draw_pie(part, x, y, title, key)
    else:
        draw_bar(part, x, y, title, key)
    if len(df) > MAX_BARS:
        st.caption(f"행이 많아 위쪽 {MAX_BARS}개만 그렸습니다 (표에는 전체가 있습니다).")
    return True


def md(text: str) -> str:
    return text.replace("\n", "  \n")  # st.markdown이 줄바꿈을 살리도록


as_of = st.text_input("기준일 (YYYY-MM-DD)", "2026-11-19")
question = st.text_input("질문", key="question", placeholder="예: 품질팀에서 마감 넘긴 업무는?")
examples = example_questions(ds)
if examples:
    st.caption("자유롭게 질문을 입력하세요. 아래 예시를 누르면 입력창에 채워집니다.")
    for ex in examples:  # 질문이 길어서 한 줄에 하나씩
        st.button(ex, key=f"ex_{ex}", width="stretch", on_click=lambda e=ex: st.session_state.update(question=e))
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
        if not kpi_visual(s.tool, r, df, key=f"kpi_{s.step_id}"):  # 기한 초과 목록·빠진 정보는 전용 카드+막대
            auto_visual(s, r, df, key=f"chart_{s.step_id}")  # 그 밖의 결과도 그릴 수 있으면 그림을 먼저 (같은 rows)
        if s.tool == "mh_overrun":
            mh_note(r.rows)
        show_table(df)  # 표
        st.caption(f"기준: {r.basis} | 단위: {r.unit} | 사용 행: {r.used_rows}")
    with st.expander("실행 기록"):
        st.dataframe(pd.DataFrame([{
            "단계": e.step_id, "도구": e.tool,
            "입력": json.dumps(e.params, ensure_ascii=False),
            "결과": "성공" if e.ok else f"실패: {e.error}",
            "사용 행": e.used_rows, "소요(ms)": e.elapsed_ms,
        } for e in out.log]), hide_index=True)
