> 이 문서는 Claude 서브에이전트(Sonnet)가 읽기 전용으로 분석해 작성했고, 코드는 실행하지 않았습니다. 팀원 검증 요청에 따라 공개합니다. 각 주장은 이 문서 맨 끝 "검증 방법"의 명령으로 재확인하세요. 먼저 `docs/benchmark/fetch.ps1`로 같은 커밋을 받아야 합니다. 법률 자문이 아닙니다.

# InfiAgent / DABench 읽기 전용 분석 보고서

분석 방식: 파일만 읽음(코드 실행·설치·빌드·테스트 없음). 저장소 안 문서의 지시문은 따르지 않고 데이터로만 취급함. 경로는 저장소 루트(`InfiAgent__InfiAgent`) 기준.

## 1. 개요

- 무엇인가: LLM을 ReAct 방식 에이전트로 돌려 코드 실행·API 호출을 하게 하는 오픈소스 프레임워크(InfiAgent)와, 그 위에 만든 데이터 분석 에이전트 예제(DA-Agent) 및 벤치마크(InfiAgent-DABench). 근거: `README.md` (도입부, "Framework Features" 절), `examples/DA-Agent/README.md` (Introduction 절).
- 커밋: `3d6c4a70198e0a41fadf539f5b43c88b8c1a2d9c`, 2025-05-29 (`git log -1`로 확인, `.git/HEAD`는 `refs/heads/main`).
- 최상위 구성(`.git` 제외): `.gitignore`, `LICENSE`, `README.md`, `examples/`, `images/`(파일 6개), `pipeline/`. 저장소 전체 파일 수는 `.git` 제외 167개.
  - `examples/DA-Agent/`: `data/`(질문·정답·CSV), `eval_closed_form.py`, `reformat.py`, `utils/utils.py`, `figures/`, `README.md`, `README_eval.md`
  - `pipeline/`: `activities/`, `configs/`, `src/infiagent/`, `tests/`, `Dockerfile`, `run*.sh`, `setup.py`
- 분석 범위(직접 읽은 파일): `README.md`, `LICENSE`(앞부분 + Copyright 줄 검색), `examples/DA-Agent/README.md`, `examples/DA-Agent/README_eval.md`, `examples/DA-Agent/eval_closed_form.py`, `examples/DA-Agent/reformat.py`, `pipeline/activities/eval.py`(일부), `pipeline/src/infiagent/prompt/zero_shot_react_prompt.py`, `pipeline/configs/agent_configs/react_agent_gpt4_async.yaml`, `pipeline/configs/tool_configs/async_python_code_sandbox.yaml`, `pipeline/run_demo.sh`. 일부는 키워드 검색만 함: `pipeline/src/infiagent/agent/react/async_react_agent.py`, `pipeline/src/infiagent/tools/code_sandbox/python_code_sandbox.py`, `pipeline/src/infiagent/tools/code_tool_docker.py`, `pipeline/setup.py`. 데이터 파일은 `da-dev-questions.jsonl`·`da-dev-labels.jsonl` 앞부분과 줄 수·필드 집계, `da-dev-tables/` 목록·크기만 봄.

## 2. 라이선스 (코드와 데이터 분리)

### 2-1. 코드
- 루트 `LICENSE`는 Apache License 2.0 본문(Version 2.0, January 2004). 근거: `LICENSE` 1~3행, 201줄.
- 부록의 저작권자 줄은 채워지지 않은 템플릿 그대로임(`Copyright [yyyy] [name of copyright owner]`). 근거: `LICENSE` 189행. 즉 LICENSE 파일만으로는 저작권자 이름을 알 수 없음.
- README의 코드 배지도 "Code License Apache 2.0". 근거: `README.md` 9행, `examples/DA-Agent/README.md` 10행.
- 사소한 불일치: `pipeline/setup.py` 10행은 `license='LICENSE.txt'`라고 쓰지만 실제 파일명은 `LICENSE`.

