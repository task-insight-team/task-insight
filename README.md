# Task Insight
🚀 Auto-BI & Guardrail Agent
   
데이터 정합성 가드레일을 갖춘 범용 데이터 시각화 에이전트 MVP
프로젝트 기간 / 인원: 3일 / 총 4명
목표: 어떤 다중 CSV 데이터셋을 업로드해도 구조를 자동 파악하고, 집계·단위 오류를 방어하며, 인터랙티브 차트와 리스크 브리핑을 생성하는 자율형 BI 에이전트 구현

📌 목차
MVP 핵심 컨셉 및 차별화 포인트
아키텍처 및 LLM 엔진 통합 전략
프로젝트 디렉터리 구조
역할 분담 및 3일 마일스톤
핵심 가드레일 시스템 프롬프트 초안
데모 시나리오 및 벤치마크 검증 기준
Day 1 오전 킥오프 체크리스트

1. MVP 핵심 컨셉 및 차별화 포인트
일반적인 "CSV 업로드 → 차트 생성" 에이전트는 다중 테이블을 조인할 때 수치를 부풀리거나 결측치를 오해하는 치명적인 한계가 있습니다. 이 MVP는 데이터 정합성 가드레일을 내장해 범용성과 분석 정확도를 동시에 확보합니다.
자동 스키마·관계(ER) 프로파일링: 다중 CSV 업로드 시 인코딩(utf-8-sig)과 날짜 컬럼을 자동 파싱하고, PK/FK 관계(예: tasks.project_id → projects.project_id, tasks.assignee_id → employees.employee_id)를 자동 추론합니다.
집계 오류 방지 가드레일: 1:N 관계 테이블 조인 시 상위 테이블 수치(예: projects.budget)를 단순 합산해 과대계상되는 문제(샘플 기준 15.5배 부풀려짐)를 코드 생성 단계에서 차단합니다.
지표 단위(Stock vs Flow) 방어: 누적 실적(actual_mh)과 주당 기준값(weekly_capacity_mh)처럼 단위가 다른 지표를 기간 환산 없이 단순 비교하는 오류를 방지합니다.
결측치 의미론적 분류: 조건부로 비어 있어야 정상인 구조적 공백(예: 미완료 업무의 완료일)과 확인이 필요한 이상 누락(예: 담당자·기한 미입력)을 구분해 우선 경고합니다.
능동적 리스크 브리핑: 자연어 질의응답(Plotly 차트 생성)뿐 아니라, 파일 업로드 직후 기한 초과·업무 쏠림·예산/공수 초과·반복 이슈를 스스로 탐지해 대시보드 카드로 제안합니다.

2. 아키텍처 및 LLM 엔진 통합 전략
4단계 에이전트 파이프라인
[CSV 업로드]
    │
    ▼
1. Ingestion & Profiler   ──▶ 인코딩/날짜 파싱, PK/FK 추론, 결측치 분류 (schema_profile.json)
    │
    ▼
2. Planner & Guardrail    ──▶ 질의 분석, 조인/집계 단위 설계, Stock/Flow 제약 부여
    │
    ▼
3. Code & Chart Generator ──▶ Pandas + Plotly 코드 실행 및 에러 자가 수정 (최대 2회)
    │
    ▼
4. Insight Narrator       ──▶ 핵심 수치 요약, 이상치 원인, 경고, 후속 질문 출력
Claude Code & OpenCode 백엔드 통합
별도의 코드 실행 샌드박스를 바닥부터 구축하는 대신, 두 도구가 기본 제공하는 파일 탐색·Python 실행·자가 수정(Self-Correction) 루프를 공통 인터페이스(AgentRunner)로 추상화해 연결합니다.
호출 방식:
Claude Code: claude -p "<프롬프트>" --output-format json (또는 Agent SDK) 비대화형 호출
OpenCode: CLI 비대화형 실행 또는 로컬 서버 API 모드로 호출
출력 규약: 차트는 workspace/{session_id}/chart.json(Plotly JSON)으로 저장하고, 텍스트 결과는 고정 JSON 스키마(summary, key_metrics, warnings, chart_path, python_code)로 반환을 강제합니다.
컨텍스트 격리 (필수): 샘플 데이터 검증 시 정답 파일(SCENARIOS.md, evaluation/ 등)이 에이전트에 노출되지 않도록 반드시 data/ 폴더(또는 업로드된 CSV만 복사한 임시 세션 폴더)만 작업 디렉터리(cwd)로 마운트합니다.

