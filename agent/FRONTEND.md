# 프론트엔드 연동 가이드 (agent)

> 아래 JSON 예시는 kafe 가상 데이터(기준일 2026-11-19)로 실제 코드를 실행해 얻은 출력이다 (rows는 2행만 남김).

## 1. 흐름 요약

```
질문 입력 ─▶ create_plan() ─▶ PlanResponse
                                 ├─ status="plan"        → 계획 표시 + [승인] [취소]
                                 │      [승인] ─▶ approve_plan() ─▶ execute_plan() ─▶ ExecutionResponse
                                 ├─ status="clarify"     → message 표시 (되묻기)
                                 ├─ status="unsupported" → message 표시 (데이터로 답할 수 없음)
                                 └─ status="error"       → message 표시 (API 키·한도·서버 오류 등)
```

**승인 전에는 데이터 분석 도구가 실행되지 않는다.** `create_plan`은 Gemini로 도구와 인자만 고르고 검증한다.
`engine.run_tool`은 `execute_plan` 안에서만 호출되며, `approve_plan`이 만든 지문(plan_hash)이 현재 계획과 같을 때만 실행된다.
계획이 승인 후 한 글자라도 바뀌면 실행하지 않고 `status="error"`, `message="...다시 확인하고 승인해 주세요."`를 돌려준다.

## 2. 함수

```python
from agent import create_plan, approve_plan, execute_plan

create_plan(question: str, ds: Dataset | None, as_of: str, llm=None) -> PlanResponse
approve_plan(plan: AnalysisPlan) -> PlanApproval          # [승인] 버튼에서만 호출
execute_plan(ds: Dataset, plan: AnalysisPlan, approval: PlanApproval) -> ExecutionResponse
```

- `ds`: `engine.load_dataset(files).dataset` (업로드 검증·결합은 engine 담당)
- `as_of`: 기준일 `"YYYY-MM-DD"`. kafe 데이터는 `"2026-11-19"`. **오늘 날짜를 넣지 말고** 화면 입력값을 넘긴다.
- `llm`: 생략하면 Gemini (`GEMINI_API_KEY` 환경변수 또는 `st.secrets`). 테스트는 `FakeLLM`.
- 세 함수 모두 예외를 던지지 않는다. 실패는 `status`와 `message`로 온다.

## 3. Streamlit 예시 (그대로 실행되는 최소 예시)

레포 루트에서 이 코드를 파일로 저장해 `streamlit run <파일>`로 실행하면 kafe 데이터로 계획 → 승인 → 결과가 동작한다.
2026-11-23 main 기준으로 실제 Gemini와 Streamlit 헤드리스 테스트(되묻기·지원 불가·API 오류·단계 오류 포함)로 확인했다.
업로드 화면이 생기면 `load_kafe()` 대신 `engine.load_dataset({f.name: f.getvalue() for f in uploaded}).dataset`를 쓴다.

```python
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


@st.cache_resource
def load_kafe():
    folder = Path("data/kafe")
    res = load_dataset({f"{n}.csv": folder / f"{n}.csv" for n in ("tasks", "employees", "projects")})
    return res.dataset if res.ok else None


ds = load_kafe()
if ds is None:
    st.error("데이터를 불러오지 못했습니다.")
    st.stop()

as_of = st.text_input("기준일 (YYYY-MM-DD)", "2026-11-19")
question = st.text_input("질문", placeholder="예: 품질팀에서 마감 넘긴 업무는?")

if st.button("분석 계획 만들기", disabled=not question):
    st.session_state["plan_resp"] = create_plan(question, ds, as_of)   # 도구 실행 없음
    st.session_state.pop("exec_resp", None)

resp = st.session_state.get("plan_resp")
if resp is not None:
    if resp.status == "plan":
        st.subheader("분석 계획")
        for line in resp.plan.summary.splitlines():
            (st.warning if line.startswith("※") else st.write)(line)   # ※ = 한계·제외 안내 (꼭 표시)
        for s in resp.plan.steps:
            st.write(f"{s.id}. {s.purpose} (도구: {s.tool})")
        if st.button("승인하고 실행"):
            st.session_state["exec_resp"] = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    elif resp.status == "error":
        st.error(resp.message)
    else:  # clarify / unsupported
        st.info(resp.message)

out = st.session_state.get("exec_resp")
if out is not None:
    (st.success if out.status == "ok" else st.warning)(out.message)
    st.write(out.explanation)
    for s in out.steps:
        r = s.result                                   # contracts.ToolResult
        st.markdown(f"**{s.step_id}. {s.purpose}**")
        if not r.ok:
            st.error(r.error)                          # 오류면 숫자를 보여 주지 않는다
            continue
        df = pd.DataFrame(r.rows)
        st.dataframe(df, hide_index=True)              # 표
        y = MAIN_VALUE.get(s.tool) or (df.columns[-1] if s.tool == "aggregate" else None)
        if s.chart_type == "bar" and y in df.columns:
            st.bar_chart(df, x=df.columns[0], y=y)     # 차트 (같은 rows)
        st.caption(f"기준: {r.basis} | 단위: {r.unit} | 사용 행: {r.used_rows}")
```

