# eval/ — 에이전트 채점

평가 질문을 **실제 에이전트**(Gemini 계획 → 승인 → 엔진 실행)로 돌리고, 끝난 뒤 정답과 비교해 "정답 ○/12"를 낸다.
엔진 테스트(`tests/test_kafe_answers.py`)는 "도구가 맞게 계산하는가"를, 이 채점은 "**에이전트가 질문을 받고 맞는 도구와 조건을 고르는가**"를 본다.

## 실행
```powershell
$env:GEMINI_API_KEY="..."                      # 키는 코드·파일·채팅에 쓰지 않는다
python -m eval.run_eval                        # kafe Q1~Q7 + 과제 sample.csv S1~S5 (Gemini 약 12~24회 호출)
python -m eval.run_eval --only Q4 S4           # 일부만
python -m eval.run_eval --out eval_result.json # 질문별 도구·인자·판정을 JSON으로 저장
```
출력 예: `O Q4 예산 대비 비용 초과율이 가장 높은 프로젝트는?  도구: cost_vs_budget | '비용 초과율(%)' 최대 P01 (30.72)`
전부 맞으면 종료 코드 0, 하나라도 틀리거나 측정 못 한 질문이 있으면 1. 질문 사이 2초 대기(`--delay`, 무료 한도 보호).

### Gemini 한도 초과 (429)
한도 초과는 에이전트 실력과 무관하므로 **오답으로 세지 않는다.**
- 질문 도중 한도 초과가 한 번이라도 나면(에이전트가 삼킨 재계획 호출 실패 포함) 그 결과를 버리고, `--quota-wait`(기본 45초) 기다린 뒤 그 질문을 처음부터 다시 돌린다. 최대 `--quota-retries`(기본 2)번.
- 그래도 한도 초과면 결과 종류 **`측정 못 함`**(표시 `-`)으로 두고 **정답률 분모에서 뺀다.** 예: `core: 정답 11/11 (측정 못 함 1)`
- 연속 3개 질문이 측정 못 함이면 일일 한도로 보고 남은 질문은 실행하지 않는다 (모두 측정 못 함).
- 키 없음·서버 오류 같은 다른 오류는 다시 돌리지 않고 지금처럼 `오류`(오답)로 센다.
- 재시도한 질문의 시간·LLM 호출 수는 마지막 실행 기준이고, 재시도 횟수는 `--out` JSON의 `retries`에 남는다.
- LLM 호출 전에 사전 차단되는 질문(데이터에 없는 정보를 묻는 질문 등)은 한도와 무관하게 원래대로 채점한다.
```powershell
python -m eval.run_eval --set general --quota-wait 60 --quota-retries 3   # 무료 키로 반복 측정할 때
```

## 질문 묶음 (`--set`)
| 묶음 | 질문 | 정답 기준 |
|---|---|---|
| `core` (기본) | 평가 질문 Q1~Q7 + 과제 sample.csv S1~S5 (12개) | 정답 파일. **발표용 점수는 이 묶음** |
| `paraphrase` | 같은 질문을 사용자 말투로 바꿔 물은 것 (`eval/extra_questions.json`) | 원래 질문(`same_as`)의 정답 규칙 그대로 |
| `refuse` | 데이터에 없는 컬럼·지표를 묻는 질문 (예: sample.csv에 "부서별 미완료 업무") | 숫자 없이 지원 불가·되묻기로 끝나야 정답. 다른 분석을 대신 실행하면 오답 (과제 Step 1 "존재하지 않는 컬럼 질문" 기준) |
| `demo` | **발표 시연에서 그대로 입력할 질문 5개** (D1~D5) | 평가 질문과 같은 계산은 그 정답 규칙, 아니면 `eval/demo_expected.json` (D4 품질팀 기한 초과 8건: 정답 파일 Q1 업무 목록 × 직원 CSV로 계산, 테스트로 확인) |

```powershell
python -m eval.run_eval --set demo                                          # 리허설 전 시연 질문만 (Gemini 5회)
python -m eval.run_eval --set all --out eval_all.json --md eval_report.md   # 전부 (Gemini 약 35~45회)
```
시연 문장을 바꾸면 `eval/extra_questions.json`의 D1~D5도 같이 바꿔 둔다.

### 범용성 측정 (`--set general`, `all`에 포함되지 않음)
kafe·sample.csv가 아닌 **다른 업종 데이터 3종**(`data/general/`)으로 어디까지 답하고, 못 하는 것은 정직하게 거절하는지 잰다.
| 데이터 | 내용 |
|---|---|
| `sales_2026.csv` | 과제 문서의 현실 규모 데이터 (12개월 × 3지역). 정답도 과제 문서 값 |
| `inventory.csv` | 가상 재고 (3개 창고 18개 품목, 한글 컬럼, 일 단위 입고일) |
| `support_tickets.csv` | 가상 고객지원 문의 26건 (2026-10~11, 처리시간 빈칸 포함) |

질문 21개 (`eval/general_questions.json`), 정답과 종류는 `eval/general_expected.json`:
| 종류 | 의미 | 통과 |
|---|---|---|
| `answer` | 지금 도구로 답할 수 있음 (엔진으로 확인됨) | 정답 |
| `limit` | 지금 도구의 한계 (비중, 월별 묶기, 컬럼끼리 비교, 계산 컬럼 등) | 정답 **또는 정직한 거절**. 틀린 숫자는 오답 |
| `refuse` | 데이터에 없는 것 | 숫자 없이 거절 |

