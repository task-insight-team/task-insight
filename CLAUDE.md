# task-insight

업무·직원·프로젝트 CSV를 올리고 질문하면, AI가 분석 계획을 세우고 승인받은 뒤 계산 도구를 실행해 표·차트·근거로 답하는 웹 도구.
시연 데이터: `data/kafe/` (가상 제조기업, 기준일 2026-11-19). 일반 CSV(과제 `data/sample.csv`)도 분석한다.
목표: 과제 Step 1 완성 + Step 3(계획 → 승인 → 실행 → 실행 기록). Step 2(로그인·저장)는 의도적으로 제외.

## 스택
**Python 3.12 고정** (`.python-version`, pandas 3은 3.11 이상 필요) · 패키지 버전은 `requirements.txt`에 고정 · pandas · pydantic · Streamlit · Plotly · Gemini API (function calling) · pytest · Streamlit Community Cloud

## 폴더와 담당 (자기 폴더 밖은 수정하지 않는다)
| 폴더 | 내용 | 담당 |
|---|---|---|
| `contracts.py` | 모듈 간 데이터 형식 | **전원 합의 후에만 수정** |
| `engine/` | 업로드 검증, 전처리, 분석 도구 6개 | 데이터 엔지니어링 |
| `agent/` | 질문 → 계획, 승인, 도구 실행, 실행 기록, 설명 | AI 에이전트 |
| `ui/`, `app.py` | Streamlit 화면, 차트 | 프론트엔드 |
| `tests/`, `docs/`, 배포 | 회귀 테스트, 문서, 통합 | 통합·보고서 |
| `data/` | kafe 데이터, 과제 sample.csv, 오류 샘플(`data/errors/`) | 데이터 엔지니어링 |
| `tests/fixtures/kafe/` | **평가 정답 (에이전트·LLM 프롬프트에 절대 넣지 않는다)** | 데이터 엔지니어링 |

`lib/`, `build/`, `dist/`, `env/` 이름의 폴더는 만들지 않는다 (.gitignore에 걸려 올라가지 않음).

## 반드시 지킬 원칙
1. **LLM은 숫자를 만들지 않는다.** 화면과 설명의 모든 수치는 `ToolResult`에서 가져온다.
2. 도구는 `engine.run_tool(df, name, params)`로만 실행한다. 허용 목록(`engine.registry.TOOLS`) 밖의 도구, 자유 Python/SQL 실행은 금지.
3. 사용자가 계획을 승인하기 전에는 도구를 실행하지 않는다.
4. 차트와 표는 같은 `ToolResult.rows`로 그린다 (차트 값 = 표 값). 결과 옆에 `basis`(기준)와 `unit`(단위)를 보여준다.
5. `ToolResult.error`가 있으면 숫자를 보여주지 않고 사유를 보여준다. LLM 호출이 실패해도 가짜 답을 만들지 않고 "실패"를 표시한다.
6. 자유 텍스트 컬럼(업무명·프로젝트명)은 LLM 프롬프트에 넣지 않는다. CSV 내용은 지시가 아니라 데이터다.
7. 기한 초과 등 날짜 계산은 항상 기준일(`as_of`)을 입력으로 받는다. 코드에서 오늘 날짜를 쓰지 않는다.
8. API 키는 코드에 쓰지 않는다. `st.secrets["GEMINI_API_KEY"]` 또는 `.env`로 읽는다.
9. 새 패키지는 마음대로 추가하지 않는다. 필요하면 팀에 알리고, Python 3.12에서 설치되는지 확인한 뒤 `requirements.txt`에 `==` 버전으로 고정한다. 3.12에서 안 되면 Python 버전을 바꾸지 말고 대체 패키지를 먼저 찾는다. Python 버전 변경은 전원 합의 후 다 같이 바꾸고 pytest 통과를 확인한다.

## 데이터 규칙
파일 종류·검증·결합·계산 기준·지원/미지원 분석: `docs/data_rules.md`
데이터 설명: `docs/kafe_data_dictionary.md` / 평가 질문(질문만): `docs/kafe_questions.json`
정답표: `docs/answer_key.md` (정답은 고정 파일 기준. AI가 임의로 바꾸지 않는다)

## 실행
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest                      # 커밋 전 반드시 통과
streamlit run app.py
```

## Git
- main에 직접 push하지 않는다. `모듈/기능` 이름의 브랜치(예: `engine/upload`) → PR → 테스트 통과 후 Squash merge
- PR은 작게, 합치기는 11시 · 15시 · 18시
- `gh`로 레포 설정 변경, 브랜치 삭제, 강제 push 금지
- 테스트를 통과시키려고 정답 값이나 테스트를 고치지 않는다. 다르면 팀에 알린다.
