"""agent 테스트 공용 도우미. (tests/conftest.py와 이름이 겹치지 않도록 conftest를 쓰지 않는다)

평가 정답 파일(tests/fixtures/kafe)은 읽지 않는다. 기대값은 엔진(run_tool)을 직접 호출해 얻는다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from engine import load_dataset

ROOT = Path(__file__).resolve().parents[2]
KAFE = ROOT / "data" / "kafe"
AS_OF = "2026-11-19"

# 대표 질문 7개와 기대 도구 (FakeLLM은 이 도구 호출을 돌려준다)
REPRESENTATIVE = [
    ("기한이 지난 미완료 업무는 몇 건인가?", "count_overdue_tasks", {"as_of": AS_OF}),
    ("부서별 미완료 업무 현황은?", "summarize_by_category", {"group_by": "부서"}),
    ("계획 대비 MH 초과가 가장 큰 프로젝트는?", "mh_overrun", {"group_by": "프로젝트ID"}),
    ("비용 초과율이 가장 큰 프로젝트는?", "cost_vs_budget", {}),
    ("완료율이 가장 낮은 프로젝트는?", "calculate_completion_rate", {"group_by": "프로젝트ID"}),
    ("담당자나 기한이 누락된 업무는?", "check_missing_data", {}),
    ("담당 업무가 가장 많은 직원은?", "aggregate",
     {"table": "main", "op": "count", "group_by": ["담당자ID"], "sort": "value_desc"}),
]

UNSUPPORTED = [
    "어떤 프로젝트에 인원을 더 배치해야 하는가?",
    "앞으로 예산이 얼마나 필요할 것인가?",
    "어떤 직원의 인사평가가 가장 좋은가?",
]


@lru_cache(maxsize=1)
def kafe_dataset():
    res = load_dataset({f"{n}.csv": KAFE / f"{n}.csv" for n in ("tasks", "employees", "projects")})
    assert res.ok and res.join.ok
    return res.dataset