결과는 정답 / 정직한 거절 / 오답 / 대체 분석 / 답하지 못함 / 오류로 분류된다 (`--md` 표에 종류별 집계).
```powershell
python -m eval.run_eval --set general --out eval_general.json --md eval_general.md   # Gemini 최대 21회
```
정답 값은 테스트가 원본 CSV(pandas)로 다시 계산해 확인한다 (엔진 결과를 옮겨 적지 않음).

### 다른 구조 CSV (`--set schema`, `all`에 포함되지 않음, 이슈 #34 회귀)
컬럼 이름이 질문 낱말과 다른 CSV 4종(`data/schema/`)과 질문 25개 (`eval/schema_questions.json`, 정답 `schema_expected.json`).
- 데이터: 한글 헤더 매출(일 단위 날짜) / 영문 헤더 티켓(`category`·`agent`·`status`) / 세미콜론·`₩1,200` 재고(`warehouse`·`stock`) / 직원표형 인사(`employee_id` 없음 → 일반 CSV)
- `answer` 17개: "카테고리별", "담당자별 open", "창고별 재고"처럼 낱말과 컬럼 이름이 달라도 답해야 함
- `refuse` 8개: 고객·만족도·반품·채널·배송 지연·입고일·성별·퇴사처럼 **데이터에 없는 정보** → 다른 컬럼으로 대신 답하면 오답
- 2026-11-24 기준 main은 낱말 규칙(`agent/guard.py`) 때문에 `answer` 일부가 Gemini 호출 전에 막힌다 (이슈 #34 A 유형). 에이전트를 고친 뒤 이 묶음으로 개선과 회귀(거절해야 할 질문까지 답하게 됐는지)를 같이 본다.
- 정답은 원본 CSV를 pandas로 직접 계산했고, 테스트가 다시 계산해 확인한다. 질문은 에이전트 결과를 보기 전에 고정했다. **결과를 보고 질문·정답을 바꾸지 않는다.**
```powershell
python -m eval.run_eval --set schema --out eval_schema.json --md eval_schema.md   # Gemini 최대 25회 (재계획 포함 시 더)
```

### 처음 보는 질문 (`--set holdout`, `all`에 포함되지 않음)
`general`과 같은 데이터 3종, **겹치지 않는 질문 17개** (`eval/holdout_questions.json`, 정답 `holdout_expected.json`).
에이전트를 고친 뒤 `general` 21문제에만 맞춘 수정이 아닌지 확인하는 용도다. 2026-11-23 에이전트 수정 전에, 에이전트 결과를 보지 않고 만들었다.
**에이전트 결과를 보고 이 질문이나 정답을 바꾸지 않는다.** 바꾸면 과적합 확인 용도가 사라진다.
```powershell
python -m eval.run_eval --set holdout --out eval_holdout.json --md eval_holdout.md   # Gemini 최대 17회
```
`--md`는 제출용 측정 표(묶음별 정답 수, 질문당 시간 평균·최대, LLM 호출 수)와 질문별 결과를 저장합니다.
LLM 호출 수는 에이전트가 요청한 횟수입니다 (재계획 포함, SDK 내부 재시도 제외).

## 정답 격리 (CLAUDE.md)
| 단계 | 읽는 파일 |
|---|---|
| 1. 에이전트 실행 | 질문 문장(`docs/kafe_questions.json`, `eval/sample_questions.json`)과 데이터만 |
| 2. 채점 | 정답(`tests/fixtures/kafe/evaluation_answers.json`, `eval/sample_expected.json`) |

- 정답 파일은 1단계가 끝난 뒤에만 연다 (`tests/test_eval_grader.py`가 정답 파일 없이 1단계가 도는지 확인).
- 승인은 사람 대신 스크립트가 `approve_plan`을 호출한다 (채점 전용).
- 정답 값은 코드에 적지 않고 정답 파일에서 읽는다.

## 채점 기준
LLM이 쓴 문장은 보지 않고 결과 표(`ToolResult.rows`·`value`)만 본다. 실행한 단계 중 하나라도 맞으면 된다.

| 규칙 | 의미 | 질문 |
|---|---|---|
| value | 결과 값이 정답 (그룹별로 나눴으면 해당 컬럼 합계) | Q1, Q6, S1, S4 |
| mapping | 범주별 값이 정답과 모두 같음 (다른 범주가 0이 아닌 값으로 끼면 오답) | Q2, S2, S3 |
| extreme | **맞는 컬럼**에서 정답 ID가 **유일한** 최댓값·최솟값 (정렬 순서에 기대지 않음) | Q3(초과), Q4(초과율 ±0.5%p), Q5(완료율), Q7, S4(증감률) |
| error | 계산할 수 없다고 사유를 안내해야 정답. 숫자를 내면 오답 | S5 (2025-12 데이터 없음) |

- 되묻기·지원 불가로 멈추면 오답 (S5처럼 계산 불가 질문은 사유가 맞으면 정답). API 오류도 오답.
- 다른 방법이라도 정답을 내면 정답 (예: Q1을 상태별로 묶어도 기한 초과 합계 15).

## 결과를 쓰는 법
- 오답이 나오면 `--out`의 `calls`(도구·인자)를 보고 에이전트 담당에게 전달한다. 정답 값이나 채점 규칙을 고쳐서 맞추지 않는다.
- Gemini 답은 실행마다 조금 다를 수 있다. 발표 수치는 여러 번 돌린 결과로 말한다.