- 금액 컬럼(`예산`, `실적 총비용` 등)은 그대로 두면 `88000000`처럼 보인다. 표시할 때 천 단위 쉼표를 넣는다 (예: `st.dataframe(df, column_config={"예산": st.column_config.NumberColumn(format="localized")})`). 값 자체는 바꾸지 않는다 (차트 값 = 표 값).

## 4. 반환 형식

### PlanResponse (`agent/schemas.py`)
| 필드 | 타입 | 설명 |
|---|---|---|
| `status` | `"plan" \| "clarify" \| "unsupported" \| "error"` | |
| `question` | `str` | 사용자 질문 |
| `as_of` | `str` | 기준일 |
| `message` | `str` | 화면에 보여줄 안내문 |
| `plan` | `AnalysisPlan \| None` | `status=="plan"`일 때만. `contracts.AnalysisPlan` 그대로 |
| `notices` | `list[str]` | 한계 안내 (예측·인사평가 불가, 제외된 단계 등). `plan.summary`에도 `※ ...` 줄로 들어 있음 |
| `rejected` | `list[str]` | 검증에서 뺀 도구 호출과 사유 (실행 기록용) |
| `model` | `str` | 사용한 Gemini 모델 |

`AnalysisPlan.steps[i]` = `PlanStep(id: str, tool: str, params: dict, purpose: str, chart_type: "bar"|"line"|"pie"|"table")`

### PlanApproval
| 필드 | 타입 |
|---|---|
| `plan_hash` | `str` (계획 전체의 SHA-256) |

### ExecutionResponse
| 필드 | 타입 | 설명 |
|---|---|---|
| `status` | `"ok" \| "partial" \| "error"` | partial = 일부 단계만 계산됨 |
| `question` | `str` | |
| `message` | `str` | |
| `steps` | `list[StepResult]` | 단계별 결과 |
| `explanation` | `str` | 단계별 설명 + `※` 한계 안내를 합친 최종 설명 |
| `log` | `list[LogEntry]` | 실행 기록 (`step_id, tool, params, ok, error, used_rows, elapsed_ms`) |

`StepResult` = `step_id: str, tool: str, purpose: str, chart_type: str, result: ToolResult, explanation: str`

### ToolResult (`contracts.py`, 엔진 결과 그대로)
| 필드 | 설명 |
|---|---|
| `rows: list[dict]` | **표 = 차트 데이터.** 첫 키가 범주(x축), 나머지가 값. 순위형 도구는 `rows[0]`이 1위 |
| `value: float \| None` | 대표 숫자 (예: 기한 초과 15건). 순위형 도구는 None |
| `unit`, `basis` | 단위, 계산 기준 — 결과 옆에 표시 |
| `used_rows` | 계산에 쓴 행 수 |
| `error: str \| None` | 있으면 rows/value를 쓰지 말고 이 사유를 표시 (`result.ok == False`) |

업무명·프로젝트명은 rows에 없다. 이름이 필요하면 `ds.projects`에서 `프로젝트ID`로 찾는다 (LLM에는 보내지 않음).

도구별로 차트에 쓰기 좋은 값 컬럼:

| tool | x축(첫 키) | 주 값 컬럼 |
|---|---|---|
| count_overdue_tasks (group_by 있음) | 그룹 | `기한 초과(건)` |
| summarize_by_category | 그룹 | `미완료(건)` |
| calculate_completion_rate | 그룹 | `완료율(%)` |
| mh_overrun | 그룹 | `초과MH 합계` |
| cost_vs_budget | 프로젝트ID | `비용 초과율(%)` |
| aggregate | 그룹 | 마지막 키 (`개수(건)`, `합계(...)`, `평균(...)`) |
| find_recurring_issues | 문제유형 | `발생(건)` |
| check_missing_data / count_overdue_tasks(그룹 없음) | — | 표로만 (`chart_type="table"`) |

## 5. 반환 예시

### 성공: 계획 (`create_plan("비용 초과율이 가장 큰 프로젝트는?", ds, "2026-11-19")`)
```json
{
  "status": "plan",
  "question": "비용 초과율이 가장 큰 프로젝트는?",
  "as_of": "2026-11-19",
  "message": "아래 분석 계획을 확인하고 승인해 주세요.",
  "plan": {
    "question": "비용 초과율이 가장 큰 프로젝트는?",
    "steps": [{"id": "s1", "tool": "cost_vs_budget", "params": {"project_id": null},
               "purpose": "예산 대비 실적 비용 확인", "chart_type": "bar"}],
    "summary": "다음 1단계 분석을 실행합니다: 예산 대비 실적 비용 확인"
  },
  "notices": [], "rejected": [], "model": "gemini-3.5-flash-lite"
}
```