3. 프로젝트 디렉터리 구조
Git 충돌을 최소화하고 4명이 병렬로 개발할 수 있도록 모듈을 분리합니다.
auto-bi-agent/
├── app.py                     # [C] Streamlit 메인 UI (업로드, 대시보드, 채팅, Plotly 렌더링)
├── core/
│   ├── profiler.py            # [A] CSV 로더(utf-8-sig), 스키마·PK/FK 추론, 결측치 분류
│   ├── guardrails.py          # [A] 생성 코드/결과 정합성 검증 (행 수 보존, 중복 집계 경고)
│   ├── runner_base.py         # [B] AgentRunner 추상 클래스 및 공통 입출력 스키마
│   ├── claude_runner.py       # [B] Claude Code CLI / SDK 연동 모듈
│   └── opencode_runner.py     # [B] OpenCode CLI / API 연동 모듈
├── prompts/
│   ├── system_guardrail.md    # [D] 범용 데이터 분석·집계 가드레일 시스템 프롬프트
│   └── auto_briefing.md       # [D] 파일 업로드 직후 실행되는 자동 리스크 탐지 프롬프트
├── workspace/                 # 세션별 임시 CSV 및 생성된 chart.json 저장소 (.gitignore)
├── eval/
│   └── run_benchmark.py       # [D] 평가 질문 7종 자동 채점 및 Claude vs OpenCode 비교
├── requirements.txt
└── README.md

4. 역할 분담 및 3일 마일스톤
팀원별 담당 역할
담당
역할
핵심 산출물
A (1명)
프로파일러 & 가드레일
다중 CSV 자동 로더, PK/FK 추론, 결측치 분류, 조인 행 수·집계 검증 로직
B (1명)
AI 에이전트 코어
AgentRunner 추상화, Claude Code / OpenCode 연동, JSON+Plotly 출력 파싱 및 재시도 루프
C (1명)
프론트엔드 & 시각화
Streamlit 대시보드 UI, 파일 업로드, Plotly 차트 렌더링, 인사이트 카드 및 실행 코드 토글
D (1명)
PM & 프롬프트/평가
시스템·브리핑 프롬프트 고도화, 벤치마크 자동 채점(run_benchmark.py), 타 데이터셋 범용성 검증

3일 개발 일정
일정
주요 마일스톤
Day 1 (기반 구축)
• 인터페이스 계약(schema_profile.json, agent_response.json) 확정 및 Repo 세팅<br>• A: profiler.py (스키마·ER 추론) 완성<br>• B: 단일 CSV 대상 Claude Code / OpenCode → Plotly 생성 프로토타입<br>• C: Streamlit 기본 레이아웃(사이드바 업로드 + 메인 채팅/차트)<br>• D: system_guardrail.md 초안 및 평가 기준표 작성
Day 2 (핵심 고도화)
• A+B 통합: 다중 테이블 조인 가드레일 및 자가 수정 루프 연동<br>• B+D: 업로드 즉시 실행되는 자동 이상치·결측치 브리핑 연동<br>• C: 인사이트 카드 UI, 경고 배지, 코드 보기/다운로드 기능 추가<br>• 전체: 샘플 데이터(data/만 투입) 1차 벤치마크 테스트
Day 3 (검증 및 마무리)
• D 주도: 오답 케이스(예산 중복 합산, 단위 오해 등) 집중 디버깅 및 프롬프트 튜닝<br>• 범용성 검증: 외부 다중 CSV(예: 이커머스 주문/고객 데이터) 교차 테스트<br>• 데모 준비: 라이브 시연 리허설 및 장애 대비 녹화 백업


5. 핵심 가드레일 시스템 프롬프트 초안
prompts/system_guardrail.md에 반영할 범용 데이터 분석 규칙입니다.
<details>
<summary>펼쳐서 보기 (프롬프트 원문)</summary>