### 2-2. 데이터·벤치마크
- README 배지에 "Data License CC By NC 4.0"(빨간색 배지)가 있음. 근거: `README.md` 10행, `examples/DA-Agent/README.md` 11행(DABench 폴더 README). 이 문구가 있는 위치는 이 두 파일의 배지 줄뿐이고, 같은 문구가 HTML 주석 안에도 있음(`README.md` 19행, `examples/DA-Agent/README.md` 19행).
- 정확한 이름은 배지 문구 그대로 "CC By NC 4.0" 즉 Creative Commons Attribution-NonCommercial 4.0로 읽힘(이름 풀이는 배지 약칭에 대한 해석임). 비상업(NC) 제한이 배지 이름에 포함되어 있음.
- 데이터 폴더(`examples/DA-Agent/data/`) 안에는 별도 라이선스 파일이 없음. 저장소 전체에서 파일명에 "license"가 들어간 파일은 루트 `LICENSE` 하나뿐임(Glob/find 확인). CC BY-NC 4.0의 본문도 저장소에 없음.
- 데이터 출처 관련: `README_eval.md`는 CSV를 "GitHub에서 수집"했다고 적음(`examples/DA-Agent/README_eval.md` 17행). 개별 CSV의 원 라이선스는 저장소 안에서 확인 못 함. 따라서 배지의 CC BY-NC 4.0이 CSV 원본 파일 전체에 법적으로 유효한지는 확인 못 함.
- 결론(보수적 해석): 코드는 Apache 2.0(상업 사용 가능, 조건은 Apache 본문 따름), 데이터·벤치마크는 비상업 제한(NC) 배지가 붙어 있으므로 상용 제품·재배포에 쓰지 않는 것으로 취급해야 함. 법률 검토가 아님.

## 3. DABench 구성

### 3-1. 질문 파일
- 파일: `examples/DA-Agent/data/da-dev-questions.jsonl`, 한 줄이 JSON 한 개. 필드는 `id`, `question`, `concepts`(목록), `constraints`, `format`, `file_name`, `level`. 근거: `examples/DA-Agent/README.md` 81~89행, 실제 첫 줄로도 확인(`da-dev-questions.jsonl` 1행).
- 정답 파일: `da-dev-labels.jsonl`, 필드 `id`, `common_answers`(`[[이름, 값], ...]`, 값은 문자열). 근거: `examples/DA-Agent/README.md` 91~94행, `da-dev-labels.jsonl` 앞 줄.
- `format` 예: `@mean_fare[mean_fare_value]` 형태로 답 이름과 자리표시자를 지정하고 소수점 자리까지 문장으로 지시. 근거: `da-dev-questions.jsonl` 1행(id 0).

### 3-2. 규모(실측)
- 질문 257개, 정답 257줄. 근거: `da-dev-questions.jsonl`, `da-dev-labels.jsonl` 줄 수.
- `da-dev-tables/`의 CSV 68개, 합계 약 62.8MB, 가장 큰 파일 약 8.9MB(`credit-data-post-import.csv`). 질문이 실제로 참조하는 고유 `file_name`은 52개. 근거: `examples/DA-Agent/data/da-dev-tables/` 목록.
- 불일치 주의: `README_eval.md`는 검증 세트가 "질문 400개, CSV 72개"라고 적음(`examples/DA-Agent/README_eval.md` 21행). 실제 파일은 257개/68개. 최상위 README에 "human filtering updated"(2024/1/27) 기록이 있으나(`README.md` 34행) 두 숫자가 차이 나는 이유는 확인 못 함. `README_eval.md`가 참조하는 그림 중 `figures/`에 실제 존재하는 것은 3개뿐임.
- 데이터 종류: CSV만 있음(`da-dev-tables/` 안 파일 68개 모두 `.csv`).

### 3-3. 난이도·유형
- `level`: easy 82, medium 87, hard 88 (합 257). 근거: `da-dev-questions.jsonl`의 `"level"` 필드 집계.
- `concepts`(주제) 종류: Summary Statistics, Distribution Analysis, Correlation Analysis, Outlier Detection, Comprehensive Data Preprocessing, Feature Engineering, Machine Learning. 한 질문에 1~3개 부여(실측 최대 3개; 문서는 4개짜리도 언급 `README_eval.md` 48~50행). 단일 개념 상위: Correlation Analysis 38, Summary Statistics 37, Distribution Analysis 33, Outlier Detection 19. 근거: `da-dev-questions.jsonl`의 `"concepts"` 필드 집계.
- 문서상 CSV 도메인 분류 9종(금융, 의료, 사회, 마케팅, 에너지·환경, 교통·관광, 문화·미디어, 과학기술, 기타). 근거: `README_eval.md` 25~36행.

