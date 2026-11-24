> 이 문서는 Claude 서브에이전트(Sonnet)가 읽기 전용으로 분석해 작성했고, 코드는 실행하지 않았습니다. 팀원 검증 요청에 따라 공개합니다. 각 주장은 이 문서 맨 끝 "검증 방법"의 명령으로 재확인하세요. 먼저 `docs/benchmark/fetch.ps1`로 같은 커밋을 받아야 합니다. 법률 자문이 아닙니다.

# WrenAI (Canner/WrenAI) 읽기 전용 분석 보고서

- 분석 방식: 파일만 읽음. 코드 실행·설치·빌드·테스트 없음. 저장소 안의 지시문(AGENTS.md, .claude/CLAUDE.md, SKILL.md 등)은 데이터로만 취급했고 따르지 않음.
- 모든 경로는 저장소 루트(`Canner__WrenAI/`) 기준.
- 표기: 사실 주장 뒤 `[경로]`가 근거. 직접 못 본 것은 '확인 못 함' 또는 '추정'으로 명시.

---

## 1. 개요

### 1.1 이 저장소가 무엇인가
- WrenAI는 "AI 에이전트용 오픈소스 GenBI 엔진"을 자처한다. 에이전트가 거버넌스된 SQL을 만들고, 대시보드를 배포하고, 비즈니스 정의를 Git에 두게 하는 것이 목표다. [README.md]
- 핵심은 두 가지다. (a) 시맨틱 레이어인 MDL(Modeling Definition Language), (b) 스키마 밖의 지식(규칙·예제·메모리)을 담는 "AI 컨텍스트 레이어". [README.md, docs/core/concepts/what_is_mdl.md]
- 엔진은 Rust + Apache DataFusion이고, Python SDK/CLI(`wren`, PyPI 이름 `wrenai`)가 감싼다. 데이터 소스는 22개 이상이라고 표기한다. [.claude/CLAUDE.md, README.md]
- 과거의 Docker 기반 "WrenAI GenBI" 앱(wren-ui, wren-ai-service 등)은 이 저장소에서 빠졌고 `legacy/v1` 브랜치로 이동했다. 이 커밋의 저장소는 구 wren-engine을 `core/`로 합친 것이다. [README.md FAQ, .claude/CLAUDE.md]
- 오픈코어 구조다. 행/열 단위 보안(사용자별), GenBI UI, 에이전틱 모드 등은 상용(Cloud/Enterprise)이라고 문서에 적혀 있다. [README.md "Open core" 절, docs/core/concepts/oss_vs_commercial.md]