### 성공: 실행 (`execute_plan(ds, resp.plan, approve_plan(resp.plan))`)
```json
{
  "status": "ok",
  "message": "분석을 완료했습니다.",
  "steps": [{
    "step_id": "s1", "tool": "cost_vs_budget", "purpose": "예산 대비 실적 비용 확인", "chart_type": "bar",
    "result": {
      "tool": "cost_vs_budget", "params": {"project_id": null},
      "rows": [
        {"프로젝트ID": "P01", "예산": 88000000.0, "실적 인건비": 51996700.0, "실적 비인건비": 63040000.0,
         "실적 총비용": 115036700.0, "비용 초과율(%)": 30.72, "프로젝트 상태": "완료", "비고": ""},
        {"프로젝트ID": "P02", "예산": 38000000.0, "...": "..."}
      ],
      "value": null, "unit": "%",
      "basis": "실적 인건비=실적MH×담당자 시간당 인건비(추정), ... 기준일까지 누적 실적(완료 시점 예측 아님), 150행 사용",
      "used_rows": 150, "error": null
    },
    "explanation": "비용 초과율이 가장 높은 프로젝트는 P01로 +30.72%입니다 (예산 88,000,000원 대비 실적 115,036,700원). 예산을 초과한 프로젝트는 2개입니다: P01, P02.\n근거: ..."
  }],
  "explanation": "비용 초과율이 가장 높은 프로젝트는 P01로 +30.72%입니다 ...",
  "log": [{"step_id": "s1", "tool": "cost_vs_budget", "params": {"project_id": null}, "ok": true,
           "error": null, "used_rows": 150, "elapsed_ms": 16}]
}
```

### 되묻기 (`"인사부 미완료 업무는?"`: 없는 부서)
```json
{"status": "clarify", "plan": null,
 "message": "질문에 나온 대상을 데이터에서 찾을 수 없습니다. 부서 '인사'은(는) 데이터에 없습니다. 있는 부서: 경영지원, 연구, 영업, 제조, 품질",
 "rejected": ["부서 '인사'은(는) 데이터에 없습니다. 있는 부서: 경영지원, 연구, 영업, 제조, 품질"]}
```

### 지원 불가 (`"어떤 직원의 인사평가가 가장 좋은가?"`)
```json
{"status": "unsupported", "plan": null,
 "message": "인사평가는 데이터로 판단할 수 없습니다.\n지원하는 분석: 기한 초과 업무, 부서·프로젝트별 현황, 완료율, ...",
 "notices": ["인사평가는 데이터로 판단할 수 없습니다."]}
```
판단·예측 질문에서 관련 '현재' 통계가 있으면 `status="plan"` + `notices`(한계 안내)로 올 수도 있다. 이때 `plan.summary`의 `※` 줄을 꼭 보여 준다.

### 조건을 반영할 수 없음 (`"연구팀 직원별 완료율은?"`: 완료율 도구는 부서 조건을 받지 못함)
```json
{"status": "unsupported", "plan": null,
 "message": "질문의 조건(부서 = 연구)을 반영해 계산할 수 있는 분석을 만들지 못했습니다. 조건을 뺀 전체 결과는 임의로 보여 드리지 않습니다. ..."}
```
`unsupported` 메시지는 위처럼 코드가 만든 고정 문구일 수도 있고, Gemini가 쓴 한계 설명(숫자 없는 문장, 예: "…도구는 부서별 필터를 지원하지 않습니다")일 수도 있다. 둘 다 그대로 표시하면 된다.
질문에 적힌 부서·프로젝트·직원·상태·기한 초과·기간·지표 조건이 계획에 반영되지 않으면, 전체 결과로 대신 답하지 않는다.

### API 오류
```json
{"status": "error", "plan": null, "message": "Gemini API 사용 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."}
```
`message` 종류: API 키 없음 · 키 오류 · 사용 한도 초과 · 시간 초과 · 모델 없음 · 서버 오류 · 응답 형식 오류. 키 값은 메시지·로그에 들어가지 않는다.

## 6. 주의

- 계획 객체(`resp.plan`)는 `st.session_state`에 보관하고, 실행 때 **같은 객체**를 넘긴다. 화면에서 인자를 바꾸면 다시 승인받아야 한다.
- 무료 Gemini 키는 분당 호출 한도가 낮다. 버튼 연타·자동 재실행(입력 변경마다 create_plan 호출)을 피한다.
- 질문 하나당 Gemini 호출은 보통 1번, 조건 누락으로 재계획하면 2번이다. 실행(`execute_plan`)은 Gemini를 호출하지 않는다.
