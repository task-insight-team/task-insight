# WORK_UNITS — 작업 단위 명세

작성일: 2026-11-25 · 근거: [README](../README.md), [CLAUDE.md](../CLAUDE.md), [docs/data_rules.md](../docs/data_rules.md) · 상태: **초안 — 제출 전 최종 점검에서 갱신**

> 체크 표시는 2026-11-25 기준 저장소 상태(코드·테스트·문서)로 확인된 것만 `[x]`로 했다. `[ ]`는 아직 확인하지 못했거나 사람이 직접 해야 하는 항목이다.
> 담당 영역은 `CLAUDE.md`의 폴더 담당표를 따른다. 자기 폴더 밖은 수정하지 않는다.

## 역할 배분 (프로젝트 시작 때 정함)

| 역할 | GitHub | 주요 개발 내용 | 관련 작업 단위 |
|---|---|---|---|
| AI 에이전트 (팀장) | docie-rgb | 질문 해석, 도구 선택, AI 출력 검증 | WU-4 |
| 데이터 엔지니어링 | zslayer-dev | CSV 업로드, 전처리, 분석 함수 | WU-2, WU-3 |
| 프론트엔드·시각화 | jhwksh | Streamlit 화면, Plotly 그래프, 질문 입력 UI | WU-5 |
| 통합·보고서 | victorymankang-dot | 결과 연결, 보고서, 테스트, 배포 | WU-6, WU-7 |