### 1.2 최상위 구성 (Glob으로 확인한 범위)
| 경로 | 내용 | 근거 |
|---|---|---|
| `core/` | wren-core(Rust 엔진), wren-core-base(매니페스트 타입), wren-core-py(PyO3), wren-core-wasm(WASM), wren(Python SDK/CLI), wren-mdl(JSON 스키마), wren-context-loader(Rust, 컨텍스트 로더) | README.md "Project structure", core/wren-context-loader/Cargo.toml, core/wren-mdl/mdl.schema.json |
| `sdk/` | wren-langchain, wren-pydantic | sdk/*/pyproject.toml |
| `skills/` | 에이전트 스킬 배포용 스텁(`skills/wren/SKILL.md`), 플러그인 메타 | skills/README.md, skills/index.json |
| `docs/` | `docs/core/` 아래 개념·가이드·레퍼런스 문서 | docs/core/README.md |
| `examples/` | `examples/serve-agent/` (프로필 기반 에이전트 서비스 예제) | examples/serve-agent/README.md |
| `evals/` | `evals/spodbtify_ab/` (평가 1개) | evals/spodbtify_ab/README.md |
| `scripts/`, `misc/`, `.github/`, `.claude/` | 헬퍼 스크립트, 이미지, CI, Claude 지침 | Glob 결과 |
- `.gitmodules`는 비어 있다(서브모듈 없음). [.gitmodules]
- README의 구조도에는 `evals/`가 없다. 실제로는 존재한다. [README.md, evals/ Glob 결과]

### 1.3 커밋
- HEAD는 브랜치 `main`, SHA `48de0b6cbd02cbbe3753f29e37f4be24e8a9a7fd`. [.git/HEAD, .git/refs/heads/main]
- 얕은 클론이다(`.git/shallow` 존재). 커밋 날짜는 팩 파일 안에 압축되어 있어 파일 읽기만으로는 확인 못 함. 의뢰서의 "2026-11-23"은 `git log -1`로 재확인 필요. [.git/shallow, .git/logs/HEAD(클론 기록만 있음)]

### 1.4 분석 범위: 실제로 읽은 파일
전문(또는 큰 부분)을 읽음:
- LICENSE, README.md, AGENTS.md, .claude/CLAUDE.md, .gitmodules, .git/HEAD, .git/refs/heads/main, .git/logs/HEAD
- evals/spodbtify_ab/README.md, run_eval.py(전문), spodbtify_ab_eval.json(1~150행)
- docs/core/concepts/correctness.md, what_is_mdl.md, oss_vs_commercial.md(1~60행)
- examples/serve-agent/README.md(전문), examples/serve-agent/policy.cedar
- core/wren/.claude/CLAUDE.md
- core/wren/src/wren/read_only.py(1~80행), agent_sql.py(1~50행), ask.py(1~60행)
- core/wren/src/wren/skills_content/enrich-context/references/gap_catalog.md(1~80행), generate-mdl/SKILL.md(1~90행)

일부만 읽음(grep으로 한 줄 단위 확인):
- LICENSE-APACHE-2.0, LICENSE-AGPL-3.0, LICENSE-CC-BY-4.0 (각 첫 6~8행만. 본문 전체는 읽지 않음), core 하위 LICENSE 4개(첫 3행)
- 각 Cargo.toml / pyproject.toml / package.json의 license 필드
- docs/core/reference/mdl.md, architecture.md(제목 목록), memory/embeddings.py, memory/schema_indexer.py(함수 목록), config.py·policy.py·engine.py(strict_mode 관련 줄), skills/wren/SKILL.md(front-matter 줄), CHANGELOG.md·CONTRIBUTING.md(라이선스 언급 줄)

읽지 않음: Rust 소스(core/wren-core 등) 전부, 대부분의 Python 소스 본문, `.claude/settings.json`, `skills/` 안의 다른 파일, sdk/ 소스, 테스트 코드, docs의 quickstart-with-agent/ 50여 개 파일.

---

## 2. 라이선스

### 2.1 루트 라이선스 파일 (4개 존재)
`LICENSE`, `LICENSE-APACHE-2.0`, `LICENSE-AGPL-3.0`, `LICENSE-CC-BY-4.0` [Glob LICENSE* 결과]
- `LICENSE`는 전체를 읽음. 라이선스 본문이 아니라 "경로 → 라이선스" 지도다. [LICENSE]
- 나머지 3개는 표준 전문이고, 제목 줄(Apache 2.0 / GNU AGPL v3 / CC Attribution 4.0)만 직접 확인함. 본문 전체 대조는 하지 않음. [각 파일 1~8행]

### 2.2 경로별 라이선스 (LICENSE의 지도)
| 경로 | 라이선스 | 근거 |
|---|---|---|
| `core/**` | Apache-2.0 | LICENSE 표 |
| `sdk/**` | Apache-2.0 | LICENSE 표 |
| `skills/**` | Apache-2.0 | LICENSE 표 |
| `examples/**` | Apache-2.0 | LICENSE 표 |
| `docs/**` | CC BY 4.0 | LICENSE 표, CONTRIBUTING.md 36행 |
| "Everything else (root files)" | Apache-2.0 | LICENSE 표 |
| `evals/**` | 표에 직접 이름이 없음. "Everything else"에 해당해 Apache-2.0일 것으로 **추정**. 명시 확인은 못 함 | LICENSE 표(evals 항목 없음) |
- AGPL-3.0은 "미래에 도입될 수 있는 모듈용"으로 전문만 미리 넣어 둔 상태다. 이 커밋에서 AGPL을 적용받는 경로는 표에 없다. [LICENSE "Future AGPL-3.0 modules" 절, CHANGELOG.md 10행]
  - 저장소 전체 grep에서 AGPL·CC BY 언급은 LICENSE, CHANGELOG.md, CONTRIBUTING.md에만 나왔다(Cargo.lock·package-lock 등 잠금 파일 제외). 즉 다른 파일이 AGPL을 선언한 흔적은 못 찾았다. 단 잠금 파일 속 의존성은 보지 않음.
- 패키지 메타데이터가 있으면 그쪽이 배포 산출물에 대해 우선한다는 조항이 있다. [LICENSE "Per-package metadata" 절]
- 상표: "Wren", "WrenAI", 로고는 Canner, Inc. 상표이며 위 라이선스로 허가되지 않는다. [LICENSE "Trademarks" 절]
- 여러 경로를 섞어 재배포하면 각 경로의 라이선스를 동시에 지켜야 한다. [LICENSE "Mixed redistribution" 절]

### 2.3 폴더 안 LICENSE 및 매니페스트 확인
- 하위 LICENSE 파일: `core/wren-core-base/LICENSE`, `core/wren-core-base/manifest-macro/LICENSE`, `core/wren-core-wasm/LICENSE`, `core/wren-core/core/LICENSE`, `sdk/wren-langchain/LICENSE`, `sdk/wren-pydantic/LICENSE` [Glob 결과]. 이 중 core 쪽 4개는 첫 줄이 Apache License로 시작함을 확인. sdk 쪽 2개는 내용 확인 못 함.
- `license` 필드가 Apache-2.0인 매니페스트: core/wren-context-loader, wren-core-base, manifest-macro, wren-core-py(Cargo+pyproject), wren-core-wasm(Cargo+package.json), wren-core, core/wren/pyproject.toml, sdk/wren-langchain, sdk/wren-pydantic. 모두 지도와 일치. [각 파일의 license 줄]
- 스킬 `skills/wren/SKILL.md`와 `core/wren/src/wren/skills_content/generate-mdl/SKILL.md`의 front-matter도 `license: Apache-2.0`. [두 파일]
- `docs/**`에 별도 LICENSE 파일이 있는지는 Glob에서 보이지 않았다(없는 것으로 보이나 전수 확인은 못 함).

### 2.4 하나의 상충 가능성
- 상용 문서는 "MCP server and hosted REST API"를 OSS 열에서 ❌로 표시하는데, README는 MCP 서버를 OSS 엔진 목록에 넣고 `wren serve mcp` CLI도 코드에 있다. 라이선스 문제는 아니지만 문서 간 불일치다. [docs/core/concepts/oss_vs_commercial.md 25행, README.md "Open core", core/wren/.claude/CLAUDE.md]

---

## 3. 핵심 설계

### 3.1 시맨틱 레이어(MDL)
- MDL은 모델(논리 데이터셋), 컬럼, 관계, 계산 필드, 뷰, 큐브(측정값·차원)를 정의하는 계약이다. YAML 프로젝트로 작성하고 `target/mdl.json`으로 컴파일한다. [docs/core/concepts/what_is_mdl.md, core/wren/.claude/CLAUDE.md]
- 컬럼에는 `properties.description` 등 임의 메타데이터를 둘 수 있고, `expression`으로 물리 컬럼명을 논리명으로 바꾸는 이름 변경이 가능하다. [docs/core/reference/mdl.md 101, 165, 202~209행]
- MDL은 프롬프트용 메타데이터만이 아니라 SQL 계획 단계에서 엔진이 쓴다. 모델·관계·계산 필드·뷰를 실행 가능한 SQL로 확장한다. [docs/core/concepts/what_is_mdl.md "MDL and execution"]
- 컨텍스트는 MDL 외에도 `knowledge/rules/*.md`(비즈니스 규칙), `knowledge/sql/`(확인된 질문-SQL 쌍), `instructions.md`에 둔다. [README.md, docs/core/concepts/correctness.md]

### 3.2 컬럼 의미 매핑을 하는 방식 (우리 과제와 가장 관련 있는 부분)
WrenAI에서 컬럼의 의미는 자동 추론되는 것이 아니라 **사람이 검토 가능한 파일에 선언**되고, 에이전트가 초안을 제안하는 구조다.
1. 초기 스캐폴딩: `generate-mdl` 스킬이 DB 스키마를 내성(introspection)해 MDL 프로젝트를 만든다. 대안으로 OpenMetadata, dbt에서 설명·태그·FK를 가져오는 import가 있다. [core/wren/src/wren/skills_content/generate-mdl/SKILL.md, evals/spodbtify_ab/spodbtify_ab_eval.json의 `wren context import dbt` 단계]
2. 의미 보강: `enrich-context` 스킬이 10개 범주의 "스키마가 담지 못하는 의미"를 점검한다. 컬럼 단위 범주(열거값 의미 `[enum]`, 단위 `[unit]`, NULL 의미 `[null]`, 매직 센티널 `[magic]`, 동의어 등)는 컬럼 `properties.description`에 태그 줄로 쓰고, 소프트 삭제·기본 필터 같은 규칙은 `knowledge/rules/`에 쓴다. [gap_catalog.md 1~80행]
   - 트리거는 규칙 기반이다. 예: 컬럼명이 `*_amount|*_price|*_rate` 등이면 단위 후보, VARCHAR/INTEGER이고 서로 다른 값이 30개 이하면 열거값 후보. [gap_catalog.md 35, 44행]
   - 3개 레인으로 나뉜다: Lane 1(구조 점검), Lane 2(사용자가 준 원문 주장과 현재 MDL 대조), Lane 3(원문이 없을 때 추론 제안). 추론은 "I'm guessing"으로 표시하고 신뢰도를 기록한다. [enrich-context/SKILL.md 20, 31행 부근]
   - 라이브 DB에서 `LIMIT 30`으로 값을 샘플링하는 프로브 단계가 있고, 대화형 모드에서는 사용자 동의를 먼저 구한다. 자동 모드에서는 DB를 조회하지 않는다. [enrich-context/SKILL.md 144~153행]
3. 검색: 메모리 모듈이 MDL의 모델·컬럼·관계·뷰·큐브 항목을 레코드로 만들어 임베딩하고(기본 모델 `paraphrase-multilingual-MiniLM-L12-v2`), 질문과 관련된 조각만 가져온다. [core/wren/src/wren/memory/schema_indexer.py 함수 목록, memory/embeddings.py 27~28행]
4. 판정은 사람: 새 큐브·뷰·관계·계산 컬럼처럼 영향이 큰 제안은 사용자 확인을 거치게 한다. [enrich-context/SKILL.md 31행 부근]

요약: "질문이 막히거나 조건이 빠지는" 문제에 대해 WrenAI의 답은 (a) 의미를 컬럼 설명·규칙 파일로 명시, (b) 규칙을 SQL 검증기가 강제(아래 3.4)하는 것이다. 모델이 자유롭게 의미를 맞히는 방식이 아니다.

### 3.3 질문 → SQL 흐름
- 기본 흐름(에이전트가 스킬을 따라 수행): `memory recall`(비슷한 과거 질문-SQL) → `memory fetch`(관련 모델·컬럼·규칙) → 에이전트가 MDL 기준 SQL 작성 → `dry-plan`(실행 SQL로 확장, 규칙 필터 주입, 열 접근 제어 적용) → 실행 → `memory store`(확인된 쌍 저장). [docs/core/concepts/correctness.md "What the agent sees"]
- 즉 **LLM이 SQL 문자열을 쓴다**. 우리 프로젝트의 "LLM은 호출 계획만, 숫자는 파이썬 도구가 계산"과는 구조가 다르다. SQL이 최종 계산을 수행한다는 점에서 숫자의 출처는 DB 엔진이지 LLM이 아니라는 면은 같지만, 계획의 표현형이 도구 호출 목록이 아니라 자유 SQL이라는 차이가 있다. (비교는 본 보고서의 해석)
- `wren ask`는 쿼리를 실행하지 않고 사용자 질문을 템플릿(guided/direct)에 끼워 에이전트용 프롬프트를 출력할 뿐이다. [core/wren/src/wren/ask.py 1~10행]
- 질문→SQL 변환을 서버 안에서 직접 하는 LLM 호출 코드는 이번에 읽은 범위에서는 못 찾았다(에이전트가 외부에서 수행). 서버형 예제 `serve agent`는 별도 "비공개 워커" 모델(NVIDIA 엔드포인트 예시)이 SQL을 작성한다. [examples/serve-agent/README.md "Start the service"]

### 3.4 안전장치 (실행 전 검증·허용 범위)
있음. 두 층이다.

(1) 일반 CLI 경로
- `dry-plan`은 DB 없이 SQL을 확장해 컬럼명 오류 시 사용 가능한 컬럼을 알려준다. [correctness.md]
- 설정 `strict_mode`(MDL에 없는 테이블 참조 거부)와 `denied_functions`(특정 함수 차단)가 있다. 기본값은 strict_mode=False. [core/wren/src/wren/config.py 32~33행, core/wren/.claude/CLAUDE.md]

(2) 거버넌스 모드(`wren serve agent` / 통제된 `serve mcp`) — 계획 후 승인 구조와 가장 가까운 부분
- SQL을 쓰는 워커와 결과를 받는 외부 플래너가 분리되어 있다. 외부 플래너는 SQL과 워커의 최종 문장을 받지 못하고, 호스트가 만든 표와 증거(evidence)만 받는다. [examples/serve-agent/README.md "Opt-in ad-hoc analysis", agent_sql.py 1~5행]
- 모든 SQL은 호스트의 게이트(`AdHocSQL.prepare`)를 통과해야 한다: 읽기 전용, 크기 제한, 거부 목록, 선언된 기본 규칙. [agent_sql.py 7~9행]
- 읽기 전용 게이트는 허용 함수 목록(allowlist) 방식이다. 쓰기·DDL·여러 문장·미등록 함수·리더 함수는 실패 처리(fail closed). [read_only.py 1~6, 16~31행, 67~71행]
- 제한: 조인 4개 이하, 출력 열 20개 이하, 행 100개 이하, 초과 결과는 자르지 않고 실패. 재귀·상관 쿼리·문자열 합성 결과 열 거부. [examples/serve-agent/README.md "Opt-in ad-hoc analysis"]
- 거부 컬럼 `denied_columns`: 프로젝트 규칙 파일의 front-matter에 선언하면 메타데이터 노출과 SQL 참조(CTE·서브쿼리·`SELECT *` 확장 포함)를 모두 막는다. 위반 시 작업이 `denied`로 즉시 종료. [examples/serve-agent/README.md "Denied columns", "Why a run_sql statement is refused"]
- 선언된 기본 규칙(예: 테스트 주문 제외, 허용 상태값만 포함, 환율 조인 필수)을 호스트가 SQL 구문 트리에서 검사한다. 위반은 `declared_default_missing`, 판정 불가는 `declared_default_undetermined`로 **실행 전 거부**된다. 필터 존재 여부를 문자열로 찾지 않고 상태 도메인에서 남는 값 집합을 계산한다. [examples/serve-agent/README.md "Declared default rules"]
  - 이것이 "조건이 빠지는 문제"에 대한 가장 직접적인 대응이다: 조건이 빠진 SQL은 실행되지 않는다.
- 규칙 위반을 사용자에게 되묻는 `clarify`(`needs_clarification`) 경로가 있다. 이때 질문 문구가 정책 프리셋에 따라 노출 범위가 다르다. [같은 README "Clarification questions follow the same policy"]
- 결과에 `evidence`(적용된 기간, 규칙 검사 결과 요약, 검증 불가 항목 id 등)를 붙여 플래너가 질문과 실제 계산을 대조하게 한다. 문서가 스스로 "증거가 질문 이해를 보증하지는 않는다"고 한계를 적는다. [같은 README "What a result's evidence tells the planner"]
- 정책 엔진: Cedar 정책 파일로 도구 호출, 쿼리 실행, 메타데이터·결과 수신을 항목별로 허용. 프리셋은 범위를 좁히는 방향으로만 작동. [examples/serve-agent/policy.cedar, README "Named policy presets"]
- 예산: 작업당 SQL 시도 수(기본 6), 사전 거부 2회, 엔진 실패 후 재시도 1회. 초과 시 작업 종료. 감사 로그(`audit_path`)는 옵션이며 기본 꺼짐. [README "Why a run_sql statement is refused", "Execution limits"]
- 이 서비스는 POSIX 프로세스 그룹을 쓰므로 Windows 미지원이라고 명시. [README 7행]
- 한계(문서 자신의 서술): 임의 산문 규칙은 모델이 해석하며 정확성의 증거가 아니다. 이 게이트는 OS 샌드박스가 아니다. 사용자별 접근 제어 시스템이 아니라 프로젝트 단위 거부 목록이다. [README "Opt-in ad-hoc analysis", "Execution limits", "Denied columns"]

---

## 4. 평가·벤치마크

### 4.1 evals 폴더 구성
`evals/spodbtify_ab/` 하나뿐이다(.gitignore, README.md, agent_output.schema.json, run_eval.py, spodbtify_ab_eval.json). [evals/ Glob 결과]

### 4.2 무엇을 재는가
- 같은 DuckDB(Spotify Million Playlist 데이터, 19개 테이블)에 대해 두 워크플로를 비교한다. `schema_only`(물리 스키마만), `dbt_integrated`(dbt 설명·참조·계층·테스트·컴파일 SQL까지 가져온 컨텍스트). 즉 **메타데이터 풍부도가 정확도에 얼마나 기여하는지** 보는 A/B다. [evals/spodbtify_ab/README.md, spodbtify_ab_eval.json 1~95행]
- 질문은 분석형 20개(상위 아티스트 협업자, HHI 분포 등). [spodbtify_ab_eval.json 98~117행]
- 통제 조건: 같은 DB, 같은 dbt 빌드, 같은 모델, 같은 질문 순서, 워크플로마다 새 세션, 상대 워크플로 정보 누설 금지. [spodbtify_ab_eval.json 33~40행]

### 4.3 점수 산정
- 질문당 3개 차원(correct_table, correct_sql, correct_answer)을 각각 0/1/2점으로 채점해 질문당 최대 6점, 총 120점. 채점자는 **사람 또는 외부 채점자**이고, 스크립트는 합산만 한다. [spodbtify_ab_eval.json 119~150행, run_eval.py 24, 132~153행]
- 정답(expected result)이 spec에 들어 있는지는 읽은 범위(1~150행)에서는 못 봤다. 150행 이후는 읽지 않음 → '확인 못 함'.
- `run_eval.py`는 spec 검증(`validate`), 프롬프트 생성(`prompt`), 임의 에이전트 명령 실행(`run-agent`), 점수 템플릿 생성, 합산·워크플로 간 격차 출력(`score`)을 제공한다. 에이전트 출력은 JSON(`selected_tables`, `sql`, `answer`, `notes`)이다. [run_eval.py 349~430행, README "Scoring Contract"]
- 데이터셋과 dbt 프로젝트는 저장소에 없다(환경 변수로 경로 지정). 답안·점수 파일은 커밋 금지. [evals/spodbtify_ab/README.md "Dataset", 마지막 줄]
- README의 "What's next"에 "golden eval runner"가 예정으로 적혀 있고, 상용 비교표에는 평가·AI Advisor가 상용 기능으로 나온다. 즉 OSS의 평가 도구는 이 A/B 하나뿐이다. [README.md 193행, docs/core/concepts/oss_vs_commercial.md 34행]
- 이번 커밋에서 정확도 수치(결과 점수)는 저장소에서 찾지 못했다(점수 파일은 커밋하지 않는 규칙). → '확인 못 함'.
- 별개로 Rust 엔진은 sqllogictest와 insta 스냅샷 테스트를 쓴다(엔진 회귀용, 에이전트 정확도 평가 아님). [.claude/CLAUDE.md]

---

## 5. 우리 프로젝트(Task Insight)에 적용할 아이디어

전제 비교: 우리는 "LLM이 허용 목록 도구 9개의 호출 계획만 작성 → 사용자 승인 → 파이썬 도구가 계산"이다. WrenAI는 "LLM이 SQL 작성 → 게이트 검증 → 엔진 실행"이다. 구조가 달라 코드 이식보다 **개념 이식**이 맞다. 아래는 모두 내 해석이며 우리 코드는 읽지 않았으므로 모듈명은 역할 기준의 제안이다.

### 5.1 가져올 만한 아이디어
1. **컬럼 의미 사전(MDL 축소판) 도입 — 컬럼 매핑 모듈**
   - WrenAI는 컬럼마다 설명·단위·열거값 의미·NULL 의미를 선언 파일에 둔다. [gap_catalog.md, mdl.md]
   - 적용: CSV 업로드 직후 컬럼별 `{역할(날짜/금액/상태/ID…), 단위, 열거값 의미, NULL 의미, 동의어}`를 담는 JSON 사전을 만들고, 사용자에게 확인받은 뒤 확정. LLM 호출 계획에는 원본 컬럼명이 아니라 이 사전의 정규 이름만 쓰게 한다.
2. **트리거 규칙 기반 후보 탐지 — 프로파일링 단계**
   - 이름 패턴(`*_amount`, `*_at`, `status`)과 고유값 ≤ 30 같은 결정적 규칙으로 후보를 뽑는다. [gap_catalog.md 35, 44행] 우리는 LLM 없이 파이썬으로 구현 가능하다(숫자를 LLM이 만들지 않는 원칙과 충돌 없음). LLM은 "제안"만, 확정은 사용자.
3. **선언된 기본 조건을 실행 전에 강제 — 계획 검증 단계**
   - WrenAI는 조건 누락을 거부(`declared_default_missing`)하고, 판정 불가도 별도 코드로 거부한다. [examples/serve-agent/README.md "Declared default rules"] 적용: 계획이 승인 화면에 가기 전 검증기가 "이 컬럼 사전에 기본 필터(테스트 행 제외, 허용 상태값)가 선언돼 있는데 계획에 빠졌다"를 검사해 자동 보완 또는 되묻기. "조건이 빠지는 문제"에 직접 대응.
   - 핵심 설계 포인트는 문자열 검색이 아닌 구조 검사, 그리고 "판정 불가"를 통과가 아닌 거부로 두는 fail-closed다.
4. **막히는 질문은 되묻기로 — 질문 해석 단계**
   - `needs_clarification` 흐름처럼 규칙 위반이나 모호성이 있으면 사용자에게 구체적 선택지를 제시한다. 우리의 승인 단계와 자연스럽게 합칠 수 있다(승인 화면에 "상태 범위 선택" 입력 추가).
5. **증거(evidence) 객체를 승인·결과 화면에 — 결과 표시 모듈**
   - 실제 적용된 기간, 적용된 규칙, 검증 불가 항목을 결과에 붙인다. [같은 README "What a result's evidence tells the planner"] 우리는 도구 호출 로그에서 같은 정보를 결정적으로 만들 수 있다. "검증 불가" 항목을 숨기지 않고 노출하는 점이 참고할 만하다.
6. **허용 목록 + fail closed 원칙 재확인**
   - read_only.py는 함수 허용 목록을 쓰고, 목록에 없으면 거부한다. [read_only.py 16~31, 67~71행] 우리 도구 9개 허용 목록과 같은 철학이다. 일반 에이전트로 확장할 때도 "도구 추가 = 목록 + 인자 스키마 + 검증기 동시 추가"를 지키는 근거로 쓸 수 있다.
7. **평가 설계: 메타데이터 A/B와 3차원 채점 — 평가 모듈**
   - 같은 질문 세트를 "컬럼 사전 없음 vs 있음"으로 돌려 사전의 기여도를 재는 설계는 우리 과제(다른 구조 CSV)에 그대로 맞는다. 채점 차원은 우리 구조에 맞게 `올바른 컬럼 선택 / 올바른 도구·인자 / 올바른 숫자`로 바꿀 수 있다. [spodbtify_ab_eval.json 119~150행] 우리는 숫자를 파이썬이 계산하므로 마지막 차원을 자동 채점(정답 값 비교)할 수 있다는 이점이 있다(WrenAI는 사람 채점).
8. **시드 질의/확인된 쌍 저장 — 선택적 확장**
   - 승인된 (질문, 계획) 쌍을 저장해 다음 유사 질문의 예시로 재사용. [correctness.md 6단계] 승인된 것만 저장하는 조건을 지키면 안전하다.
9. **감사 로그 — 승인 구조 보강**
   - 시도 횟수·거부 코드를 값 없이 기록하는 이벤트 로그 설계(SQL·키 미기록). [examples/serve-agent/README.md "An optional operator-only audit_path"]

### 5.2 가져오면 안 되는 것 / 충돌 지점
- **LLM이 자유 SQL을 작성하게 하는 구조**: 우리 원칙(허용 목록 도구, 계획만 작성, 사용자 승인 후 파이썬 계산)과 충돌한다. SQL 게이트는 강력하지만 문서가 인정하듯 산문 규칙·질문 이해는 증명하지 못한다. 도입하지 말 것.
- **임베딩 기반 스키마 검색을 필수 경로로**: 의미 매핑의 결정성이 떨어진다. 후보 제안용으로만 쓰고 확정은 사전·사용자 승인으로.
- **LLM 추론으로 컬럼 의미를 확정(Lane 3)**: WrenAI도 "추론"으로 표시하고 신뢰도를 붙인다. 우리도 확정이 아닌 제안으로만 쓸 것.
- **상용 영역의 기능 모사**: 행/열 단위 사용자별 보안, GenBI UI, 에이전틱 모드는 상용이라고 문서가 밝힌다. 라이선스 문제가 아니라도 범위 밖이다. [README.md "Open core", oss_vs_commercial.md]
- **상표**: "Wren", "WrenAI", 로고는 사용 불가. [LICENSE "Trademarks"]
- **의존성**: Warble 컴파일러/런타임, NVIDIA 호스트 엔드포인트, Cedar 등은 우리 스택(Gemini)과 무관하며 외부 서비스 의존이 생긴다. [examples/serve-agent/README.md]

### 5.3 코드 복사 가능 범위 (라이선스 근거)
- **복사 가능(조건부)**: `core/**`, `sdk/**`, `skills/**`, `examples/**`, 루트 파일은 Apache-2.0. 복사·수정·재배포 시 Apache 조건(라이선스 사본 포함, 저작권·고지 유지, 변경 표시, NOTICE가 있으면 유지)을 지켜야 한다. 이 요건은 Apache 2.0 표준 조건에 대한 일반 지식이며, 전문 전체를 대조하지는 않았다. [LICENSE 지도, LICENSE-APACHE-2.0]
  - 예: `core/wren/src/wren/read_only.py`의 허용 목록 아이디어는 Apache 범위라 원칙적으로 복사 가능하나, 이 파일은 sqlglot에 의존하는 SQL 전용 코드라 우리 쪽에 쓸 곳이 거의 없다(추정).
  - 예: `gap_catalog.md`의 범주 정의는 `core/wren/src/wren/skills_content/` 아래이므로 `core/**`로서 Apache-2.0. 범주 표를 우리 프로파일링 규칙으로 옮길 때는 출처 표기를 권한다.
- **복사 시 주의(CC BY 4.0)**: `docs/**`의 문장·표·다이어그램을 우리 문서에 옮기려면 CC BY 4.0에 따라 저작자 표시, 라이선스 링크, 변경 표시가 필요하다. 코드가 아니라 문서이므로 우리 프로젝트 문서에 인용할 때만 해당. [LICENSE 지도, CONTRIBUTING.md 36행]
- **`evals/**`**: 지도에 이름이 없어 Apache-2.0(루트 기본)으로 **추정**. 복사 전에 확인 필요. [LICENSE 지도]
- **AGPL**: 현재 적용 경로가 없으나 향후 도입 가능성을 LICENSE가 예고한다. 가져온 시점의 커밋 SHA를 기록하고, 이후 버전을 가져올 때는 LICENSE 지도를 다시 확인할 것.
- 결론: 코드를 복사하는 경우는 위 폴더의 라이선스가 허용하는 범위에서만 하고, 복사한 파일마다 출처 경로·커밋 SHA·라이선스를 주석으로 남긴다. 다만 이번 분석은 개념 이식을 권하며, 복사가 꼭 필요한 파일은 현재 확인된 것이 없다.

---

## 6. 확인하지 못한 것과 한계

- 커밋 날짜: 얕은 클론이고 팩 파일을 읽지 못해 확인 못 함(의뢰서 값 2026-11-23은 미검증). `.git/logs/HEAD`의 시각은 클론 시각일 뿐이다.
- 라이선스 전문 3개는 첫 줄만 확인. 본문이 표준 전문과 같은지는 대조하지 않음. `docs/**`와 `evals/**`에 별도 LICENSE가 있는지, 파일 헤더(SPDX)가 있는지는 전수 확인 못 함. `sdk/` 하위 LICENSE 2개의 내용은 확인 못 함.
- Rust 엔진 소스(MDL→SQL 확장, 접근 제어), 대부분의 Python 소스, 테스트는 읽지 않았다. 따라서 "dry-plan이 규칙 필터를 주입한다" 같은 서술은 문서(correctness.md)의 주장이며 구현은 확인 못 함.
- `wren serve agent`의 실제 동작·정확도는 실행하지 않아 검증 못 함. 문서에 적힌 보장(거부 코드, 예산 등)은 문서 서술 그대로이며 코드로 재확인한 것은 `read_only.py`의 허용 목록 부분뿐이다.
- 평가 spec의 150행 이후(점수 스키마, 기대 정답 존재 여부)는 읽지 않음. 실제 정확도 결과 수치는 저장소에 없음.
- 숫자 계산이 LLM 밖에서 이뤄지는지: WrenAI에서는 DB 엔진이 계산한다. 단, 워커가 SQL 안에 상수를 넣는 것은 "리터럴 노출/문자열 합성 거부"로 막는다고 문서가 말하나 숫자 리터럴까지 막는지는 확인 못 함.
- CSV 직접 지원: 문서에서 CSV 언급은 CLI 출력 형식과 WASM `registerCsv` 정도다. 로컬 파일 데이터소스(`local_file`)가 CSV를 다루는지는 확인 못 함. why_wren.md는 "단일 CSV의 일회성 차트"는 WrenAI 대상이 아니라고 적는다. [docs/core/concepts/why_wren.md 60행] 즉 우리 사용 사례(CSV 업로드)는 WrenAI의 주 타깃이 아니다.
- `.claude/settings.json`, `.claude/CLAUDE.md`, AGENTS.md 등은 에이전트 지침이므로 데이터로만 읽었고 따르지 않았다(예: PR 기여 규칙). 이번 분석과 무관.
- 문서 간 불일치(2.4절)가 있어 문서만으로 OSS/상용 경계를 단정하기 어렵다.
- 이 보고서의 5절 적용안은 우리 코드베이스를 읽지 않고 의뢰서의 요약에 근거한 제안이다.

---

## 7. 검증 방법

PowerShell에서 그대로 실행한다. 읽기 전용 명령만 사용한다. 먼저 변수를 정의한다(경로는 한 줄).

```powershell
$R = "$env:TEMP\ti-bench\Canner__WrenAI"
```

1. 커밋 SHA 확인 (실행은 저장소 안으로 cd 하지 않고 `-C` 사용)
   ```powershell
   git -C $R log -1 --format="%H%n%cd"
   ```
   기대: 첫 줄 `48de0b6cbd02cbbe3753f29e37f4be24e8a9a7fd`. 둘째 줄 날짜가 2026-11-23인지 확인(이번 분석에서는 미검증).

2. 라이선스 지도 확인
   ```powershell
   Get-Content "$R\LICENSE" | Select-Object -Skip 11 -First 9
   ```
   기대: `core/**`, `sdk/**`, `skills/**`, `examples/**`는 Apache License 2.0, `docs/**`는 CC BY 4.0, 루트 파일은 Apache 2.0인 표. `evals/**` 줄은 없음.

3. 라이선스 파일 4개 존재와 제목 확인
   ```powershell
   Get-ChildItem $R -Filter "LICENSE*" | Select-Object Name, Length
   Get-Content "$R\LICENSE-AGPL-3.0" -TotalCount 2
   ```
   기대: LICENSE, LICENSE-AGPL-3.0, LICENSE-APACHE-2.0, LICENSE-CC-BY-4.0 네 개. 둘째 명령은 `GNU AFFERO GENERAL PUBLIC LICENSE` / `Version 3, 19 November 2007`.

4. 매니페스트의 license 필드가 모두 Apache-2.0인지
   ```powershell
   Get-ChildItem $R\core,$R\sdk -Recurse -Include Cargo.toml,pyproject.toml,package.json -ErrorAction SilentlyContinue | Select-String -Pattern 'license' | Select-Object -First 20 | ForEach-Object { "$($_.Path.Replace($R,'')) : $($_.Line.Trim())" }
   ```
   기대: `Apache-2.0` 값만 나옴(`license.workspace = true` 같은 줄은 워크스페이스 상속). AGPL 값은 없음. 참고: 이 명령은 의존 폴더(node_modules 등)가 없다는 전제에서 결과가 20줄 이내여야 한다.

5. AGPL이 LICENSE 외에 선언된 곳이 없는지
   ```powershell
   Get-ChildItem $R -Recurse -File -Include *.md,*.toml,*.yml,*.json,*.py -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\\.git\\|LICENSE-AGPL|package-lock|Cargo.lock|uv.lock' } | Select-String -Pattern 'AGPL' | Select-Object -First 10 Path, LineNumber
   ```
   기대: LICENSE, CHANGELOG.md, CONTRIBUTING.md에서만 나옴(소스에 AGPL 선언 없음).

6. evals 폴더 구성과 채점 규칙
   ```powershell
   Get-ChildItem "$R\evals" -Recurse -File | Select-Object -ExpandProperty FullName
   Select-String -Path "$R\evals\spodbtify_ab\spodbtify_ab_eval.json" -Pattern '"max_total"|"max_score_per_question"|"id": "(correct_table|correct_sql|correct_answer)"'
   ```
   기대: 파일 5개(.gitignore, README.md, agent_output.schema.json, run_eval.py, spodbtify_ab_eval.json). 두 번째 명령에서 `max_score_per_question: 6`, `max_total: 120`, 세 차원 id.

7. 평가 질문 20개와 워크플로 2개
   ```powershell
   (Select-String -Path "$R\evals\spodbtify_ab\spodbtify_ab_eval.json" -Pattern '^\s*\{"id": \d+, "text"').Count
   Select-String -Path "$R\evals\spodbtify_ab\spodbtify_ab_eval.json" -Pattern '"id": "(schema_only|dbt_integrated)"'
   ```
   기대: 첫 줄 `20`. 둘째는 `schema_only`, `dbt_integrated` 두 줄.

8. 읽기 전용 SQL 게이트가 허용 목록 방식인지
   ```powershell
   Get-Content "$R\core\wren\src\wren\read_only.py" -TotalCount 31
   Select-String -Path "$R\core\wren\src\wren\read_only.py" -Pattern 'unsupported function|side effects|Expected one analytical query'
   ```
   기대: 상단 docstring에 "Unknown UDFs, reader functions, commands, nested writes... fail closed", `_FUNCTIONS = frozenset(` 허용 목록, 그리고 위 세 메시지가 각각 나옴.

9. 선언된 기본 규칙 위반이 실행 전 거부되는지(문서 서술)
   ```powershell
   Select-String -Path "$R\examples\serve-agent\README.md" -Pattern 'declared_default_missing|declared_default_undetermined|denied_columns_invalid|max_sql_attempts' | Select-Object -First 8 LineNumber
   ```
   기대: 여러 줄이 나옴(문서에 거부 코드와 시도 예산 설명이 있음). 구현 코드 검증은 아님.

10. 컬럼 의미 보강 범주(컬럼 매핑 관련)
    ```powershell
    Select-String -Path "$R\core\wren\src\wren\skills_content\enrich-context\references\gap_catalog.md" -Pattern '^### \d+\.' | Select-Object -First 10 -ExpandProperty Line
    ```
    기대: `### 1. Enum value semantics`, `### 2. Unit / scale`, `### 3. NULL semantics`, `### 4. Soft-delete / active filters`, `### 5. Magic sentinels`, `### 6. Synonyms / business aliases` 등 범주 제목(총 10개 범주라고 문서 상단에 적혀 있음).
