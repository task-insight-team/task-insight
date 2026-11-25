# TECH_SPEC — Task Insight

작성일: 2026-11-25 · 근거: [README](../README.md), [CLAUDE.md](../CLAUDE.md), [docs/data_rules.md](../docs/data_rules.md) · 상태: **초안 — 제출 전 최종 점검에서 갱신**

## 1. 스택

| 구분 | 선택 |
|---|---|
| 언어 | Python **3.13.2** 고정 (`.python-version`) |
| 데이터 | pandas 3.1.1, numpy 2.5.5 |
| 데이터 형식 | pydantic 2.14.3 (`contracts.py`) |
| 화면 | Streamlit 1.67.0, Plotly 7.1.0 |
| LLM | Gemini API, function calling (`google-genai` 3.6.1). 기본 모델 `gemini-3.5-flash-lite` (`GEMINI_MODEL`로 변경) |
| 테스트 | pytest 9.2.0 |
| CI | GitHub Actions (`.github/workflows/ci.yml`): PR·main push마다 `python -m pytest -q`. Gemini 키 없이 실행하고 live 테스트는 건너뜀 |
| 배포 | Streamlit Community Cloud — https://task-insight-vojhzjrnxn7gnm7gkb8rgn.streamlit.app/ |

패키지는 `requirements.txt`에 `==`로 고정하고, 이 파일에는 한글을 쓰지 않는다 (Windows pip가 cp949로 읽어 설치 실패).

## 2. 전체 구조

```
CSV 업로드/예시 데이터
      │
      ▼
engine.load_dataset ── 파일별 검증(ValidationReport) ── 결합(JoinReport) ──▶ Dataset
      │                                                                         │
질문 ──▶ agent.create_plan ──(Gemini: 도구·인자만 선택)──▶ 가드(guard) ──▶ AnalysisPlan
                                                                                │
                                              사용자 승인(PlanApproval, plan_hash)
                                                                                │
                              agent.execute_plan ──▶ engine.run_tool ──▶ ToolResult(rows, value, unit, basis, error)
                                                                                │
                                         설명문(explain, 숫자는 Python이 삽입) + 표·차트(같은 rows) + 실행 기록
```

신뢰 경계: **LLM은 도구 이름과 인자만 고른다.** 계산은 `engine`, 숫자 문장은 `agent/explain.py`가 `ToolResult`에서 만든다.

## 3. 모듈

| 경로 | 책임 |
|---|---|
| `contracts.py` | 모듈 간 데이터 형식. Params 모델, `ToolResult`, `AnalysisPlan`, 상수(10MB, 10만 행, 반올림). **전원 합의 후에만 수정** |
| `engine/loader.py` | 파일 종류 인식, 인코딩·구분자, 검증, 정리(`clean`), 결합, `Dataset` 생성. `DATASET_VERSION` 관리 |
| `engine/tools.py` | 분석 도구 10개 구현 |
| `engine/registry.py` | 허용 도구 목록(`TOOLS`), `run_tool`, `tool_schemas`, `gemini_tool_declarations` |
| `agent/planner.py` | 질문 → `PlanResponse` (plan·clarify·unsupported·error) |
| `agent/guard.py` | 호출 검증, 질문 조건 반영 검사, 미지원 사전 안내, 계산 의도 검사, 계획 지문 |
| `agent/executor.py` | `approve_plan`, `execute_plan` (승인 확인 후 도구 실행) |
| `agent/explain.py` | 결과 설명문 |
| `agent/llm.py`, `prompts.py`, `schemas.py` | Gemini 호출, 프롬프트, 응답 스키마 |
| `app.py`, `ui/` | Streamlit 화면, 차트, 발표용 홈페이지(`ui/homepage`, 정적 HTML) |
| `eval/` | 에이전트 채점 (`python -m eval.run_eval`) |

## 4. 핵심 데이터 형식 (`contracts.py`)

- `ToolResult`: `rows`(첫 키 = 범주/x축, 나머지 숫자, 순위형은 rows[0]이 1위), `value`, `unit`, `basis`, `used_rows`, `error`. **표와 차트는 같은 `rows`를 쓴다.** `error`가 있으면 숫자를 보여주지 않는다.
- `AnalysisPlan` / `PlanStep`: 단계 최대 4개, 각 단계는 목적·도구·인자·차트 종류. 서로 독립인 단계만 허용 (앞 결과에 의존하는 인자는 미지원).
- `PlanApproval`: `plan_hash`를 가진다. 승인 후 인자가 하나라도 바뀌면 `execute_plan`이 실행하지 않고 재승인을 요구한다.
- `ValidationReport` / `Issue` / `JoinReport`: 화면에 오류·경고·행 번호·결합 전후 행 수를 보여주기 위한 형식.

## 5. 분석 도구 10개 (허용 목록)

