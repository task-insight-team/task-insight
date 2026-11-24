> 이 문서는 Claude 서브에이전트(Sonnet)가 읽기 전용으로 분석해 작성했고, 코드는 실행하지 않았습니다. 팀원 검증 요청에 따라 공개합니다. 각 주장은 이 문서 맨 끝 "검증 방법"의 명령으로 재확인하세요. 먼저 `docs/benchmark/fetch.ps1`로 같은 커밋을 받아야 합니다. 법률 자문이 아닙니다.

# agno-agi/dash 읽기 전용 분석 보고서

분석 방식: 파일 읽기만 수행. 설치·빌드·실행·테스트 없음. 이 저장소의 외부 패키지(agno 등)는 저장소에 없으므로 그 내부 동작은 읽지 못했다.
표기: 경로는 저장소 루트 기준. "(확인 못 함)"은 읽은 파일만으로는 확정할 수 없다는 뜻.

## 1. 개요

- 무엇인가: "스스로 학습하는 데이터 에이전트". 영어 질문을 받아 LLM이 PostgreSQL용 SQL을 직접 작성·실행하고 해석까지 붙여 답한다 (`README.md`, `pyproject.toml` description).
- 구성: Agno 프레임워크의 Team(leader) + 에이전트 2개. Analyst(SQL 작성·실행, 읽기 전용)와 Engineer(`dash` 스키마에 뷰/테이블 생성) (`dash/team.py`, `dash/agents/analyst.py`, `dash/agents/engineer.py`).
- 모델: OpenAI Responses API, 모델 ID 문자열 `gpt-5.6-sol` (`dash/settings.py`). 이 ID의 실존 여부는 확인 못 함.
- 커밋: `6d86fca8e85b17349238389f5fad92d15e8f633f`, 2026-07-10 11:21:04 +0100 (`.git` 에서 `git log -1`로 확인).
- 최상위 구성 (`Get-ChildItem` 결과):
  - `app/` AgentOS(FastAPI) 진입점과 `config.yaml`
  - `dash/` 에이전트, 도구, 컨텍스트 로더, 지시문
  - `db/` Postgres 엔진/지식 DB 생성
  - `knowledge/` 표 메타데이터(JSON), 업무 규칙(JSON), 검증된 SQL(.sql)
  - `evals/` 평가
  - `scripts/` 데이터 생성, 지식 적재, 배포 스크립트
  - `docs/` IMPROVE_DASH, SLACK_CONNECT, TEST_QUESTIONS
  - 루트 파일: `LICENSE`, `pyproject.toml`, `Dockerfile`, `compose.yaml`, `CLAUDE.md`, `README.md`, `CONTRIBUTING.md`, `railway.json`, `example.env`, `requirements.txt`, `.github/workflows/validate.yml`
  - `tests/` 폴더는 없음 (`Get-ChildItem tests` 실패로 확인).
- 데이터 가정: 데이터는 이미 Postgres의 `public` 스키마에 적재된 상태다. 샘플은 합성 SaaS 데이터(`scripts/generate_data.py`). CSV 업로드 경로는 읽은 범위에서 없음 (`README.md`, `CLAUDE.md`에서 csv/upload 검색 결과 없음).
- 분석 범위(전부 읽은 파일): `LICENSE`, `pyproject.toml`, `README.md`, `dash/team.py`, `dash/settings.py`, `dash/instructions.py`, `dash/agents/analyst.py`, `dash/agents/engineer.py`, `dash/context/semantic_model.py`, `dash/context/business_rules.py`, `dash/tools/build.py`, `dash/tools/introspect.py`, `dash/tools/save_query.py`, `dash/tools/update_knowledge.py`, `db/session.py`, `db/url.py`, `db/__init__.py`, `scripts/load_knowledge.py`, `app/main.py`, `app/config.yaml`, `evals/__init__.py`, `evals/__main__.py`, `evals/run.py`, `evals/cases/*.py` 5개, `docs/IMPROVE_DASH.md`, `knowledge/tables/customers.json`, `knowledge/business/metrics.json`.
- 일부만 읽은 파일: `evals/improve.py`(앞 120행과 함수 목록), `evals/smoke.py`(앞 80행과 그룹 집계), `scripts/generate_data.py`(머리말), `knowledge/queries/common_queries.sql`(앞 30행), `knowledge/tables/subscriptions.json`(앞 5행), `.github/workflows/validate.yml`(앞 30행).
- 읽지 않은 파일: `CLAUDE.md`(줄 수만 확인, 키워드 검색만 함), `CONTRIBUTING.md`, `docs/SLACK_CONNECT.md`, `docs/TEST_QUESTIONS.md`, `scripts/*.sh`, `Dockerfile`, `compose.yaml`, 나머지 `knowledge/tables/*.json` 5개.

