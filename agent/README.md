# agent/ — AI 에이전트

질문 → **분석 계획(도구 실행 없음)** → 사용자 승인 → `engine.run_tool` 실행 → `ToolResult` + 설명문.

## 화면에서 쓰는 법

```python
from agent import create_plan, approve_plan, execute_plan

resp = create_plan(question, ds, as_of="2026-11-19")   # ds = load_dataset(...).dataset
if resp.status == "plan":
    show(resp.plan.summary, resp.plan.steps)            # steps[i].purpose / tool / params / chart_type
    if st.button("승인"):
        out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
        st.write(out.explanation)
        for s in out.steps:                              # s.result = ToolResult (rows로 표·차트 둘 다)
            ...
else:
    st.info(resp.message)                                # clarify / unsupported / error 안내
```

| 반환 | 필드 |
|---|---|
| `PlanResponse` | `status`(plan·clarify·unsupported·error), `message`, `plan`(AnalysisPlan), `notices`(한계 안내), `rejected`(제외된 호출 사유), `as_of`, `model` |
| `ExecutionResponse` | `status`(ok·partial·error), `message`, `steps`(StepResult: step_id·tool·purpose·chart_type·**result: ToolResult**·explanation), `explanation`, `log` |

- 승인 후 계획이 바뀌면(인자 하나라도) `execute_plan`이 실행하지 않고 재승인을 요구한다 (`PlanApproval.plan_hash`).
- 설명문의 숫자는 모두 `ToolResult`에서 Python이 가져온다. LLM은 도구와 인자만 고른다.
- 한 계획은 서로 독립인 단계 최대 4개. 앞 결과에 따라 인자가 바뀌는 질문은 아직 지원하지 않는다.

## 설정

| 환경변수 | 설명 |
|---|---|
| `GEMINI_API_KEY` | 필수. 환경변수 또는 Streamlit `st.secrets` |
| `GEMINI_MODEL` | 선택. 기본 `gemini-3.5-flash-lite` (2026-11-22 확인: 2.5-flash는 신규 키 404, 3.8·3.5-flash는 503·504 잦음) |

## 터미널 확인

```bash
python -m agent "기한이 지난 미완료 업무는 몇 건인가?"          # 계획 출력 후 y/N 승인
python -m agent "비용 초과율이 가장 큰 프로젝트는?" --yes
```

## 테스트

```bash
pytest agent/tests          # 오프라인 (FakeLLM). 키가 있으면 test_live_gemini도 실행
pytest tests agent/tests    # 전체
```
`pytest.ini`의 `testpaths`가 `tests`뿐이라 `pytest`만 치면 agent 테스트는 돌지 않는다 (통합 담당과 협의 필요).