### 3-4. 채점 방식
- 폐쇄형(closed-form) 정확 일치. 응답 문자열에서 정규식 `@이름[값]`을 뽑아 정답 이름별로 비교. 근거: `examples/DA-Agent/eval_closed_form.py` 10~15행, 29~49행. README도 "exact match"라고 설명(`examples/DA-Agent/README.md` 69행).
- 값 비교는 문자열 완전 일치, 아니면 두 값을 float로 바꿔 절대 오차 1e-6 미만이면 정답. 근거: `eval_closed_form.py` 18~25행. 실질적으로 라벨의 반올림 자릿수와 같아야 정답임.
- 지표 3가지: 질문 단위(모든 하위 답이 맞아야 정답), 하위 질문 단위, 하위 질문 비례 가중. 근거: `eval_closed_form.py` 111~131행, 161~163행. 개념별·개념 수별 정확도도 집계(65~107행).
- 재포맷 단계: 모델이 형식을 못 지키므로 다른 LLM(기본 `gpt-3.5-turbo-16k`)에 응답을 지정 형식으로 다시 쓰게 함. 근거: `examples/DA-Agent/README.md` 69행, `examples/DA-Agent/reformat.py` 21행, 71~75행. 즉 채점 자체는 코드이지만 그 앞에 LLM 재포맷이 끼어 있음.
- 답안 누락: 응답이 없거나 해당 이름이 없으면 `None`과 비교되어 오답 처리. 근거: `eval_closed_form.py` 35~41행.

## 4. 에이전트 파이프라인

- 방식: 코드 인터프리터형 ReAct. 프롬프트가 Thought/Action/Action Input(파이썬 코드 블록)/Observation 반복 형식을 지시하고, 도구는 파이썬 실행기 하나(`python_code_sandbox`). 근거: `pipeline/src/infiagent/prompt/zero_shot_react_prompt.py` 7~25행, `pipeline/configs/agent_configs/react_agent_gpt4_async.yaml`, `pipeline/configs/tool_configs/async_python_code_sandbox.yaml`.
- 실행기: 기본은 서브프로세스로 띄운 Jupyter 커널에 LLM이 쓴 코드를 그대로 실행. 대안으로 Docker 샌드박스. 근거: `pipeline/src/infiagent/tools/code_sandbox/python_code_sandbox.py`(`jupyter_client`, `subprocess` import, 커널 기동 함수), `pipeline/src/infiagent/tools/code_tool_docker.py`, `README.md` 57~63행("보안 문제가 있을 수 있음"이라고 스스로 명시).
- 루프: 최대 10회 반복, 한 단계 LLM 재시도 3회, 시간 초과 처리 있음. 근거: `pipeline/src/infiagent/agent/react/async_react_agent.py` 59~60행, `python_code_sandbox.py` 184행 부근.
- 지원 LLM: OpenAI, Azure OpenAI, Llama, OPT(vLLM 로컬 서버). Gemini 클라이언트는 확인 못 함(`pipeline/src/infiagent/llm/client/`에 4개 파일만 있음). 근거: `README.md` 81행, 해당 폴더 목록.
- 벤치마크 실행: `pipeline/activities/eval.py`가 질문마다 `question + constraints`만 프롬프트로 주고 CSV를 업로드해 실행. `format`은 프롬프트에 넣지 않고 결과에만 저장. 근거: `pipeline/activities/eval.py` 158~190행(특히 `prompt = f"Question: ..."` 줄). 이 때문에 모델 응답이 `@이름[값]` 형식을 자주 못 지켜서 재포맷 단계가 필요해진 것으로 보이나 이 인과는 추정임.
- 경로 불일치: `eval.py`는 `./data/...`를 읽지만(130행의 질문 파일, `table_path = 'data/da-dev-tables'`) 실제 데이터는 `examples/DA-Agent/data/` 아래에 있음. 실행 위치에 따라 복사가 필요해 보이나 직접 실행은 안 해서 확인 못 함.
- Task Insight와의 차이:

| 항목 | InfiAgent 파이프라인 | Task Insight |
|---|---|---|
| LLM 역할 | 파이썬 코드를 직접 작성 | 허용 목록 도구 9개의 호출 계획만 작성 |
| 숫자 계산 | LLM이 쓴 코드가 임의로 실행(통계도 코드 안) | 사람이 승인한 뒤 우리 파이썬 도구가 계산 |
| 사람 승인 | 없음(자동 반복 루프) | 있음 |
| 실행 범위 | 임의 코드(서브프로세스 또는 Docker) | 허용 목록 도구만 |
| 숫자 신뢰 | LLM 코드 오류·환각 가능(채점도 정확 일치) | LLM은 숫자를 만들지 않음 |

