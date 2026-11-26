# Task Insight

업무·직원·프로젝트 CSV를 올리고 질문하면, AI가 **분석 계획을 세우고 → 사용자가 승인하면 → 검증된 계산 도구를 실행해** 표·차트·근거로 답하는 웹 도구입니다. 여러 CSV를 합칠 때 생기는 수치 부풀림과, LLM이 숫자를 지어내는 문제를 막는 데 초점을 둡니다.

> [배포 앱 열기](https://task-insight-vojhzjrnxn7gnm7gkb8rgn.streamlit.app/) · [소개 홈페이지](https://task-insight-vwm7.netlify.app/) · [개발 문서](DevelopDoc/PRD.md) · [MIT 라이선스](LICENSE) · [제3자 자료 안내](NOTICE.md)

바이브 코딩 해커톤 팀 프로젝트입니다. 시연 데이터(`data/kafe/`의 (가상) 한빛센서텍, 기준일 2026-11-19)는 모두 가상 데이터입니다.

## 배포 URL

| 용도 | 주소 |
|---|---|
| 분석 앱 (Streamlit Community Cloud) | https://task-insight-vojhzjrnxn7gnm7gkb8rgn.streamlit.app/ |
| 소개 홈페이지 (Netlify) | https://task-insight-vwm7.netlify.app/ |

- 접속이 없으면 앱이 잠들어, 처음 열 때 30초~1분 걸립니다. 발표·심사 전에 한 번 열어 깨워 두세요.
- 로그인은 없습니다 (과제 Step 2 의도적 제외). 화면에서 예시 데이터를 고르거나 CSV를 올립니다.
- 시연 질문은 화면의 예시 질문 버튼을 쓰면 됩니다. Gemini API 키는 배포 환경의 Secrets에만 있고 이 저장소에는 없습니다.
- 소개 홈페이지(`ui/homepage/`, 정적 파일)는 Netlify에 배포되어 있고, 위 분석 앱을 iframe으로 포함합니다. 로컬에서는 `ui/homepage/index.html`을 브라우저로 열어도 됩니다.

## 왜 만들었나

업무 표에 프로젝트 예산을 붙여서 합하면 실제 예산 합계 7.03억 원이 109.03억 원(15.5배)으로 부풀려집니다. LLM이 직접 계산하면 그럴듯한 가짜 숫자가 나옵니다. Task Insight는 **LLM에게 계산을 맡기지 않고**, 정합성 규칙을 내장한 계산 도구를 AI가 고르게만 합니다.

## 핵심 원칙

1. **LLM은 숫자를 만들지 않습니다.** 화면의 모든 수치는 계산 도구의 결과(`ToolResult`)입니다.
2. **승인 전에는 실행하지 않습니다.** 질문 → 계획 → 승인 → 실행. 승인 후 계획이 바뀌면 다시 승인을 받습니다.
3. **허용된 도구 10개만 실행합니다.** 자유 Python/SQL 실행은 없습니다.
4. **표와 차트는 같은 값입니다.** 기준(basis)과 단위(unit)를 함께 보여줍니다.
5. **계산할 수 없으면 숫자 대신 사유를 보여줍니다.** LLM 호출이 실패해도 가짜 답을 만들지 않습니다.
6. **업무명·프로젝트명 같은 자유 텍스트는 LLM에 보내지 않습니다.** CSV 내용은 지시가 아니라 데이터입니다.

## 핵심 기능

| 기능 | 내용 |
|---|---|
| 업로드 검증 | 인코딩(UTF-8·CP949), 필수 컬럼, 날짜·숫자 형식, 중복, 결측을 검사하고 문제 행 번호를 보여줌 |
| 자동 결합 | 업무 → 직원·프로젝트 다대일 결합. 결합 후 행 수가 달라지면 결과를 쓰지 않고, 예산은 업무 행에 붙이지 않음 |
| 분석 계획·승인 | 질문에 맞는 도구와 조건을 Gemini(function calling)가 고르고, 사용자가 승인해야 실행 |
| 분석 도구 10개 | 집계, 비율, 기간 비교, 기한 초과, 범주별 요약, 완료율, 반복 문제, MH 초과, 예산 대비 비용, 누락 데이터 |
| 일반 CSV | 구분자·날짜·숫자 표기 자동 정리, 다른 업종 CSV도 분석 (예시 데이터 포함) |
| 결과 표시 | 표·차트·근거·설명문과 실행 기록 |
| 한계 안내 | 원인 추정, 예측, 주차별 과부하 등 지원하지 않는 질문은 이유와 함께 거절 |

전체 규칙: [docs/data_rules.md](docs/data_rules.md)

## 시작하기 (팀원용)

> API 키는 팀 비공개 채널로 받습니다. **키를 저장소·이슈·AI 대화에 붙여 넣지 마세요.** `.streamlit/secrets.toml`은 `.gitignore`에 들어 있습니다.

1. Python 3.13.2 확인 (`.python-version`)
   ```powershell
   py -3.13 --version
   ```
2. 내려받고 가상환경 만들기
   ```powershell
   git clone https://github.com/task-insight-team/task-insight.git
   ```
   ```powershell
   cd task-insight; py -3.13 -m venv .venv; .venv\Scripts\activate
   ```
3. 패키지 설치 (버전은 `==`로 고정)
   ```powershell
   pip install -r requirements.txt
   ```
4. 테스트 확인 (Gemini 키 없이 통과해야 함)
   ```powershell
   pytest
   ```
5. Gemini 키를 환경변수 `GEMINI_API_KEY` 또는 `.streamlit/secrets.toml`에 넣고 실행
   ```powershell
   streamlit run app.py
   ```
   브라우저에서 `http://localhost:8501` → 예시 데이터 선택 → 질문 입력 → 계획 확인 → [승인하고 실행]
6. 내 폴더 확인: [CLAUDE.md](CLAUDE.md)의 폴더·담당 표. 자기 폴더 밖은 수정하지 않습니다.
7. 브랜치 `모듈/기능` → 작은 PR → 테스트 통과 → Squash merge. main에는 직접 push하지 않습니다.
8. AI 코딩 도구에 맡길 때는 [CLAUDE.md](CLAUDE.md)를 읽게 하고, 정답 파일(`tests/fixtures/kafe/`)은 주지 않습니다.

## 자주 쓰는 명령

| 명령 | 내용 |
|---|---|
| `streamlit run app.py` | 로컬 실행 |
| `pytest` | 전체 테스트 (정답 고정 파일 포함, live Gemini 테스트는 키가 없으면 건너뜀) |
| `python -m agent "기한이 지난 미완료 업무는 몇 건인가?"` | 터미널에서 계획 → 승인 → 실행 확인 (Gemini 키 필요) |
| `python -m eval.run_eval --set demo` | 발표 시연 질문 5개를 실제 에이전트로 채점 (Gemini 키 필요) |
| `python -m eval.run_eval --set all --out eval_all.json --md eval_all.md` | 전체 질문 묶음 채점 |
| `python -m eval.run_eval --set demo --repeat 3` | 같은 질문을 반복해 점수 범위 측정 |

`eval`은 Gemini 호출 횟수와 무료 한도가 있습니다. 묶음별 설명과 호출 수는 [eval/README.md](eval/README.md)를 보세요.

## 문서

| 문서 | 내용 |
|---|---|
| [DevelopDoc/PRD.md](DevelopDoc/PRD.md) | 제출용: 문제 · 목표 · 사용자 · 기능 요구사항 · 리스크 · 마일스톤 |
| [DevelopDoc/TECH_SPEC.md](DevelopDoc/TECH_SPEC.md) | 제출용: 아키텍처 · 모듈 · 안전 설계 · 테스트 · 배포 |
| [DevelopDoc/WORK_UNITS.md](DevelopDoc/WORK_UNITS.md) | 제출용: 작업 단위별 완료 조건 체크리스트 |
| [DevelopDoc/FINAL_CHECKLIST.md](DevelopDoc/FINAL_CHECKLIST.md) | 제출용: 최종 완료 체크리스트 |
| [CLAUDE.md](CLAUDE.md) | 팀 규칙 · 폴더 담당 · 지킬 원칙 |
| [docs/data_rules.md](docs/data_rules.md) | 전처리 규칙 · 계산 기준 · 지원/미지원 분석 |
| [docs/kafe_data_dictionary.md](docs/kafe_data_dictionary.md) | 시연 데이터 설명 |
| [docs/answer_key.md](docs/answer_key.md) | 평가 질문 정답표 |
| [docs/presentation_prep.md](docs/presentation_prep.md) | 발표 준비 (뼈대 · 시연 정답표) |
| [docs/presentation/Task_Insight_발표자료.pdf](docs/presentation/Task_Insight_발표자료.pdf) | 발표 자료 최종본 (PDF) |
| [docs/benchmark/README.md](docs/benchmark/README.md) | 오픈소스 벤치마킹 분석 |
| [docs/plan_day1.md](docs/plan_day1.md) | Day 1 기획안 (킥오프 기록) |
| [docs/initial_plan_review.md](docs/initial_plan_review.md) | 초기 기획안과 결과물 비교 (Discussion 포함) |
| [eval/README.md](eval/README.md) | 에이전트 채점 |
| [agent/README.md](agent/README.md) · [engine/CLAUDE.md](engine/CLAUDE.md) | 에이전트 · 엔진 사용법 |

## 팀

프로젝트 시작 때 정한 역할 분담입니다 (실명 대신 역할과 GitHub 계정으로 적습니다).

| 역할 | GitHub | 맡은 부분 |
|---|---|---|
| AI 에이전트 (팀장) | docie-rgb | 질문 해석, 도구 선택, AI 출력 검증 (`agent/`) |
| 데이터 엔지니어링 | zslayer-dev | CSV 업로드, 전처리, 분석 함수 (`engine/`, `data/`) |
| 프론트엔드·시각화 | jhwksh | Streamlit 화면, Plotly 그래프, 질문 입력 UI (`ui/`, `app.py`) |
| 통합·보고서 | victorymankang-dot | 결과 연결, 보고서, 테스트, 배포 (`tests/`, `docs/`) |

실제 구현 중에는 폴더 단위 담당으로 정착했고, 화면·평가·문서는 서로 나눠 작업한 PR도 있습니다 (커밋 이력은 `git shortlog`).

## 제출 정보

- 저장소: https://github.com/task-insight-team/task-insight
- 배포 URL: 위 [배포 URL](#배포-url)
- 테스트 방법: 위 [시작하기](#시작하기-팀원용) 4단계 `pytest`와 [자주 쓰는 명령](#자주-쓰는-명령). 배포 앱은 URL에서 예시 질문 버튼으로 확인
- 제출 커밋: (제출 시 기록)
- 외부 코드 · 데이터 · 모델의 출처와 라이선스:
  - 의존 패키지: `requirements.txt`에 버전을 고정한 pandas, numpy, pydantic, pytest, streamlit, plotly, google-genai. 각 패키지의 라이선스를 따릅니다. 소개 홈페이지는 CDN에서 Pretendard, GSAP, Lenis, Three.js를 불러옵니다.
  - 데이터: `data/kafe/`, `data/general/`, `data/errors/`는 팀이 만든 가상 데이터입니다. `data/sample.csv`는 해커톤 과제로 받은 자료입니다.
  - LLM: Gemini API (기본 모델 `gemini-3.5-flash-lite`, `GEMINI_MODEL`로 변경). 키는 배포 환경 Secrets와 각자 로컬에만 있습니다.
  - 일부 코드는 AI 도구(Claude Code)와 함께 작성했습니다.
  - 이 저장소의 라이선스: MIT — 아래 [라이선스](#라이선스), 루트 [LICENSE](LICENSE)

## 라이선스

코드와 문서는 [MIT 라이선스](LICENSE)입니다 (Copyright (c) 2026 task-insight-team contributors). 각자 포크해 수정·공개·상업적으로 사용할 수 있고, 저작권 표시와 라이선스 문구만 남기면 됩니다.

다음은 MIT 적용 대상이 아닙니다.

| 대상 | 이유 |
|---|---|
| `data/sample.csv` | 해커톤 과제로 받은 자료 (권리는 주최 측). 재배포 전 삭제하거나 주최 측 조건 확인 |
| `ui/homepage/images/` | 출처 확인 전까지 제외 |
| `requirements.txt`의 패키지, 홈페이지가 CDN으로 불러오는 폰트·스크립트 (Pretendard, three.js, Lenis, GSAP) | 각자의 라이선스를 따름 |

자세한 내용은 [NOTICE.md](NOTICE.md)에 있습니다. `data/kafe/`(가상 제조기업 데이터), `data/general/`, `data/errors/`는 팀이 만든 가상 데이터라 MIT에 포함됩니다.

## 가상 데이터 고지

`data/kafe/`의 기업·직원·프로젝트·수치는 해커톤용으로 만든 **합성 데이터**입니다. 실존 기업·인물과 관계가 없고 개인정보를 담지 않았습니다. 홈페이지 이미지도 AI로 만든 가상 이미지입니다.
