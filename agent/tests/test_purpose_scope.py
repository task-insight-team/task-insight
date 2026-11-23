"""회귀 테스트: 승인 전 계획 문구(purpose·summary)에 부서 범위가 보이는지 (FakeLLM, 실제 API 미사용).

배경 (2026-11-23 배포 앱 점검): '품질팀 기한 초과' 계획이 "기한이 지난 미완료 업무 확인"으로만 표시돼
승인하는 사람이 부서 범위를 볼 수 없었다 (계산은 department=품질로 정확했음).
"""

from __future__ import annotations

import pytest

from agent import FakeLLM, create_plan
from agent.tests.helpers import AS_OF, kafe_dataset


@pytest.fixture(scope="module")
def ds():
    return kafe_dataset()


def purpose_of(ds, question, tool, args):
    resp = create_plan(question, ds, AS_OF, FakeLLM(FakeLLM.calls((tool, args))))
    assert resp.status == "plan", resp.message
    return resp.plan.steps[0].purpose, resp.plan.summary


@pytest.mark.parametrize("question,tool,args,expected", [
    # 부서 조건이 있는 계획
    ("품질팀에서 기한이 지났는데 완료되지 않은 업무는 몇 건이야?", "count_overdue_tasks", {"department": "품질"},
     "기한이 지난 미완료 업무 확인 (부서 품질)"),
    ("연구팀 직원별 완료율은?", "calculate_completion_rate", {"group_by": "담당자ID", "department": "연구"},
     "완료율 확인 (담당자ID별) (부서 연구)"),
    ("영업팀의 P05 업무 현황은?", "summarize_by_category", {"group_by": "상태", "department": "영업", "project_id": "P05"},
     "범주별 업무 현황(전체·완료·미완료) 확인 (상태별) (부서 영업) (프로젝트 P05)"),
    # 부서 조건이 없는 계획: 문구 그대로
    ("기한이 지난 미완료 업무는 몇 건인가?", "count_overdue_tasks", {},
     "기한이 지난 미완료 업무 확인"),
    ("부서별 미완료 업무 현황은?", "summarize_by_category", {"group_by": "부서"},
     "범주별 업무 현황(전체·완료·미완료) 확인 (부서별)"),
    ("P03 프로젝트 비용은?", "cost_vs_budget", {"project_id": "P03"},
     "예산 대비 실적 비용 확인 (프로젝트 P03)"),
    ("제조 부서 미완료 업무 몇 건?", "aggregate",
     {"op": "count", "filters": [{"column": "부서", "op": "==", "value": "제조"}, {"column": "상태", "op": "!=", "value": "완료"}]},
     "개수 집계 [조건: 부서 == 제조] [조건: 상태 != 완료]"),
])
def test_purpose_text(ds, question, tool, args, expected):
    purpose, summary = purpose_of(ds, question, tool, args)
    assert purpose == expected
    assert expected in summary  # 승인 화면의 계획 요약에도 같은 문구


def test_no_department_text_without_department(ds):
    purpose, summary = purpose_of(ds, "기한이 지난 미완료 업무는 몇 건인가?", "count_overdue_tasks", {})
    assert "(부서 " not in purpose and "None" not in purpose and "(부서 " not in summary


def test_normalized_department_name_is_shown(ds):
    """LLM이 '품질팀'으로 넣어도 계획에는 데이터 값 '품질'로 표시된다."""
    purpose, _ = purpose_of(ds, "품질팀의 기한 초과 미완료 업무는?", "count_overdue_tasks", {"department": "품질팀"})
    assert purpose == "기한이 지난 미완료 업무 확인 (부서 품질)"