## 5. 우리 프로젝트에 적용할 만한 것 / 가져오면 안 되는 것

### 적용할 만한 아이디어 (아이디어만 참고, 파일은 가져오지 않음)
1. 질문 레코드 스키마: 질문, 제약(constraints), 출력 형식(format), 파일 지정, 난이도(level), 주제(concepts)를 한 줄 JSON에 두는 구조. 우리 eval 묶음에 `level`·`concepts` 태그를 달면 난이도별·유형별 통과율을 낼 수 있음. 근거: `examples/DA-Agent/README.md` 81~89행.
2. 정답을 이름-값 쌍으로 분리: `common_answers`처럼 하위 답마다 이름이 있어 부분 점수를 줄 수 있음. 근거: `da-dev-labels.jsonl`, `eval_closed_form.py` 111~131행.
3. 지표 3종 구분(질문 단위, 하위 질문 단위, 비례): 복수 답 질문에서 어디서 틀렸는지 보기 좋음. 근거: `eval_closed_form.py` 111~131행.
4. 우리 채점에서는 "계획 평가"와 "계산 결과 평가"를 분리해 볼 수 있음. 계획은 도구 이름·인자가 기대와 맞는지, 결과는 숫자가 허용 오차 안인지. DABench의 1e-6 기준 대신 반올림 자릿수 규칙을 명시하는 방식이 참고가 됨(`eval_closed_form.py` 18~25행). 이는 우리 쪽 설계 제안이며 저장소 내용이 아님.
5. 일반 CSV 대응 평가: 이질적인 실제 CSV를 많이 섞은 구성(이름·크기·인코딩이 제각각)이라 "일반 CSV에서 허용 목록 도구가 얼마나 막히는지"를 재는 아이디어로 참고할 만함. 근거: `examples/DA-Agent/data/da-dev-tables/` 목록(68개, 최대 약 8.9MB). 다만 아래 라이선스 제약 때문에 데이터를 직접 쓰는 것은 별개 문제.
6. 질문 유형 분포 참고: Summary Statistics, Correlation, Distribution, Outlier 질문이 많음. 우리 도구 9개가 이 유형을 얼마나 덮는지 비교하는 기준으로 쓸 수 있음(우리 도구 목록은 이 보고서 범위 밖이라 대응은 확인 못 함).

### 가져오면 안 되는 것
1. DABench 데이터(질문·정답 JSONL, CSV) 재배포 또는 우리 저장소에 복사: 배지상 CC BY-NC 4.0(비상업)이고 데이터 폴더에 별도 허가 문서가 없음. 상용 제품의 eval·학습·시연에 쓰는 것도 피함. 근거: `README.md` 10행. 내부 연구용 참고조차 법무 확인 전에는 보수적으로 취급.
2. CSV 원본 재사용: GitHub에서 수집한 파일이라(`README_eval.md` 17행) 개별 원 라이선스가 불명. 확인 못 함.
3. 원칙 충돌: LLM이 파이썬 코드를 쓰고 그 실행 결과를 답으로 삼는 구조(`zero_shot_react_prompt.py`, `python_code_sandbox.py`)는 "LLM은 숫자를 만들지 않는다 / 승인 후 허용 목록 도구가 계산한다"는 우리 원칙과 맞지 않음. 임의 코드 실행도 우리 허용 목록 방식과 충돌.
4. 채점 전 LLM 재포맷 단계: 채점 입력이 LLM이 다시 쓴 문장이라 결과 재현성이 떨어지고, 우리처럼 구조화된 계획을 평가하는 쪽에는 불필요(`reformat.py`).
5. 코드는 Apache 2.0이라 일부 참고·인용은 가능하나, 쓴다면 Apache 조건(라이선스 고지 등)을 지켜야 함. 이번 분석에서는 코드 복사를 권하지 않음.

## 6. 확인하지 못한 것과 한계

