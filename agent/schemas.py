"""agent 입출력 형식.

contracts.py의 AnalysisPlan · PlanStep · ToolResult를 그대로 감싸서 쓴다 (새 계산 형식은 만들지 않음).
화면(ui)은 PlanResponse → (사용자 승인) → ExecutionResponse 순서로 받는다.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from contracts import AnalysisPlan, ToolResult

PlanStatus = Literal["plan", "clarify", "unsupported", "error"]
ExecStatus = Literal["ok", "partial", "error"]


class PlanResponse(BaseModel):
    """create_plan 결과. 이 단계에서는 어떤 도구도 실행하지 않는다."""
    status: PlanStatus  # plan: 승인 대기 / clarify: 되묻기 / unsupported: 데이터로 답할 수 없음 / error: 오류
    question: str
    as_of: str
    message: str = ""  # 사용자에게 보여줄 안내 (되묻기 질문, 지원 불가 사유, 오류 사유)
    plan: Optional[AnalysisPlan] = None  # status == "plan"일 때만
    notices: list[str] = Field(default_factory=list)  # 한계 안내 (예측·인사평가 불가 등). plan.summary에도 들어 있음
    rejected: list[str] = Field(default_factory=list)  # 검증에서 제외한 도구 호출과 사유 (실행 기록용)
    model: str = ""  # 사용한 LLM 모델명


class PlanApproval(BaseModel):
    """사용자가 승인한 계획의 지문. 계획이 한 글자라도 바뀌면 실행하지 않는다."""
    plan_hash: str


class StepResult(BaseModel):
    step_id: str
    tool: str
    purpose: str
    chart_type: str
    result: ToolResult  # 엔진이 돌려준 그대로 (표와 차트 모두 result.rows로 그린다)
    explanation: str  # result의 값만으로 Python이 만든 설명문


class LogEntry(BaseModel):
    step_id: str
    tool: str
    params: dict
    ok: bool
    error: Optional[str] = None
    used_rows: int = 0
    elapsed_ms: int = 0


class ExecutionResponse(BaseModel):
    """execute_plan 결과."""
    status: ExecStatus  # ok: 전 단계 성공 / partial: 일부 실패 / error: 실행 안 함 또는 전부 실패
    question: str = ""
    message: str = ""
    steps: list[StepResult] = Field(default_factory=list)
    explanation: str = ""  # 단계별 설명 + 한계 안내를 합친 최종 설명
    log: list[LogEntry] = Field(default_factory=list)