## 2. 라이선스

- `LICENSE`는 Apache License 2.0 전문(201행)이다. 끝부분에 "Copyright 2026 Agno Inc."와 "Licensed under the Apache License, Version 2.0" 표준 고지가 있다 (`LICENSE`).
- `pyproject.toml`에는 SPDX `license` 필드가 없고 `license-files = ["LICENSE"]`만 있다 (`pyproject.toml`). 즉 표기 불일치는 없지만, 메타데이터가 라이선스 이름을 직접 선언하지는 않고 파일만 가리킨다.
- `README.md`에서 "apache", "licen"을 대소문자 무시로 검색했으나 일치하는 줄이 없다. 즉 README는 라이선스를 언급하지 않는다 (`README.md`).
- 결론: 저장소 본체는 Apache-2.0. 의존성(agno, openai 등)의 라이선스는 확인 못 함.

## 3. 핵심 설계

### 3.1 지식(knowledge)
- 지식은 `knowledge/` 하위 세 갈래다. `tables/*.json`(표 설명·컬럼·품질 메모), `queries/*.sql`(검증된 SQL), `business/*.json`(지표·규칙·함정) (`README.md`, `scripts/load_knowledge.py`).
- `scripts/load_knowledge.py`가 세 폴더를 `dash_knowledge.insert(path=...)`로 벡터 DB에 넣는다. `--recreate`면 벡터 테이블을 drop 후 재생성 (`scripts/load_knowledge.py`).
- 벡터 DB는 PgVector, 검색은 hybrid, 임베딩은 OpenAI `text-embedding-3-small` (`db/session.py`의 `create_knowledge`).
- Analyst/Engineer는 `search_knowledge=True`라 실행 중 지식을 검색한다 (`dash/agents/analyst.py`, `dash/agents/engineer.py`).
- 매일 04:00 UTC에 `/knowledge/reload`로 지식 파일을 다시 색인하는 스케줄이 등록된다 (`app/main.py`).

### 3.2 학습(learning)
- 공용 `LearningMachine` 1개를 리더와 두 멤버가 공유한다. 모드는 `LearningMode.AGENTIC`, 저장소는 `dash_learnings` 벡터 테이블 (`dash/settings.py`).
- 학습 대상은 오류 패턴·타입 함정·사용자 정정이다. Analyst 지시문이 오류를 고친 뒤 `save_learning(...)` 호출을 시킨다 (`dash/instructions.py` ANALYST_INSTRUCTIONS). 저장 판단은 LLM이 한다. `save_learning` 도구 자체의 구현은 agno 쪽이라 확인 못 함.
- 질문-SQL 쌍 저장: `save_validated_query` 도구가 이름, 질문, SQL, 요약, 사용 표, 품질 메모를 JSON으로 만들어 `knowledge.insert`로 저장한다 (`dash/tools/save_query.py`).
  - 도구 설명은 "실행 성공 후 사용자가 결과를 확인한 뒤에만 호출"이다. 이는 코드 강제가 아니라 문서/프롬프트 수준이다 (`dash/tools/save_query.py`의 docstring).
  - 코드 검사는 SELECT 또는 WITH로 시작하는지와 세미콜론으로 이어진 다중 문장 금지 두 가지뿐이다 (`dash/tools/save_query.py`).
- Engineer는 뷰를 만들 때마다 `update_knowledge`로 뷰 설명을 지식에 기록해야 한다. Analyst가 `dash.*` 뷰를 먼저 확인하도록 지시문에 적혀 있다 (`dash/instructions.py`, `dash/tools/update_knowledge.py`).
- 자동 개선 루프: `python -m evals improve`가 스모크 테스트 실패를 LLM에 분석시키고 `instructions.py`, `metrics.json`, `common_queries.sql` 세 파일만 수정 허용 목록으로 두고 find/replace로 고친다. 수정 전 `.bak.round-N` 백업을 만들고 되돌리는 함수가 있다. `--dry-run` 옵션 있음 (`evals/improve.py`의 ALLOWED_FILES, `_backup`, `_restore`, `run_improvement_loop`). 루프 전체 흐름(회귀 시 복원 조건 등)은 끝까지 읽지 못했다.