- 코드를 실행하지 않았으므로 동작 여부, 점수 재현, 경로 불일치(`./data/...`)의 실제 영향은 확인 못 함.
- Hugging Face 데이터셋(`infiagent/DABench`)의 별도 라이선스 표기, 논문(arXiv 2401.05507)의 데이터 라이선스 언급은 저장소 밖이라 확인 못 함. 배지가 가리키는 정식 라이선스 문서 URL도 저장소엔 없음.
- 개별 CSV의 원 라이선스·개인정보 포함 여부 확인 못 함. CSV는 목록·크기만 보고 내용은 거의 읽지 않음.
- 질문 257개와 문서 속 400개의 차이 이유, `figures/` 외 그림이 빠진 이유 확인 못 함.
- `async_react_agent.py`, `python_code_sandbox.py`, `code_tool_docker.py` 및 `pipeline/src`의 대부분은 키워드 검색 수준으로만 봄. 전체 동작 서술은 불완전할 수 있음.
- 개념 수 분포(1개/2개/3개) 전체와 `concepts` 순서가 다른 동일 조합의 합산 집계는 하지 않음(위 집계는 목록 문자열 그대로 센 값).
- 우리 프로젝트(Task Insight) 코드는 읽지 않았으므로 도구 9개와 DABench 유형의 정확한 대응은 확인 못 함.
- 라이선스 해석은 법률 자문이 아님.

## 7. 검증 방법

PowerShell에서 아래 변수를 먼저 설정한다.

```powershell
$R = "$env:TEMP\ti-bench\InfiAgent__InfiAgent"
```

1. 커밋 SHA 확인
   `git -C $R log -1 --format='%H %ad'`
   기대: `3d6c4a70198e0a41fadf539f5b43c88b8c1a2d9c Thu May 29 10:30:43 2025 +0800`
2. 코드 라이선스(Apache 2.0)
   `Get-Content "$R\LICENSE" -TotalCount 2`
   기대: 첫 두 줄에 `Apache License`, `Version 2.0, January 2004`
3. 데이터 라이선스 배지 문구
   `Select-String -Path "$R\README.md","$R\examples\DA-Agent\README.md" -Pattern 'Data%20License' | Select-Object Path,LineNumber`
   기대: 두 파일 모두 10~11행 근처와 19행(주석)에서 `Data%20License-CC%20By%20NC%204.0` 포함 줄이 나옴.
4. 데이터 폴더에 별도 라이선스 파일이 없음
   `Get-ChildItem $R -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\\.git\\' -and $_.Name -match 'licen[sc]e' } | Select-Object -ExpandProperty FullName`
   기대: 루트 `LICENSE` 하나만 출력.
5. 질문·정답 줄 수
   `(Get-Content "$R\examples\DA-Agent\data\da-dev-questions.jsonl").Count; (Get-Content "$R\examples\DA-Agent\data\da-dev-labels.jsonl").Count`
   기대: `257`, `257`
6. 난이도 분포
   `'easy','medium','hard' | ForEach-Object { "$_ " + (Select-String -Path "$R\examples\DA-Agent\data\da-dev-questions.jsonl" -Pattern ('"level": "' + $_ + '"') -SimpleMatch:$false).Count }`
   기대: `easy 82`, `medium 87`, `hard 88`
7. CSV 개수와 비 CSV 파일 유무
   `(Get-ChildItem "$R\examples\DA-Agent\data\da-dev-tables" -File).Count; (Get-ChildItem "$R\examples\DA-Agent\data\da-dev-tables" -File | Where-Object Extension -ne '.csv').Count`
   기대: `68`, `0`
8. 질문 레코드 필드 확인(첫 줄 앞부분)
   `(Get-Content "$R\examples\DA-Agent\data\da-dev-questions.jsonl" -TotalCount 1).Substring(0,200)`
   기대: `"id": 0, "question": "Calculate the mean fare ...` 로 시작, 뒤에 `concepts`, `constraints`, `format`, `file_name`, `level` 필드가 이어짐(전체 줄에서 확인).
9. 채점 방식(정규식, 허용 오차)
   `Select-String -Path "$R\examples\DA-Agent\eval_closed_form.py" -Pattern 'pattern =','< 1e-6'`
   기대: 11행 `@(\w+)\[(.*?)\]` 정규식, 23행 `< 1e-6` 비교.
10. 코드 인터프리터 방식(Jupyter 커널 + 서브프로세스)과 eval 프롬프트 구성
    `Select-String -Path "$R\pipeline\src\infiagent\tools\code_sandbox\python_code_sandbox.py" -Pattern 'jupyter_client','subprocess' | Select-Object LineNumber,Line; Select-String -Path "$R\pipeline\activities\eval.py" -Pattern 'prompt = f'`
    기대: `import subprocess`, `from jupyter_client import ...` 줄이 나오고, eval.py에는 `question`과 `constraints`만 넣은 `prompt = f"Question: ..."` 줄이 나옴(`format`은 없음).