`aggregate`, `calculate_ratio`, `compare_periods`, `count_overdue_tasks`, `summarize_by_category`, `calculate_completion_rate`, `find_recurring_issues`, `mh_overrun`, `cost_vs_budget`, `check_missing_data`.
인자·필요 파일·계산 기준·미지원 목록은 [docs/data_rules.md](../docs/data_rules.md)가 기준이다. 새 도구 추가 절차: `contracts.py` Params(합의) → `tools.py` 함수 → `registry.TOOLS` 등록 → `docs/data_rules.md`와 테스트.

도구 공통 규칙: 예외를 던지지 않고 `ToolResult.error`에 한국어 사유를 담는다. 원본 DataFrame을 수정하지 않는다. 날짜는 기준일 입력으로만 계산한다. 업무명·프로젝트명은 `rows`에 넣지 않는다. 숫자는 소수 둘째 자리 사사오입으로 반올림한다.

## 6. 데이터 처리 규칙 (요약)

- 파일 종류 인식: ID 컬럼이 있고 해당 표 컬럼의 절반 이상이 있어야 표준 파일. 아니면 일반 CSV.
- 거부: 빈 파일, 헤더만, 10MB·10만 행 초과, UTF-8/CP949 아님, 헤더 중복·`_` 시작, 표준 파일 필수 컬럼 누락.
- 경고 + 계산 제외: 필수값 빈칸, 날짜·숫자·허용값 오류, 음수.
- 경고 + 유지: 담당자·기한 빈칸(`needs_check`), 기한 < 시작일 등. 구조적 공백은 정상.
- 결합: 업무 → 직원(`assignee_id`), 업무 → 프로젝트(`project_id`) 다대일. 결합 후 행 수 불일치, 직원·프로젝트 ID 중복이면 결합하지 않는다. **예산은 업무 행에 붙이지 않는다.**
- 전체 표와 코드: [docs/data_rules.md](../docs/data_rules.md)

## 7. 보안·안전 설계

| 위험 | 대응 |
|---|---|
| LLM 환각 숫자 | 숫자는 `ToolResult`에서만. LLM 실패 시 "실패" 표시 |
| 임의 코드 실행 | 허용 도구 10개 외 실행 금지, 자유 Python/SQL 없음. 호출은 `guard.validate_call`로 검증 |
| 프롬프트 주입 | 자유 텍스트 컬럼을 프롬프트에 넣지 않음. 결과 설명의 텍스트는 `_safe_text`로 정리. 샘플 `data/errors/injection.csv`로 확인 |
| 승인 우회 | 승인 전 실행 없음, `plan_hash` 불일치 시 재승인 |
| 질문 조건 누락 | 질문의 부서·프로젝트·직원·상태·기한 조건이 계획에 없으면 그 단계를 제외하고 1회 재계획, 그래도 안 되면 `unsupported` |
| 정답 유출 | `tests/fixtures/kafe/`, `SCENARIOS.md`는 에이전트·프롬프트에 넣지 않음 |
| 비밀값 | API 키는 `st.secrets["GEMINI_API_KEY"]` 또는 환경변수. 코드·문서·채팅에 쓰지 않음 |
| 오래된 캐시 | 코드 지문(`code_version`)이 바뀌면 캐시를 버리고, `DATASET_VERSION`이 다른 Dataset은 계산하지 않음 |

## 8. 테스트와 평가

| 구분 | 위치 | 확인하는 것 |
|---|---|---|
| 엔진 정답 | `tests/test_kafe_answers.py`, `test_sample_answers.py` | Q1~Q7, S1~S5 고정 정답 |
| 결합·검증·안전 | `test_join.py`, `test_validation.py`, `test_safety.py`, `test_data_integrity.py` | 예산 중복 합산 방지, 오류 샘플, 데이터 정합성 |
| 필터·범용 CSV | `test_filters.py`, `test_generic_csv.py`, `test_gemini_schemas.py` | 조건, 일반 CSV, Gemini 스키마 |
| 에이전트(오프라인) | `agent/tests/` | 가짜 LLM으로 계획·가드·조건 반영·계산 의도 검사 |
| 에이전트(live) | `agent/tests/test_live_gemini.py` | 실제 Gemini 호출. 키가 없으면 건너뜀 |
| 채점 | `eval/run_eval.py` | 실제 에이전트가 맞는 도구·조건을 고르는지. 묶음: core, paraphrase, refuse, demo, general, holdout |

최근 실행(2026-11-25, 최신 main): `565 passed, 11 skipped` (건너뜀은 live Gemini 테스트).
채점 묶음 추가분: `schema`(다른 구조 CSV 4종 `data/schema/`, 질문 25개), `--repeat N`(같은 질문 반복 측정, 점수 범위). 설명: [eval/README.md](../eval/README.md)
`holdout` 질문과 정답은 에이전트 결과를 보고 바꾸지 않는다 (과적합 확인 용도).