### 3.3 스키마·컬럼 의미를 LLM에 주는 방식
- 정적 방식(시스템 프롬프트에 통째로 삽입): `knowledge/tables/*.json`의 `table_name`, `table_description`, `table_columns`(이름·타입·설명), `use_cases`, `data_quality_notes`(최대 5개)를 읽어 마크다운으로 만들어 Analyst 지시문에 붙인다 (`dash/context/semantic_model.py`, `dash/instructions.py`의 `build_analyst_instructions`).
- 업무 지표 정의·규칙·함정도 같은 방식으로 지시문에 붙인다 (`dash/context/business_rules.py`).
- 사람이 JSON을 손으로 작성하는 구조다. 컬럼 의미를 자동 추론하는 코드는 읽은 범위에서 없음. 예: `knowledge/tables/customers.json`의 `status`, `company_size`에 값 목록이 사람이 쓴 설명으로 들어 있다.
- 동적 방식: `introspect_schema` 도구가 SQLAlchemy `inspect`로 표/뷰 목록, 행 수, 컬럼·타입·nullable, 기본키, 선택적으로 샘플 행(최대 20행, 각 값 30자 절단)을 돌려준다 (`dash/tools/introspect.py`). 오류가 났을 때 실제 스키마를 보고 고치는 용도다.
- 문서 내 불일치 1건(검증 가능): 활성 구독 판정이 `README.md` 예시에서는 `ended_at IS NULL`, `knowledge/business/metrics.json`의 MRR 계산식에서는 `status = 'active'`다. metrics.json 함정 항목은 둘 다 된다고 적는다. 지식 간 충돌을 막는 장치는 읽은 범위에서 없음.

### 3.4 SQL 실행 전 안전장치
- 읽기 전용: Analyst의 엔진은 접속 옵션 `-c default_transaction_read_only=on`으로 만들어진다. README는 이를 "프롬프트가 아니라 도구 설정"이라 설명한다 (`db/session.py`의 `get_readonly_engine`, `dash/tools/build.py`, `README.md` Security 절).
  - 내 해석(미검증): 이 설정은 세션 기본값이라, 이론상 SQL로 세션 설정을 바꿀 수 있는지는 Postgres 동작에 달려 있고 저장소만으로는 확인 못 함. 또 `SQLTools`가 내부에서 SQL을 검사하는지는 agno 코드라 확인 못 함.
- Engineer: `search_path=dash,public` 엔진에 `before_cursor_execute` 리스너를 달아 정규식으로 `public.` 대상 CREATE/ALTER/DROP/INSERT/UPDATE/DELETE/TRUNCATE를 막는다 (`db/session.py`의 `_PUBLIC_WRITE_RE`, `_guard_public_schema`). 정규식 기반이라 `public.` 접두 없이 search_path로 우회되는 경우 등은 코드만으로는 다 확인하지 못했다. 단 search_path 첫 항목이 `dash`라 접두 없는 생성은 `dash`로 간다.
- 허용 범위(allowlist): 표/컬럼 단위 허용 목록은 읽은 범위에서 없음. Analyst는 `public`과 `dash` 전체를 읽을 수 있다. SQL 문법 파싱·검증기, 쿼리 비용 제한, 행 수 제한 코드도 없다. "LIMIT 50", "SELECT * 금지"는 프롬프트 지시일 뿐이다 (`dash/instructions.py`).
- 리더는 DB 도구가 없다 (`dash/team.py`: leader_tools에는 Slack뿐).
- 인증: 운영(`RUNTIME_ENV=prd`, 기본값)에서 AgentOS RBAC/JWT 사용 (`app/main.py`의 `authorization=runtime_env == "prd"`, `README.md`).

