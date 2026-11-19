# 정답표

엔진 결과와 비교하는 기준 값입니다. 정답 원본은 `tests/fixtures/kafe/evaluation_answers.json`이고, 테스트는 이 파일을 직접 읽습니다.

> ⚠️ `tests/fixtures/kafe/`의 정답 파일과 `SCENARIOS.md`는 **AI 에이전트에게 주면 안 됩니다.** 에이전트에는 `data/kafe/`의 CSV와 `docs/kafe_questions.json`(질문만)만 제공합니다.

## 검증 상태

| 확인 방법 | 상태 |
|---|---|
| 데이터 제작 스크립트가 계산한 고정 정답 | 제공됨 |
| 별도의 pandas 코드로 다시 계산 (엔진과 별개) | ✅ 7개 모두 일치 |
| 엔진 테스트 (`tests/test_kafe_answers.py`) | ✅ 통과 |
| 사람이 엑셀로 확인 | ☐ 아래 표의 `엑셀 확인` 칸 |

## A. kafe 평가 질문 (기준일 2026-11-19)

| # | 질문 | 정답 | 도구 | 엑셀 확인 |
|---|---|---|---|---|
| Q1 | 현재 기한을 초과한 미완료 업무는 몇 건? | **15건** (T054, T075, T078, T080, T087, T093, T103, T117~T121, T132, T133, T136) | count_overdue_tasks | ☐ |
| Q2 | 부서별 미완료 업무 수는? | 품질 18, 제조 9, 연구 6, 경영지원 4, 영업 3, 미배정 4 (1인당: 품질 2.57로 최다) | summarize_by_category | ☐ |
| Q3 | 계획 대비 실제 MH가 가장 많이 초과된 프로젝트는? | **P05** (초과MH 370) | mh_overrun | ☐ |
| Q4 | 예산 대비 비용 초과율이 가장 높은 프로젝트는? | **P01** (+30.72%), 초과 프로젝트는 P01·P02 | cost_vs_budget | ☐ |
| Q5 | 완료율이 가장 낮은 프로젝트는? | **P10** (20%, 15건 중 3건 완료) | calculate_completion_rate | ☐ |
| Q6 | 확인이 필요한 누락 데이터는 몇 건? | **7건** (담당자 빈칸 T049·T063·T096·T103, 기한 빈칸 T061·T129·T142) | check_missing_data | ☐ |
| Q7 | 담당 업무가 가장 많이 배정된 직원은? | **E032** (9건) | aggregate | ☐ |

## B. sample.csv (과제 제공, 일반 CSV 모드)

| # | 질문 | 정답 | 도구 |
|---|---|---|---|
| S1 | 전체 매출은? | 500 | aggregate |
| S2 | 지역별 매출은? | 서울 250, 부산 250 | aggregate |
| S3 | 월별 매출은? | 2026-01 300, 2026-02 200 | aggregate |
| S4 | 전월 대비 매출 변화와 감소 지역은? | 전체 −33.33%, 부산 −75%(감소), 서울 +50% | compare_periods |
| S5 | 2026-01의 전월 대비는? | 계산 불가: 2025-12 데이터 없음 | compare_periods |

## 엑셀로 확인하는 방법 (tasks.csv)

열: A task_id, B project_id, C assignee_id, … F start_date, G due_date, H completed_at, I status, … K planned_mh, L actual_mh

- **Q1:** `=COUNTIFS(I:I,"<>완료",G:G,"<"&DATE(2026,11,19))` (기한 빈칸은 날짜 비교에서 자동으로 빠짐)
- **Q6:** `=COUNTBLANK(C2:C151)` → 4, `=COUNTBLANK(G2:G151)` → 3
- **Q7:** C열로 피벗 테이블 → 개수 → 내림차순 정렬
- **Q5:** 피벗 테이블 (행: project_id, 값: task_id 개수) + status="완료" 필터를 건 같은 피벗과 비교
- **Q3:** M2에 `=MAX(L2-K2,0)` → project_id별 M열 합계 피벗
- **Q2·Q4:** employees.csv를 결합해야 하므로 엑셀에서는 `XLOOKUP`으로 담당자의 department·hourly_labor_cost를 가져온 뒤 피벗
