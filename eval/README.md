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
전부 맞으면 종료 코드 0, 하나라도 틀리면 1. 질문 사이 2초 대기(`--delay`, 무료 한도 보호).

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