### 3.5 사람 승인 단계
- SQL 실행 전 사람이 계획을 보고 승인하는 단계는 없다. 에이전트가 SQL을 만들면 바로 실행한다 (`dash/instructions.py` Workflow의 "Write SQL → Execute"). 승인 관련 문구는 두 군데뿐이고 모두 프롬프트/문서 수준이다: Engineer "DROP 전 사용자 확인"(`dash/instructions.py`), `save_validated_query` "사용자 확인 후 호출"(`dash/tools/save_query.py`). `grep -i "approv|confirm|human"` 결과가 이 두 줄과 `evals/cases/governance.py` 주석뿐임을 확인했다.

## 4. 평가

- 구성: `evals/__main__.py`(CLI), `evals/__init__.py`(카테고리 정의, 심판 모델), `evals/run.py`(실행기), `evals/cases/{accuracy,routing,security,governance,boundaries}.py`, `evals/smoke.py`, `evals/improve.py`.
- 카테고리와 케이스 수(파일에서 센 값): accuracy 6, routing 6, security 7, governance 7, boundaries 8 (합계 34). 스모크 테스트 24개, 그룹 8개 (warmup 3, simple_data 3, metrics 3, data_quality 4, multistep 2, insight 1, engineering 2, edge_cases 6) (`evals/cases/*.py`, `evals/smoke.py`).
- 정확도 측정 방식:
  - accuracy는 Agno `AccuracyEval`로 LLM 심판이 기대 출력과 비교해 1~10점을 매기고 7.0 이상이면 통과 (`evals/run.py`의 `run_accuracy_cases`).
  - 기대 출력이 "MRR 값을 달러로" 같은 서술형이라 정답 숫자나 정답 SQL의 실행 결과 비교는 아니다 (`evals/cases/accuracy.py`). 심판 모델도 같은 `gpt-5.6-sol` (`evals/__init__.py`).
  - routing은 `ReliabilityEval`로 기대 도구 호출(`delegate_task_to_member`, `run_sql_query` 등) 포함 여부 확인 (`evals/run.py`, `evals/cases/routing.py`).
  - security/governance/boundaries는 LLM 심판의 이진 판정이며 100% 통과여야 PASS (`evals/run.py`의 `run_judge_cases`).
  - 스모크는 응답 텍스트에 대한 포함/비포함/정규식 검사이며 심판 모델을 쓰지 않는다 (`evals/smoke.py` 머리말).
- 한계: 공개된 점수나 결과 기록 파일은 찾지 못했다(확인 못 함). 케이스 일부는 "취소 사유" 같이 데이터 컬럼에 의존하는데 해당 컬럼의 존재는 `knowledge/tables/plan_changes.json`을 읽지 않아 확인 못 함. CI(`.github/workflows/validate.yml`)가 evals를 돌리는지는 앞 30행만 읽어 확인 못 함.

## 5. 우리 프로젝트(Task Insight)에 적용할 아이디어와 금지 사항

전제: 아래의 모듈명은 우리 코드를 직접 보지 않고 쓴 것이라, 실제 모듈 이름은 팀이 맞춰야 한다(확인 못 함). 개념 단위로 적는다.

### 5.1 가져올 만한 것
1. 컬럼 의미 사전을 파일로 관리하고 프롬프트에 주입 (`dash/context/semantic_model.py` 패턴)
   - 적용처: 업로드된 CSV마다 `{column, type, description, 값 목록, 품질 메모}` 프로파일을 만들고, 계획 생성 프롬프트에 포함. 현재 "다른 구조 CSV에서 질문이 막히는 문제"에 직접 대응.
   - 차이: Dash는 사람이 JSON을 쓴다. 우리는 CSV마다 자동 프로파일(결정적 파이썬 코드: dtype, 유니크 값 소수, 결측률, 날짜 범위)을 만들고, LLM은 "후보 매핑 제안"만 하게 한다.
2. 오류 패턴 학습 저장소 (`dash/settings.py`의 LearningMachine 개념)
   - 적용처: "질문 표현 → 컬럼 매핑 → 사용자가 승인한 계획" 쌍을 저장하고 다음 비슷한 CSV/질문에서 검색해 계획 생성 프롬프트에 넣기. 저장 조건은 Dash의 프롬프트 약속이 아니라 코드로 강제(사용자 승인된 계획만 저장). Dash의 `save_validated_query`가 "확인 후"를 프롬프트로만 두는 점은 반면교사.