## 9. 실행·배포

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest
streamlit run app.py
```

- Community Cloud 배포 앱: https://task-insight-vojhzjrnxn7gnm7gkb8rgn.streamlit.app/ (잠든 앱은 첫 접속에 30초~1분)
- Gemini 키는 Cloud의 Secrets에 등록한다. 저장소에는 올리지 않는다.
- 소개 홈페이지(`ui/homepage/index.html`)는 배포 앱을 iframe으로 포함한다. 홈페이지 자체는 정적 파일이라 별도 배포가 없다.

## 10. Git 규칙

- main 직접 push 금지. `모듈/기능` 브랜치 → PR → CI 통과 → Squash merge. 합치기는 11시·15시·18시.
- `gh`로 레포 설정 변경, 브랜치 삭제, 강제 push 금지.
- `lib/`, `build/`, `dist/`, `env/` 이름의 폴더는 만들지 않는다 (`.gitignore`에 걸림).

## 11. 알려진 제약

- 한 계획은 서로 독립인 단계 최대 4개.
- 월 단위 비교만 지원 (주·분기 미지원).
- Gemini 무료 한도·503·504 가능. 모델 가용성은 계정마다 다르다 (2.5-flash는 신규 키에서 404였음, 2026-11-22 확인).
- 인건비는 현재 단가 소급 적용 추정. 비용 초과율은 기준일 누적값.

## 12. 초기 구조 대비 변경 (프로젝트 시작 시점 → 최종)

초기 구조: 사용자 질문 → LLM(질문 분석·도구 선택) → Pandas(정확한 계산) → 시각화 엔진(차트) → LLM(결과 해석).
상세 비교와 Discussion은 [docs/initial_plan_review.md](../docs/initial_plan_review.md).

| 항목 | 초기 계획 | 최종 | 판정 |
|---|---|---|---|
| 기본 구조 | 단일 LLM + 계산 도구 + 검증 로직 | 그대로. Gemini function calling이 도구·인자만 고르고 계산은 `engine.run_tool`만 | 유지 |
| 분석 도구 | 4~5개 | 엔진 도구 10개 + 에이전트 전용 2개(`ask_clarification`, `note_limitation`) | 확장 |
| 에이전트 흐름 | 질문 → LLM 계획 → 계산 → 차트 → LLM 설명 | 사전 차단 → LLM 계획 → 사후 검증·1회 재계획 → **사용자 승인(계획 지문 SHA-256)** → 실행 | 강화 |
| 결과 설명 | LLM이 계산된 숫자로 해석 | **Python 템플릿**(`agent/explain.py`, LLM 미사용) | 변경 |
| 인터페이스 계약 | `metric`·`group_by`·`period`·`chart_type`·`summary` JSON 1개 | `contracts.py`의 `AnalysisPlan`(독립 단계 최대 4개), `ToolResult` | 구체화 |
| 데이터 | 업무관리 CSV 1종 | kafe 다중 CSV 결합 + sample.csv + 일반 CSV | 확장 |
| 검증 | 정답 질문 10~20개 | 엔진 정답 테스트 + `eval/` 6개 묶음 약 97문항, `--repeat`, 한도 초과(429) 재시도 | 초과 달성 |

설계 결정 요약 (이유와 대가):
- **결과 해석에서 LLM 제외:** "계산된 숫자만 쓰라"는 프롬프트 지시는 보장되지 않는다. 템플릿은 `ToolResult` 값만 쓰므로 숫자 왜곡이 구조적으로 불가능하다. 대가는 문장이 덜 자연스럽고 여러 결과를 엮은 해석을 못 한다는 점.
- **승인 단계 추가:** Gemini가 질문 조건을 빠뜨리거나 비슷한 다른 지표를 고르는 일이 반복돼 도입했다. 사용자가 계산식을 실행 전에 본다. 대가는 클릭 한 번과 호출 지연.
- **사전 차단·사후 검증:** 데이터에 없는 정보를 묻는 질문에 LLM이 비슷한 분석으로 대신 답하는 경향을 막고 무료 호출 한도를 아낀다. 컬럼 의미 해석(#44)은 `TI_COLUMN_HINTS` 기본 OFF (정확도를 범용성보다 먼저 택함).
- **반복 측정:** 같은 질문에도 Gemini 계획이 흔들려 1회 측정이 ±1~2문제 달라져 `--repeat`로 범위를 본다.

하지 못한 것: 능동적 리스크 브리핑(파일을 올리자마자 문제를 먼저 찾아 주기), 결과 간 연결 질문(앞 결과에 따라 인자가 바뀌는 질문), 스키마 매핑 UI, 엑셀 대비 단계 수 정량 비교.