`contracts.py`(WU-1)는 전원 합의 후에만 수정한다. 인터페이스를 첫날 합의하는 것을 병렬 개발의 기반으로 삼았고, 엔진 도구가 늘 때마다(#38, #40) 에이전트 연동 PR(#39, #42)이 따라갔다.

## 7일 일정 (초기 계획)과 진행

| 일차 | 초기 목표 | 결과 |
|---|---|---|
| 1 | 기획 확정: 고객, 데이터 구조, 질문 5개, 함수·응답 형식 정의 | `plan_day1.md`, `contracts.py`, kafe 데이터와 정답 |
| 2 | 데이터 처리: CSV 입력, 필수 컬럼 검사, 통계 함수 | 완료 (WU-2, WU-3) |
| 3 | AI 에이전트: 자연어 질문 → 분석 함수 호출 | 완료 (WU-4), 승인 단계 추가 |
| 4 | UI·시각화: 지표 카드, 그래프, 원본 데이터 확인 | 완료 (WU-5) |
| 5 | 통합: 질문부터 결과 출력까지 연결 | 완료 (WU-4, WU-5, WU-6) |
| 6 | 검증: 사전 정답 질문 10~20개로 정확도·도구 선택 검증 | 약 97문항 6개 묶음 (엔진 테스트 + `eval/`) |
| 7 | 배포·발표: 공개용 가상 데이터, 실제 동작 시연 | 배포 완료, 발표 준비 중 (WU-7) |

일정 날짜의 근거는 [docs/plan_day1.md](../docs/plan_day1.md)와 [docs/initial_plan_review.md](../docs/initial_plan_review.md)다.

## 공통 완료 조건 (모든 작업 단위)

- [ ] `pytest` 통과 (최근 2026-11-25 최신 main: 565 passed, 11 skipped)
- [ ] 작은 PR, 테스트 통과 후 Squash merge
- [ ] 정답 값·테스트를 고쳐서 통과시키지 않았다
- [ ] 새 패키지는 팀에 알리고 3.13.2에서 설치를 확인해 `==`로 고정했다

---

## WU-1. 데이터 계약 (`contracts.py`)

담당: 전원 합의 / 산출: 모듈 간 데이터 형식

- [x] Params 모델, `ToolResult`, `AnalysisPlan`, `PlanApproval`, 검증·결합 리포트 형식이 정의되어 있다
- [x] 상수(10MB, 10만 행, 소수 둘째 자리 반올림, 기준 날짜 형식)가 한 곳에 있다
- [x] 변경은 팀 합의 후에만 한다 (예: 범용성 묶음 PR에서 contracts 변경 명시)

## WU-2. 데이터 엔지니어링 — 업로드·검증·결합 (`engine/loader.py`, `data/`)

담당: 데이터 엔지니어링

- [x] 파일 종류(업무·직원·프로젝트·일반) 자동 인식
- [x] 빈 파일·헤더만·크기·행 수·인코딩·중복 헤더·필수 컬럼 누락은 거부하고 이유를 반환한다
- [x] 형식 오류 행은 행 번호와 함께 경고하고 계산에서 제외한다
- [x] 담당자·기한 빈칸은 "확인 필요"로, 구조적 공백은 정상으로 구분한다
- [x] 업무 → 직원·프로젝트 다대일 결합, 결합 후 행 수 검증, 예산 비결합 (7.03억 → 109.03억 부풀림 방지)
- [x] 일반 CSV: 구분자 자동 인식, 날짜·숫자 표기 정리
- [x] 오류 샘플 8종(`data/errors/`)과 회귀 테스트
- [x] 원본 파일·DataFrame을 수정하지 않는다
- [x] 표 구조를 바꾸면 `DATASET_VERSION`을 올린다

## WU-3. 데이터 엔지니어링 — 분석 도구 10개 (`engine/tools.py`, `engine/registry.py`)

담당: 데이터 엔지니어링

- [x] 도구 10개가 `registry.TOOLS`에 등록되어 있고 `run_tool`로만 실행된다
- [x] 도구는 예외를 던지지 않고 `ToolResult.error`에 한국어 사유를 담는다
- [x] 날짜 계산은 기준일 입력만 사용한다
- [x] kafe Q1~Q7이 고정 정답과 일치한다 (`tests/test_kafe_answers.py`). 2026-11-24 pandas 별도 계산으로 재확인
- [x] sample.csv S1~S5가 정답과 일치한다
- [x] 없는 대상(부서·ID)은 오류, 0건은 정상 0, 분모 0은 계산 불가로 구분한다
- [x] 월별 묶음, 비율, 컬럼끼리 비교, 곱한 값 집계(`multiply_by`), `percent=false` 지원
- [x] `docs/data_rules.md`에 규칙이 반영되어 있다
- [ ] 정답표 Q2·Q4의 사람 엑셀 확인 (결합 필요해 미검증으로 표기됨)

## WU-4. AI 에이전트 — 계획·승인·실행 (`agent/`)

담당: AI 에이전트

- [x] 질문 → `PlanResponse` (plan·clarify·unsupported·error)
- [x] LLM은 도구와 인자만 고르고 숫자는 `ToolResult`에서 가져온다
- [x] 승인 전 실행 없음, 승인 후 계획이 바뀌면 재승인 (`plan_hash`)
- [x] 질문 조건(부서·프로젝트·직원·상태·기한·기간·지표)이 계획에 없으면 해당 단계 제외, 1회 재계획 후 `unsupported`
- [x] 없는 컬럼·지표, 미지원 연산은 다른 분석으로 대체하지 않고 사전 안내
- [x] 계산 의도 검사 (곱셈, ×100 없는 나눗셈 등)
- [x] 자유 텍스트 컬럼을 프롬프트에 넣지 않는다
- [x] LLM 실패 시 가짜 답 없이 "실패" 표시
- [x] 오프라인 에이전트 테스트(`agent/tests/`) 통과
- [ ] 2026-11-25 기준 실제 Gemini로 `eval --set core`, `--set demo`를 돌려 결과를 기록했다 (이 저장소에는 채점 결과 파일이 없음)

## WU-5. 프론트엔드 — 화면 (`app.py`, `ui/`)

담당: 프론트엔드

- [x] 예시 데이터 선택, CSV 업로드, 파일별 검증 결과·미리보기, 결합 전후 행 수 표시
- [x] 질문 입력 → 계획 표시 → [승인하고 실행] → 결과 흐름
- [x] 표와 차트가 같은 `ToolResult.rows`를 쓰고 기준·단위를 함께 보여준다
- [x] 모든 결과에 그림 자동 표시 (숫자 카드·막대·선·원)
- [x] `error`가 있으면 숫자 대신 사유를 보여준다
- [x] 배포 중 코드 재로드 뒤에도 오래된 Dataset이 남지 않는다 (캐시 방식, 코드 지문)
- [x] 발표용 예시 질문 5개 버튼
- [x] 소개 홈페이지(`ui/homepage`)와 배포 앱 iframe 연결, 발표 모드 키 입력

## WU-6. 통합·보고서 — 테스트·문서·배포·평가 (`tests/`, `docs/`, `eval/`, `.github/`)

담당: 통합·보고서

- [x] 회귀 테스트: 정답, 결합, 검증, 안전, 필터, 범용 CSV, Gemini 스키마
- [x] CI: PR·main push마다 pytest, 비밀값 없이 실행
- [x] 정답 파일(`tests/fixtures/kafe/`)을 에이전트 쪽에 노출하지 않는다
- [x] 데이터 규칙·데이터 설명·정답표 문서
- [x] 평가 채점 도구(`eval/`)와 질문 묶음 (core·paraphrase·refuse·demo·general·holdout)
- [x] 배포 URL이 있다: 분석 앱(Streamlit Community Cloud) https://task-insight-vojhzjrnxn7gnm7gkb8rgn.streamlit.app/, 소개 홈페이지(Netlify) https://task-insight-vwm7.netlify.app/ (README 배포 URL 절)
- [x] 개발 문서(README, PRD, TECH_SPEC, WORK_UNITS, FINAL_CHECKLIST)
- [x] MIT 라이선스와 예외 대상(sample.csv, 홈페이지 이미지, 외부 패키지)을 README에 표기
- [ ] 발표 직전 배포 앱 동작 확인 (예시 질문 5개)

## WU-7. 발표 준비 (`docs/presentation_prep.md`, `ui/homepage/`)

담당: 전원

- [x] 발표 뼈대와 시연 정답표, Claude 요청문을 `docs/presentation_prep.md`에 정리
- [x] 시연 정답 Q1~Q7을 CSV에서 pandas로 재계산해 정답표와 일치 확인 (2026-11-24)
- [ ] 5분 대본과 예상 질문 정리
- [ ] 슬라이드 제작 (화면 캡처 자리 포함)
- [ ] Gemini 장애 대비 로컬 백업 캡처
- [ ] 발표 리허설