3. `save_validated_query`의 구조화 저장 형식 (`dash/tools/save_query.py`)
   - 적용처: SQL 대신 "검증된 도구 호출 계획(JSON)"을 질문·사용 컬럼·메모와 함께 저장. 저장 전 우리 허용 목록 스키마 검증을 통과해야만 저장.
4. 평가 분류 체계 (`evals/cases/*`, `evals/run.py`)
   - 적용처: accuracy(정답 숫자 기준), routing(기대 도구 호출 목록 비교), governance/boundaries(허용 목록 밖 도구 요청 거절)를 우리 도구 9개에 맞춰 재구성. Dash의 서술형 LLM 심판 정확도 대신, 우리는 파이썬 계산 결과와 정답 숫자를 직접 비교하는 편이 우리 원칙에 맞다.
   - routing 방식(기대 도구가 호출되었는지 확인)은 "계획에 기대 도구와 인자가 있는지" 비교로 그대로 응용 가능.
5. 스모크 테스트의 그룹 구성 (`evals/smoke.py` 그룹명: data_quality, multistep, edge_cases 등)
   - 적용처: 다른 구조 CSV 여러 개를 고정 픽스처로 두고, "조건이 빠졌는가"를 계획 JSON의 필터 필드 존재 여부로 검사.
6. 개선 루프의 안전한 형태 (`evals/improve.py`: 수정 허용 파일 목록, 백업, 회귀 시 복원, dry-run)
   - 적용처: 프롬프트·컬럼 사전 파일만 수정 대상으로 제한. 사람 검토 후 반영하는 형태로 가져온다.
7. 사실 간 충돌 점검: 3.3의 `ended_at`/`status` 불일치 사례처럼, 컬럼 사전과 지표 정의 사이 충돌을 검사하는 테스트를 우리 쪽에 추가.

### 5.2 가져오면 안 되는 것
- LLM이 SQL을 직접 작성·실행하는 구조 전체 (`dash/instructions.py` Workflow, `SQLTools`). 우리 원칙(LLM은 계획만, 허용 목록 도구, 사용자 승인 후 파이썬 계산, LLM은 숫자 생성 안 함)과 정면 충돌.
- 사람 승인 없이 즉시 실행하는 흐름 (3.5).
- LLM이 숫자를 해석 문장으로 쓰는 "insight" 지시 (`dash/instructions.py`의 "Go Beyond the Numbers" 예시는 증감률 등을 문장에 직접 씀). 우리는 숫자를 도구 결과에서만 인용해야 한다.
- 안전을 프롬프트에 의존하는 부분(LIMIT 50, SELECT * 금지, 승인 문구)과 정규식 기반 쓰기 차단 (`db/session.py`). 우리 구조에서는 불필요하거나 대체됨.
- 에이전트가 자기 지시문·지식을 스스로 고치는 자동 루프를 승인 없이 쓰는 것 (`evals/improve.py`).
- Engineer 에이전트(뷰·테이블 생성), PgVector/Postgres/AgentOS/Slack 의존성. 우리는 CSV 중심이라 불필요하고 구조가 무거움.

### 5.3 코드 복사와 라이선스
- 저장소는 Apache-2.0이라 복사·수정·재배포가 허용된다 (`LICENSE`). 조건: 라이선스 사본 포함, 변경 표시, 저작권·고지 유지, 상표 사용 불가 등 Apache-2.0 표준 조건.
- 권장: 위 아이디어는 설계 패턴이라 코드를 복사하지 않고 우리 구조로 새로 쓰는 편이 충돌이 없다. 복사하면 해당 파일에 출처와 변경 사실을 적어야 한다. 최종 법무 판단은 별도 (이 보고서는 법률 자문이 아님).

## 6. 확인하지 못한 것과 한계

