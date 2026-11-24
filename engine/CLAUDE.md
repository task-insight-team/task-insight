# engine/ 규칙

데이터 엔진: 업로드 검증·결합·전처리(`loader.py`), 분석 도구 10개(`tools.py`), 도구 목록과 실행 진입점(`registry.py`).

## 다른 모듈이 쓰는 방법
```python
from engine import load_dataset, run_tool, tool_schemas

# Streamlit: files = {f.name: f.getvalue() for f in st.file_uploader(..., accept_multiple_files=True)}
res = load_dataset(files)            # LoadResult
res.reports                          # 파일별 ValidationReport (화면에 오류·경고·행 번호 표시)
res.join                             # JoinReport (결합 전후 행 수)
res.errors                           # 파일 조합 문제 (같은 종류 2개 등)
if res.ok:
    ds = res.dataset                 # ds.main: 결합된 업무 표, ds.employees, ds.projects
    result = run_tool(ds, "count_overdue_tasks", {"as_of": "2026-11-19", "group_by": "부서"})
    if result.ok:
        result.rows, result.value, result.unit, result.basis
    else:
        result.error                 # 숫자 대신 이 사유를 보여준다

tool_schemas()                       # 표준 JSON Schema ($defs·$ref 포함 가능)

# Gemini function calling: 이걸 쓴다 ($ref를 펼치고 Optional을 nullable로 바꾼 형식)
from google.genai import types
from engine import gemini_tool_declarations
tool = types.Tool(function_declarations=gemini_tool_declarations())
config = types.GenerateContentConfig(tools=[tool])
# 모델이 고른 function_call의 name·args를 그대로 run_tool(ds, name, dict(args))에 넘긴다 (검증은 엔진이 함)
```
`aggregate` 필터의 `in` 값은 목록(`["P01", "P05"]`)과 쉼표 문자열(`"P01,P05"`)을 모두 받습니다. 숫자·날짜 비교 값도 문자열로 보내면 엔진이 컬럼 형식에 맞춰 바꿉니다.
`aggregate`·`calculate_ratio`는 `period="month"`로 group_by의 날짜 컬럼을 월별로 묶습니다. 필터에 `compare_column`을 주면 다른 컬럼과 비교합니다 (예: `{"column": "수량", "op": "<", "compare_column": "안전재고"}`).
`calculate_ratio`(비율 %)는 `kind`로 `share`(그룹별 비중)·`column_ratio`(합계÷합계)·`condition_rate`(조건에 맞는 행 비율)를 고릅니다.
`count_overdue_tasks`·`summarize_by_category`·`calculate_completion_rate`는 `department`(예: `"품질"`, `"품질팀"`도 됨)·`project_id`(예: `"P05"`)로 범위를 좁힐 수 있습니다 (생략하면 전체).
데이터에 없는 대상(없는 부서·오타 ID)은 `error`에 가능한 값과 함께 안내하고, 대상은 맞는데 0건이면 정상 결과 0(목록은 빈 행)입니다. 평균·완료율처럼 분모가 0이면 "0건이라 계산할 수 없습니다" 오류입니다.
업무명·프로젝트명은 결과 rows에 없습니다. 화면에 이름이 필요하면 `ds.main`/`ds.projects`에서 ID로 찾습니다 (LLM에는 보내지 않음).

## 지킬 것
- 도구는 예외를 던지지 않는다. 계산할 수 없으면 `ToolResult.error`에 한국어 사유.
- 컬럼 이름은 영어와 한국어(`contracts.DISPLAY_NAMES`)를 모두 받는다. 없는 컬럼이면 실행하지 않고 사용 가능한 컬럼을 안내한다.
- 형식 오류 행(`_invalid`)은 계산에서 제외하고 `used_rows`에 실제 사용 행 수를 넣는다. 담당자·기한 빈칸은 오류가 아니라 '확인 필요'라 제외하지 않는다.
- 예산은 업무 행에 붙이지 않는다 (중복 합산 방지). 결합 후 행 수가 바뀌면 결합 결과를 쓰지 않는다.
- `rows`의 첫 키는 범주(차트 x축), 나머지는 숫자. 순위형 도구는 rows[0]이 1위. 숫자는 `r()`로 반올림.
- 자유 텍스트(업무명·프로젝트명)는 결과 rows에 넣지 않는다.
- 날짜 계산에 오늘 날짜를 쓰지 않는다. 기준일은 입력으로 받는다.
- 원본 DataFrame을 수정하지 않는다.
- `load_dataset`·`build_dataset`가 만드는 표 구조(파생 컬럼, 결합 방식, 컬럼 형식)를 바꾸면 `engine/loader.py`의 `DATASET_VERSION`을 1 올린다. `run_tool`은 버전이 다른 Dataset(앱 업데이트 전에 불러와 캐시에 남은 데이터 등)으로는 계산하지 않고 "다시 불러와 주세요"를 돌려준다. Dataset 여부는 클래스가 아니라 속성으로 판단한다 (배포 중 모듈 재로드 대응).
- 새 도구 추가: `contracts.py`에 Params 모델(팀 합의) → `tools.py` 함수 → `registry.TOOLS` 등록 → `docs/data_rules.md`와 테스트 추가.

## 테스트
- `tests/test_kafe_answers.py`: kafe 평가 질문 Q1~Q7 (고정 정답 파일과 비교)
- `tests/test_sample_answers.py`: 과제 sample.csv 정답
- `tests/test_join.py`: 결합 행 수, 예산 중복 합산 방지, 키 중복
- `tests/test_validation.py`, `tests/test_safety.py`
- 테스트가 실패하면 정답을 고치지 말고 원인을 찾는다.
