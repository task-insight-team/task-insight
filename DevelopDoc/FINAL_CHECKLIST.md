# FINAL_CHECKLIST — 최종 완료 체크리스트

작성일: 2026-11-25 · 근거: [README](../README.md), [CLAUDE.md](../CLAUDE.md), [docs/data_rules.md](../docs/data_rules.md) · 상태: **초안 — 제출 전 최종 점검에서 갱신**

> `[x]`는 2026-11-25 기준 저장소에서 확인된 것, `[ ]`는 제출·발표 직전에 직접 확인해야 하는 것이다.

## 1. 제출물

- [x] 저장소 링크 — https://github.com/task-insight-team/task-insight
- [x] 배포 URL — https://task-insight-vojhzjrnxn7gnm7gkb8rgn.streamlit.app/ (README 배포 URL 절)
- [x] `README.md` (저장소 최상단): 서비스 소개, 배포 URL, 시작하기, 테스트 방법, 팀, 제출 정보, 라이선스
- [ ] README '제출 커밋' 칸에 제출 커밋 식별자를 기록했다
- [x] `DevelopDoc/PRD.md`
- [x] `DevelopDoc/TECH_SPEC.md`
- [x] `DevelopDoc/WORK_UNITS.md` (작업별 완료 조건 포함)
- [x] `DevelopDoc/FINAL_CHECKLIST.md`
- [ ] 문서 속 URL·수치·체크 상태가 최종 코드와 일치한다
- [ ] 변경사항이 PR로 main에 합쳐졌다 (main 직접 push 금지)

## 2. 필수 기능

- [x] CSV 업로드 후 파일별 검증 결과(오류·경고·행 번호)를 보여준다
- [x] 표준 3종 자동 결합, 결합 후 행 수 검증, 예산 중복 합산 방지
- [x] 일반 CSV 분석 (구분자·날짜·숫자 표기 정리)
- [x] 질문 → 분석 계획 → 사용자 승인 → 실행 → 실행 기록 (과제 Step 3)
- [x] 승인 전에는 도구를 실행하지 않는다
- [x] 허용 도구 10개만 실행한다 (자유 Python/SQL 없음)
- [x] 표와 차트가 같은 값이고 기준·단위를 보여준다
- [x] 계산 불가 시 숫자 대신 사유를 보여준다
- [x] 없는 컬럼·지표·미지원 분석은 거절한다
- [x] 로그인·저장(Step 2)은 의도적으로 제외했고 문서에 밝혔다

## 3. 정확성

- [x] kafe Q1~Q7이 고정 정답과 일치한다 (15건, 품질 18, P05 370MH, P01 +30.72%, P10 20%, 누락 7건, E032 9건)
- [x] 위 7개를 `data/kafe` CSV에서 pandas로 별도 재계산해 일치를 확인했다 (2026-11-24)
- [x] sample.csv S1~S5 정답 테스트 통과
- [x] 오류 샘플 8종(빈 파일, 헤더만, 필수 컬럼 누락, 중복, 잘못된 숫자, CP949, 주입 등) 처리
- [ ] 정답표 Q2·Q4 사람 엑셀 확인 (미검증으로 표기)
- [ ] 실제 Gemini로 `python -m eval.run_eval --set core`와 `--set demo` 실행, 결과 기록
- [ ] (선택) `--set general`, `--set holdout` 실행, 결과 기록

## 4. 안전·보안

- [x] LLM이 만든 숫자가 화면에 나오지 않는다 (숫자는 `ToolResult`에서)
- [x] 자유 텍스트 컬럼(업무명·프로젝트명)을 LLM 프롬프트에 넣지 않는다
- [x] 정답 파일(`tests/fixtures/kafe/`, `SCENARIOS.md`)을 에이전트에 제공하지 않는다
- [x] 날짜 계산에 오늘 날짜를 쓰지 않고 기준일을 입력으로 받는다
- [x] 원본 DataFrame을 수정하지 않는다
- [ ] `GEMINI_API_KEY`가 코드, 문서, 커밋 이력, 슬라이드, 화면 캡처 어디에도 없다
- [ ] `.streamlit/secrets.toml`이 커밋에 포함되지 않았다 (`git ls-files`로 확인)

## 5. 테스트·CI

- [x] `pytest` 통과 (2026-11-25 최신 main: 565 passed, 11 skipped, 건너뜀은 live Gemini)
- [x] CI(GitHub Actions)가 PR·main push마다 pytest를 실행한다
- [ ] 제출 직전 main 최신 커밋에서 CI가 통과(녹색)다
- [ ] 로컬 `.venv`에서 `pytest`를 다시 실행했다

## 6. 배포·시연

- [x] Streamlit Community Cloud 배포 URL이 있다
- [ ] 배포 앱을 미리 열어 깨워 두었다 (잠든 앱은 30초~1분)
- [ ] 시연 질문 5개가 배포 앱에서 정답으로 동작한다
- [ ] Gemini 503·504 등 장애 대비 로컬 백업 화면 캡처를 준비했다
- [ ] 소개 홈페이지(`ui/homepage/index.html`) 발표 모드(→, 스페이스, ←)를 확인했다
- [ ] 홈페이지의 배포 앱 iframe 주소가 현재 URL과 같다

## 7. 문서·고지

- [x] 외부 코드·데이터·모델의 출처와 라이선스를 README에 적었다 (MIT, 예외: sample.csv·홈페이지 이미지·외부 패키지)
- [x] 저장소 루트에 `LICENSE`(MIT)가 있다
- [x] 가상 데이터 고지(한빛센서텍 합성 데이터, seed 20261119)가 README와 데이터 문서에 있다
- [x] 홈페이지 이미지가 AI 생성 가상 이미지임을 밝혔다
- [x] 한계(인건비 추정, 누적값, 최대 4단계, 월 단위만, 과부하 미지원)를 문서에 밝혔다
- [ ] 발표 슬라이드에도 가상 데이터 고지와 한계를 넣었다

## 7-1. 초기 기획 반영

- [x] 초기 기획(제안 모습, 공통 데이터 항목, 에이전트 구조, 역할, 7일 일정, 최종 제안)을 PRD 11절, TECH_SPEC 12절, WORK_UNITS에 반영했다
- [x] README 팀 표를 프로젝트 시작 때 정한 역할 분담(AI 에이전트·데이터 엔지니어링·프론트엔드·통합)으로 맞췄다
- [x] 초기 기획안과 결과물 비교 문서([docs/initial_plan_review.md](../docs/initial_plan_review.md), PR #56)가 main에 병합됐다
- [ ] 발표에서 미구현·미수행 항목을 밝힌다 (능동 리스크 브리핑, 결과 간 연결 질문, 엑셀 대비 단계 수 비교)

## 8. 발표

- [ ] 5분 대본과 예상 질문(답변 포함)
- [ ] 슬라이드 (문제 → 해법 → 가드레일 → 시연 → 검증 → 한계)
- [ ] 리허설 1회 이상 (시간 측정)
- [ ] 발표자·시연자 역할 분담
