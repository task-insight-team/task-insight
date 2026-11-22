"""AI 에이전트: 질문 → 분석 계획 → (사용자 승인) → 엔진 실행 → 설명.

화면(ui)에서 쓰는 방법
    from agent import create_plan, approve_plan, execute_plan

    resp = create_plan(question, ds, as_of="2026-11-19")      # PlanResponse (도구 실행 없음)
    if resp.status == "plan":
        # resp.plan.steps(purpose·tool·params)와 resp.plan.summary를 보여 주고 승인 버튼
        approval = approve_plan(resp.plan)                     # 버튼을 눌렀을 때만
        out = execute_plan(ds, resp.plan, approval)            # ExecutionResponse
        out.explanation; out.steps[i].result (ToolResult: rows로 표·차트)
    else:
        resp.message  # clarify(되묻기) / unsupported(지원 불가) / error(오류) 안내
"""

from agent.executor import approve_plan, execute_plan
from agent.llm import FakeLLM, GeminiClient, LLMError, LLMReply
from agent.planner import create_plan
from agent.schemas import ExecutionResponse, LogEntry, PlanApproval, PlanResponse, StepResult

__all__ = ["create_plan", "approve_plan", "execute_plan", "PlanResponse", "PlanApproval", "ExecutionResponse",
           "StepResult", "LogEntry", "GeminiClient", "FakeLLM", "LLMReply", "LLMError"]