```markdown
당신은 다중 CSV 데이터를 분석하고 Plotly 인터랙티브 차트를 생성하는 시니어 데이터 분석 에이전트입니다.
코드를 작성하고 실행할 때 아래 5가지 가드레일을 반드시 준수하십시오.


1. 인코딩 및 날짜 파싱
- CSV 로드 시 항상 `encoding="utf-8-sig"`를 우선 시도하고, 날짜 형식 컬럼은 `pd.to_datetime()`으로 변환한다.
- 분석 기준일이 명시되지 않았다면 데이터 내 최신 실적/완료일 또는 스냅샷 기준을 명시적으로 밝힌다.


2. 1:N 조인 시 상위 테이블 수치 중복 집계 금지 (치명적 오류 방지)
- 1:N 관계 테이블을 조인한 뒤, 상위 테이블에만 존재하는 금액·예산·목표치 컬럼을 절대 단순 합산(`sum()`)하지 않는다.
- 반드시 하위 테이블을 먼저 상위 키 단위로 `groupby().sum()` 집계한 뒤, 상위 테이블과 1:1로 조인하여 비교한다.
- 다대일(Many-to-One) 조인 전후로 하위 테이블의 행 수(`len(df)`)가 유지되는지 `assert`로 검증한다.


3. 지표 단위(Stock vs Flow) 혼동 금지
- 기간 전체 누적값(Stock, 예: 누적 공수/비용)과 단위 기간당 기준값(Flow, 예: 주당 가용시간)을 기간 환산 없이 직접 대소 비교하지 않는다.
- 기간별 원천 로그(timesheet 등)가 없어 정확한 환산이 불가능한 경우, "추정치이며 단위 기간별 원천 로그가 필요함"을 경고 문구에 명시한다.


4. 결측치의 의미론적 구분
- 결측치(NULL)를 무조건 데이터 오류로 처리하지 않는다.
- 특정 상태/조건에서만 값이 존재해야 정상인 '구조적 공백'(예: 미완료 항목의 완료일)과 업무상 누락된 '확인 필요 결측'(예: 담당자 미지정, 기한 미입력)을 구분하여 리포트한다.


5. 시각화 및 출력 규약
- 모든 차트는 `plotly.express` 또는 `plotly.graph_objects`로 작성하며, 한글 깨짐이 없도록 설정하고 `workspace/{session_id}/chart.json`에 저장한다.
- 최종 응답은 반드시 지정된 JSON 스키마(`summary`, `key_metrics`, `warnings`, `chart_path`, `python_code`)로만 출력한다.
```

</details>


6. 데모 시나리오 및 벤치마크 검증 기준
샘플 데이터셋(가상 제조기업 한빛센서텍, 기준일 2026-11-19)에 대해 사전 하드코딩 없이 범용 로직만으로 아래 결과를 정확히 도출하는지 검증합니다.
시연 1 (데이터 품질 진단): 업로드 즉시 확인 필요 누락 7건(담당자 미지정 4건, 완료기한 미입력 3건)을 구조적 공백(178칸)과 분리해 알림
시연 2 (리스크 자동 대시보드): 기한 초과 미완료 업무 15건, 품질 부서 업무 집중(1인당 미완료 2.57건) 및 최다 배정 직원(E032, 9건) 자동 시각화
시연 3 (조인 가드레일 검증): 프로젝트별 예산 대비 실적 비용 초과율 질의 시 중복 집계 없이 P01(+30.72%), P02(+14.23%) 정확히 출력
시연 4 (반복 이슈 탐색): 반복 문제 유형(커넥터 접촉불량 5건, 온도 드리프트 4건 등) 파레토 차트 및 프로젝트별 매트릭스 시각화
벤치마크 자동 채점 (Q1~Q7):
Q1 기한 초과 미완료 건수: 15건
Q2 부서별 미완료 건수 (품질 18건, 1인당 2.57건 최다)
Q3 MH 초과 최대 프로젝트: P05 (370.0 MH)
Q4 비용 초과율 최대 프로젝트: P01 (+30.72%)
Q5 완료율 최저 프로젝트: P10 (20.0%)
Q6 확인 필요 누락 업무 수: 7건
Q7 최다 배정 직원: E032 (9건)

7. Day 1 오전 킥오프 체크리스트
Git 리포지토리 생성 및 브랜치 전략(main, feat/profiler, feat/agent, feat/ui, feat/eval) 확정
schema_profile.json (A → B) 인터페이스 스키마 확정
agent_response.json (B → C) 인터페이스 스키마 확정
Claude Code / OpenCode 로컬 CLI 실행 환경 및 API 키 테스트 완료
data/ 폴더만 격리된 테스트 워크스페이스 세팅 완료