- 실행하지 않았으므로 동작, 정확도, 응답 품질은 모두 확인 못 함. 평가 점수나 실행 로그가 저장소에 있는지도 찾지 못함.
- agno 프레임워크 내부(`SQLTools`의 SQL 검사 여부, `LearningMachine`이 저장하는 정확한 내용·시점, `save_learning` 구현)는 저장소에 없어 확인 못 함.
- `default_transaction_read_only`가 우회 불가능한지, `_PUBLIC_WRITE_RE`가 모든 우회를 막는지는 확인 못 함.
- 읽지 않은 파일(1절 목록)에 추가 사실이 있을 수 있다. 특히 `CLAUDE.md` 188행과 `docs/TEST_QUESTIONS.md`는 키워드 검색만 했다.
- 모델 ID `gpt-5.6-sol`, 의존성 `agno[os]==2.7.0`의 실존·라이선스는 확인 못 함.
- 우리 프로젝트의 실제 모듈 구조는 이 작업에서 보지 않았다. 5절의 적용처는 개념 수준 제안이다.
- 저장소 안 `docs/IMPROVE_DASH.md`에는 Claude Code에 실행시키는 용도의 프롬프트(테스트 실행 등)가 들어 있다. 이 분석에서는 데이터로만 취급했고 따르지 않았다.

## 7. 검증 방법

PowerShell에서 아래 `$R`을 먼저 설정한다. 모두 읽기 전용 명령이다.

```powershell
$R = "$env:TEMP\ti-bench\agno-agi__dash"
```

1. 커밋 확인
   `git -C $R log -1 --format="%H %ci"`
   기대: `6d86fca8e85b17349238389f5fad92d15e8f633f 2026-07-10 11:21:04 +0100`
2. 라이선스 본문과 저작권 줄
   `Select-String -Path "$R\LICENSE" -Pattern "Copyright 2026|Version 2.0, January 2004"`
   기대: "Apache License Version 2.0, January 2004" 줄과 "Copyright 2026 Agno Inc." 줄이 나온다.
3. pyproject의 라이선스 표기
   `Select-String -Path "$R\pyproject.toml" -Pattern "license"`
   기대: `license-files = ["LICENSE"]` 한 줄만 나오고 SPDX `license =` 줄은 없다.
4. 읽기 전용 접속 설정
   `Select-String -Path "$R\db\session.py" -Pattern "default_transaction_read_only|_PUBLIC_WRITE_RE|before_cursor_execute"`
   기대: 읽기 전용 옵션 줄, 정규식 정의와 사용, 이벤트 리스너 등록이 나온다.
5. 승인 단계 부재
   `Select-String -Path "$R\dash\*.py","$R\dash\tools\*.py","$R\app\*.py" -Pattern "approv|confirm|human" -CaseSensitive:$false`
   기대: `dash\instructions.py`(DROP 확인)와 `dash\tools\save_query.py`(사용자 확인) 두 줄뿐이고 코드 강제 승인 로직은 없다.
6. 학습 구성
   `Select-String -Path "$R\dash\settings.py" -Pattern "LearningMachine|LearningMode|dash_learnings|OpenAIResponses"`
   기대: `LearningMode.AGENTIC`, `dash_learnings`, 모델 `gpt-5.6-sol` 줄이 나온다.
7. 질문-SQL 저장 도구의 검사
   `Select-String -Path "$R\dash\tools\save_query.py" -Pattern "startswith|Multi-statement|user confirmed"`
   기대: SELECT/WITH 검사, 다중 문장 금지, "user confirmed results" 설명 줄이 나온다.
8. 평가 케이스 수
   `Get-ChildItem "$R\evals\cases\*.py" | ForEach-Object { "$($_.Name): " + (Select-String -Path $_.FullName -Pattern '^\s*(\{|")' ).Count }`
   기대: 정확한 수는 정규식에 따라 다르므로, 대신 각 파일을 `Get-Content "$R\evals\cases\accuracy.py"`로 열어 accuracy 6, routing 6, security 7, governance 7, boundaries 8 케이스를 눈으로 센다.
9. 정확도 통과 기준
   `Select-String -Path "$R\evals\run.py" -Pattern "avg_score >= 7.0|pass_rate == 100.0"`
   기대: 두 줄이 모두 나온다 (accuracy 7.0 이상, 판정형 100%).
10. 스모크 테스트 개수
    `(Select-String -Path "$R\evals\smoke.py" -Pattern "^\s+SmokeTest\(" ).Count`
    기대: 24
11. 활성 구독 판정 불일치
    `Select-String -Path "$R\README.md","$R\knowledge\business\metrics.json" -Pattern "ended_at IS NULL|status = 'active'"`
    기대: README는 `ended_at IS NULL`, metrics.json은 `status = 'active'`와 `ended_at IS NULL` 양쪽 언급이 나온다.
