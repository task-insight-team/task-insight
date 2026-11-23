"""모듈 간 데이터 규약 (팀 합의 없이 수정 금지).

engine(데이터 엔진) · agent(AI 에이전트) · ui(화면)가 이 파일의 형식으로만 데이터를 주고받는다.
- 데이터: 업무(tasks)·직원(employees)·프로젝트(projects) CSV 3개를 올리면 엔진이 검증하고 결합한다.
  표준 형식이 아닌 CSV 1개(예: 과제 sample.csv)는 일반(generic) 모드로 분석한다.
- agent는 PlanStep.params를 그대로 engine.run_tool에 넘긴다. 엔진이 *Params 모델로 검증한다.
- engine 도구는 항상 ToolResult를 돌려준다 (예외 대신 error에 사유).
- ui는 ToolResult.rows 하나로 표와 차트를 함께 그린다 (차트 값 = 표 값).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 1. 표준 테이블 (docs/kafe_data_dictionary.md)
# ---------------------------------------------------------------------------
TableKind = Literal["tasks", "employees", "projects", "generic"]

TABLE_COLUMNS: dict[str, list[str]] = {
    "tasks": ["task_id", "project_id", "assignee_id", "task_name", "category", "start_date", "due_date",
              "completed_at", "status", "progress_percent", "planned_mh", "actual_mh",
              "planned_nonlabor_cost", "actual_nonlabor_cost", "priority", "issue_type"],
    "employees": ["employee_id", "department", "role", "weekly_capacity_mh", "hourly_labor_cost"],
    "projects": ["project_id", "project_name", "lead_department", "project_start_date", "planned_end_date",
                 "budget", "status", "project_type"],
}
# 이 컬럼들이 모두 있으면 해당 표로 인식
TABLE_SIGNATURE: dict[str, set[str]] = {
    "tasks": {"task_id", "project_id", "due_date", "status"},
    "employees": {"employee_id", "department"},
    "projects": {"project_id", "budget"},
}
ID_COLUMN = {"tasks": "task_id", "employees": "employee_id", "projects": "project_id"}
DATE_COLUMNS: dict[str, list[str]] = {
    "tasks": ["start_date", "due_date", "completed_at"],
    "employees": [],
    "projects": ["project_start_date", "planned_end_date"],
}
NUMBER_COLUMNS: dict[str, list[str]] = {
    "tasks": ["progress_percent", "planned_mh", "actual_mh", "planned_nonlabor_cost", "actual_nonlabor_cost"],
    "employees": ["weekly_capacity_mh", "hourly_labor_cost"],
    "projects": ["budget"],
}
# 빈칸이면 '확인 필요'로 알리는 컬럼 (값은 지우지 않고 남겨 둔다)
CHECK_IF_MISSING: dict[str, list[str]] = {
    "tasks": ["assignee_id", "due_date"],
    "employees": [],
    "projects": [],
}
# 빈칸이면 안 되는 컬럼 (형식 오류로 계산에서 제외)
REQUIRED_VALUES: dict[str, list[str]] = {
    "tasks": ["task_id", "project_id", "category", "start_date", "status", "planned_mh", "actual_mh"],
    "employees": ["employee_id", "department", "hourly_labor_cost"],
    "projects": ["project_id", "budget", "status"],
}
ALLOWED_VALUES: dict[str, dict[str, list[str]]] = {
    "tasks": {"status": ["미착수", "진행중", "완료", "보류"], "priority": ["높음", "중간", "낮음"]},
    "employees": {},
    "projects": {"status": ["진행중", "완료", "보류"]},
}
STATUS_DONE = "완료"  # 미완료 = 완료가 아닌 모든 상태 (미착수·진행중·보류)
NONCONFORMANCE_CATEGORY = "부적합 대응"  # 이 분류일 때만 issue_type이 있어야 함
UNASSIGNED = "미배정"  # 담당자가 없을 때 부서 값

DATE_FORMAT = "%Y-%m-%d"
FREE_TEXT_COLUMNS: list[str] = ["task_name", "project_name"]  # 결과 rows·LLM 프롬프트에 넣지 않음

# 화면에 보여줄 이름 (도구 입력도 이 한국어 이름을 받는다)
DISPLAY_NAMES: dict[str, str] = {
    "task_id": "업무ID", "project_id": "프로젝트ID", "assignee_id": "담당자ID", "task_name": "업무명",
    "category": "분류", "start_date": "시작일", "due_date": "기한", "completed_at": "완료일", "status": "상태",
    "progress_percent": "진행률(%)", "planned_mh": "계획MH", "actual_mh": "실적MH",
    "planned_nonlabor_cost": "계획 비인건비", "actual_nonlabor_cost": "실적 비인건비", "priority": "우선순위",
    "issue_type": "문제유형", "employee_id": "직원ID", "department": "부서", "role": "직급",
    "weekly_capacity_mh": "주당 가용MH", "hourly_labor_cost": "시간당 인건비", "project_name": "프로젝트명",
    "lead_department": "주관부서", "project_start_date": "프로젝트 시작일", "planned_end_date": "계획 종료일",
    "budget": "예산", "project_status": "프로젝트 상태", "project_type": "프로젝트 유형",
    # 결합 후 파생 컬럼
    "mh_over": "초과MH", "labor_cost": "실적 인건비", "total_cost": "실적 총비용",
    "start_month": "시작월", "completed_month": "완료월",
}

# ---------------------------------------------------------------------------
# 2. 공통 규칙
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 파일당 10MB
MAX_ROWS = 100_000
ROUND_DIGITS = 2  # 소수 둘째 자리, 사사오입(ROUND_HALF_UP)
# 기한 초과 = 상태≠완료 그리고 기한 < 기준일 (기한 당일은 초과 아님, 기한 빈칸은 판정 불가로 제외)
# 완료율 = 상태가 완료인 건수 / 전체 건수 x 100 (%)
# 초과MH = max(실적MH - 계획MH, 0) (업무 단위로 구한 뒤 합산)
# 실적 인건비 = 실적MH x 담당자 시간당 인건비 / 실적 총비용 = 실적 인건비 + 실적 비인건비
# 비용 초과율 = 프로젝트 실적 총비용 / 예산 x 100 - 100 (%). 예산은 프로젝트당 한 번만 쓴다


# ---------------------------------------------------------------------------
# 3. 업로드 검증·결합 결과
# ---------------------------------------------------------------------------
class Issue(BaseModel):
    level: Literal["error", "warning", "info"]  # error: 분석 불가 / warning: 확인 필요 / info: 참고
    code: str  # 예: "empty_file", "missing_columns", "invalid_date"
    message: str  # 사용자에게 보여줄 한국어 문장
    count: int = 0  # 영향 행 수
    rows: list[int] = Field(default_factory=list)  # 영향 행 번호(헤더 제외 1부터), 최대 50개


class ColumnInfo(BaseModel):
    name: str
    dtype: Literal["number", "date", "text"]
    missing: int


class ValidationReport(BaseModel):
    filename: str = ""
    ok: bool  # error가 없으면 True
    kind: TableKind
    n_rows: int
    n_cols: int
    encoding: Optional[str] = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class JoinReport(BaseModel):
    ok: bool
    tasks_rows: int  # 결합 전 업무 행 수
    joined_rows: int  # 결합 후 행 수 (같아야 정상: 다대일 결합)
    has_employees: bool
    has_projects: bool
    issues: list[Issue] = Field(default_factory=list)


class CleaningResult(BaseModel):
    before_rows: int
    after_rows: int
    removed_duplicates: int
    removed_invalid: int
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. 도구 입력 (LLM이 채우는 값 -> 엔진이 이 모델로 검증)
#    컬럼 이름은 영어(내부 이름)와 한국어(DISPLAY_NAMES) 모두 받는다.
# ---------------------------------------------------------------------------
class Filter(BaseModel):
    column: str
    op: Literal["==", "!=", ">", ">=", "<", "<=", "in", "is_null", "not_null"]
    value: Any = None


class AggregateParams(BaseModel):
    """범용 집계: 합계·평균·개수. table로 직원·프로젝트 표도 집계할 수 있다."""
    table: Literal["main", "employees", "projects"] = "main"  # main = 결합된 업무 표 또는 일반 CSV
    op: Literal["sum", "mean", "count"]
    value_column: Optional[str] = None  # count면 생략
    group_by: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    sort: Literal["group", "value_desc", "value_asc"] = "group"
    top_n: Optional[int] = Field(default=None, ge=1)


class CountOverdueParams(BaseModel):
    as_of: str  # 기준일 YYYY-MM-DD (재현성을 위해 필수)
    group_by: Optional[str] = None
    department: Optional[str] = None  # 이 부서 업무만 (예: "품질"). 생략하면 전체
    project_id: Optional[str] = None  # 이 프로젝트 업무만 (예: "P05"). 생략하면 전체


class SummarizeByCategoryParams(BaseModel):
    group_by: str  # 예: "부서", "프로젝트ID", "분류", "상태"
    as_of: Optional[str] = None  # 주면 기한 초과 건수도 계산
    department: Optional[str] = None  # 이 부서 업무만 (예: "품질"). 생략하면 전체
    project_id: Optional[str] = None  # 이 프로젝트 업무만 (예: "P05"). 생략하면 전체


class CompletionRateParams(BaseModel):
    group_by: Optional[str] = None  # 예: "프로젝트ID", "담당자ID"
    department: Optional[str] = None  # 이 부서 업무만 (예: "연구"). 생략하면 전체
    project_id: Optional[str] = None  # 이 프로젝트 업무만 (예: "P05"). 생략하면 전체


class ComparePeriodsParams(BaseModel):
    date_column: str  # 날짜(YYYY-MM-DD) 또는 월(YYYY-MM) 컬럼. 예: "완료일", "month"
    op: Literal["sum", "mean", "count"] = "count"
    value_column: Optional[str] = None
    current: str  # YYYY-MM
    previous: Optional[str] = None  # 생략하면 전월
    group_by: Optional[str] = None


class FindRecurringParams(BaseModel):
    key: str = "문제유형"
    min_count: int = Field(default=2, ge=2)


class MhOverrunParams(BaseModel):
    group_by: str = "프로젝트ID"


class CostVsBudgetParams(BaseModel):
    project_id: Optional[str] = None  # 생략하면 전체 프로젝트


class CheckMissingParams(BaseModel):
    include_structural: bool = False  # True면 '구조적 공백'(값이 없어야 정상인 칸)도 목록에 포함


# ---------------------------------------------------------------------------
# 5. 도구 출력 (모든 도구 공통)
# ---------------------------------------------------------------------------
class ToolResult(BaseModel):
    tool: str
    params: dict  # 실제로 사용한 입력
    rows: list[dict] = Field(default_factory=list)  # 표 데이터 = 차트 데이터. 첫 키 = 범주(x축)
    value: Optional[float] = None  # 대표 숫자 (예: 기한 초과 15건). 순위형 도구는 None, rows[0]이 1위
    unit: str = ""  # "건", "%", "MH", "원" 등
    basis: str = ""  # 계산 기준 (예: "기준일 2026-11-19, 상태≠완료, 기한<기준일")
    used_rows: int = 0  # 계산에 사용한 행 수
    error: Optional[str] = None  # 계산 불가 사유. 있으면 rows/value를 신뢰하지 않는다

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# 6. 분석 계획 (agent -> 사용자 승인 -> 실행)
# ---------------------------------------------------------------------------
class PlanStep(BaseModel):
    id: str  # "s1", "s2", ...
    tool: str  # engine.TOOLS의 이름
    params: dict
    purpose: str = ""  # 이 단계가 무엇을 확인하는지 (숫자 포함 금지)
    chart_type: Literal["bar", "line", "pie", "table"] = "table"


class AnalysisPlan(BaseModel):
    question: str
    steps: list[PlanStep]
    summary: str = ""  # 분석 전 설명. 수치·결론은 실행 후에만 생성
