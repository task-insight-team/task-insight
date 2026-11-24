"""ToolResult → 사용자용 설명문 (Python 템플릿, LLM 미사용).

설명에 들어가는 숫자는 모두 result.value 또는 result.rows에서 그대로 가져온다 (차트·표와 같은 값).
원인·예측·평가는 쓰지 않는다. 1위가 동점이면 동점 항목을 모두 밝힌다.
"""

from __future__ import annotations

from typing import Any, Optional

from contracts import PlanStep, ToolResult

TOP_LIST = 5


def num(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        if float(v).is_integer():
            return f"{int(v):,}"
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)


def pct(v: Any, signed: bool = False) -> str:
    if v is None:
        return "-"
    s = num(v)
    return f"+{s}%" if signed and float(v) > 0 else f"{s}%"


def _first_key(rows: list[dict]) -> str:
    return next(iter(rows[0])) if rows else ""


def _leaders(rows: list[dict], key: str) -> tuple[list[str], Any]:
    """rows가 key 기준으로 이미 정렬돼 있다고 보고, 1위와 같은 값의 범주를 모두 돌려준다."""
    cat = _first_key(rows)
    top = rows[0].get(key)
    names = [str(r[cat]) for r in rows if r.get(key) == top]
    return names, top


def _names(names: list[str]) -> str:
    return ", ".join(names) + (" (공동)" if len(names) > 1 else "")


def _leader_rows(rows: list[dict], key: str) -> list[dict]:
    """1위와 같은 값을 가진 행들 (동점). 각 행의 원래 수치를 따로 설명하기 위해 쓴다."""
    top = rows[0].get(key)
    return [r for r in rows if r.get(key) == top]


def _listing(rows: list[dict], key: str, unit: str = "") -> str:
    cat = _first_key(rows)
    items = [f"{r[cat]} {num(r.get(key))}{unit}" for r in rows[:TOP_LIST]]
    more = " 등 (전체는 표 참고)" if len(rows) > TOP_LIST else ""
    return ", ".join(items) + more


def explain_result(step: PlanStep, res: ToolResult) -> str:
    if not res.ok:
        return f"이 분석은 계산할 수 없습니다: {res.error}"
    try:
        body = _BUILDERS.get(res.tool, _generic)(res)
    except Exception:  # noqa: BLE001 - 설명 실패가 결과 표시를 막지 않게
        body = _generic(res)
    scope = _scope_label(res.params)
    if scope:  # 부서·프로젝트로 좁힌 결과임을 첫머리에 밝힌다 (전체 결과로 오해하지 않게)
        body = f"[{scope}] {body}"
    return f"{body}\n근거: {res.basis}" if res.basis else body


def _scope_label(params: dict) -> str:
    parts = []
    if params.get("department"):
        parts.append(f"부서 {params['department']}")
    if params.get("project_id"):
        parts.append(f"프로젝트 {params['project_id']}")
    return " · ".join(parts)


def _generic(res: ToolResult) -> str:
    if not res.rows:
        return "결과 행이 없습니다."
    return f"결과 {len(res.rows)}행을 표와 차트로 확인하세요."


def _overdue(res: ToolResult) -> str:
    as_of = res.params.get("as_of", "")
    head = f"기준일 {as_of} 기준, 기한이 지난 미완료 업무는 {num(res.value)}건입니다."
    if res.params.get("group_by") and res.rows:
        key = "기한 초과(건)"
        names, top = _leaders(res.rows, key)
        if top:
            head += f" {_first_key(res.rows)}별로는 {_names(names)}이(가) {num(top)}건으로 가장 많습니다. ({_listing(res.rows, key, '건')})"
    elif res.rows:
        longest = max(res.rows, key=lambda r: r.get("초과 일수") or 0)
        head += f" 기한을 가장 오래 넘긴 업무는 {longest.get('업무ID')}({num(longest.get('초과 일수'))}일 초과)입니다."
    return head


def _summary(res: ToolResult) -> str:
    if not res.rows:
        return "조건에 맞는 업무가 0건입니다." if _scope_label(res.params) else "집계할 데이터가 없습니다."
    key = "미완료(건)"
    cat = _first_key(res.rows)
    names, top = _leaders(res.rows, key)
    out = f"{cat}별 미완료 업무는 {_names(names)}이(가) {num(top)}건으로 가장 많습니다. ({_listing(res.rows, key, '건')})"
    per_head = [r for r in res.rows if r.get("1인당 미완료") is not None]
    if per_head:
        best = max(per_head, key=lambda r: r["1인당 미완료"])
        out += f" 인원 대비로는 {best[cat]}이(가) 1인당 {num(best['1인당 미완료'])}건으로 가장 높습니다."
    return out


def _completion(res: ToolResult) -> str:
    if not res.params.get("group_by"):
        r = res.rows[0] if res.rows else {}
        return f"전체 완료율은 {pct(res.value)}입니다 (완료 {num(r.get('완료(건)'))}건 / 전체 {num(r.get('전체(건)'))}건)."
    if not res.rows:
        return "집계할 데이터가 없습니다."
    cat = _first_key(res.rows)
    names, low = _leaders(res.rows, "완료율(%)")
    first = res.rows[0]
    if len(res.rows) == 1:  # 대상이 하나면 '가장 낮은'이라고 쓰지 않는다
        return f"{first[cat]}의 완료율은 {pct(low)}입니다 (완료 {num(first.get('완료(건)'))}건 / 전체 {num(first.get('전체(건)'))}건)."
    tied = _leader_rows(res.rows, "완료율(%)")
    if len(tied) > 1:  # 완료율은 같아도 건수는 다를 수 있다 → 각자의 건수를 따로 쓴다
        each = ", ".join(f"{r[cat]}(완료 {num(r.get('완료(건)'))}건 / 전체 {num(r.get('전체(건)'))}건)" for r in tied)
        return (f"완료율이 가장 낮은 {cat}은(는) {each}로, 모두 {pct(low)}입니다 (공동). "
                f"전체 완료율은 {pct(res.value)}입니다.")
    return (f"완료율이 가장 낮은 {cat}은(는) {_names(names)}로 {pct(low)}입니다"
            f" (완료 {num(first.get('완료(건)'))}건 / 전체 {num(first.get('전체(건)'))}건). 전체 완료율은 {pct(res.value)}입니다.")


def _mh(res: ToolResult) -> str:
    if not res.rows:
        return "집계할 데이터가 없습니다."
    cat = _first_key(res.rows)
    names, top = _leaders(res.rows, "초과MH 합계")
    if not top:
        return "계획 대비 실적 MH를 초과한 업무가 없습니다."
    first = res.rows[0]
    tied = _leader_rows(res.rows, "초과MH 합계")
    if len(tied) > 1:
        each = ", ".join(f"{r[cat]}(계획 {num(r.get('계획MH'))}MH, 실적 {num(r.get('실적MH'))}MH)" for r in tied)
        return f"계획 대비 실적 MH 초과가 가장 큰 {cat}은(는) {each}로, 모두 초과 {num(top)}MH입니다 (공동)."
    return (f"계획 대비 실적 MH 초과가 가장 큰 {cat}은(는) {_names(names)}로 초과 {num(top)}MH입니다"
            f" (계획 {num(first.get('계획MH'))}MH, 실적 {num(first.get('실적MH'))}MH, 초과 투입 업무 {num(first.get('초과 투입 업무(건)'))}건)."
            f" ({_listing(res.rows, '초과MH 합계', 'MH')})")


def _cost(res: ToolResult) -> str:
    if not res.rows:
        return "프로젝트가 없습니다."
    key = "비용 초과율(%)"
    first = res.rows[0]
    detail = f"예산 {num(first.get('예산'))}원 대비 실적 {num(first.get('실적 총비용'))}원"
    if res.params.get("project_id"):
        return f"{first['프로젝트ID']}의 비용 초과율은 {pct(first.get(key), True)}입니다 ({detail})."
    names, top = _leaders(res.rows, key)
    tied = _leader_rows(res.rows, key)
    if len(tied) > 1:  # 동점이면 1위 행 하나의 금액을 다른 프로젝트 것처럼 쓰지 않는다
        detail = "; ".join(f"{r['프로젝트ID']} 예산 {num(r.get('예산'))}원 대비 실적 {num(r.get('실적 총비용'))}원" for r in tied)
    over = [r["프로젝트ID"] for r in res.rows if (r.get(key) or 0) > 0]
    out = f"비용 초과율이 가장 높은 프로젝트는 {_names(names)}로 {pct(top, True)}입니다 ({detail})."
    out += f" 예산을 초과한 프로젝트는 {len(over)}개입니다" + (f": {', '.join(over)}." if over else ".")
    return out


def _missing(res: ToolResult) -> str:
    out = f"확인이 필요한 누락 데이터가 있는 업무는 {num(res.value)}건입니다."
    need = [r for r in res.rows if r.get("분류") == "확인 필요"]
    if need:
        by_col: dict[str, list[str]] = {}
        for r in need:
            by_col.setdefault(r["컬럼"], []).append(r["업무ID"])
        out += " " + ", ".join(f"{c} 빈칸 {len(ids)}건({', '.join(ids)})" for c, ids in by_col.items()) + "."
    return out


def _aggregate(res: ToolResult) -> str:
    if not res.rows:
        return "결과가 없습니다."
    keys = list(res.rows[0])
    label = keys[-1]
    unit = "건" if res.params.get("op") == "count" else ""
    if not res.params.get("group_by"):
        cond = " / ".join(f"{f['column']} {f['op']} {f.get('value') if f.get('value') is not None else ''}".strip()
                          for f in res.params.get("filters") or [])
        what = "업무 수" if res.params.get("op") == "count" else label
        return f"{'조건(' + cond + ')에 맞는 ' if cond else ''}{what}는 {num(res.rows[0][label])}{unit}입니다."
    cat = " · ".join(keys[:-1])
    sort = res.params.get("sort")
    if sort in ("value_desc", "value_asc"):
        cat_key = keys[0]
        top = res.rows[0][label]
        names = [" / ".join(str(r[k]) for k in keys[:-1]) for r in res.rows if r[label] == top] if len(keys) > 2 else \
            [str(r[cat_key]) for r in res.rows if r[label] == top]
        if res.params.get("op") == "count":
            what = "업무 건수가 " + ("가장 많은" if sort == "value_desc" else "가장 적은")
        else:
            what = f"{label}이(가) " + ("가장 큰" if sort == "value_desc" else "가장 작은")
        return f"{what} {cat}은(는) {_names(names)}({num(top)}{unit})입니다. ({_listing(res.rows, label, unit)})"
    return f"{cat}별 {label}: {_listing(res.rows, label, unit)}"


def _recurring(res: ToolResult) -> str:
    if not res.rows:
        return f"{res.params.get('min_count', 2)}건 이상 반복된 문제유형이 없습니다."
    names, top = _leaders(res.rows, "발생(건)")
    return (f"반복된 문제유형은 {num(res.value)}개입니다. 가장 많이 반복된 유형은 {_names(names)}({num(top)}건)입니다."
            f" ({_listing(res.rows, '발생(건)', '건')})")


def _periods(res: ToolResult) -> str:
    if not res.rows:
        return "비교할 데이터가 없습니다."
    r = res.rows[0]
    keys = list(r)
    prev, cur = keys[1], keys[2]
    if res.params.get("group_by"):
        cat = keys[0]
        # 전체 증감률은 value(전체 기준), 그룹별 증감은 rows(그룹 기준)에서 가져온다
        if res.value is not None:
            out = f"{prev} → {cur} 전체 증감률은 {pct(res.value, True)}입니다 (전체 합산 기준)."
        else:
            out = f"{prev} → {cur} 전체 증감률은 계산할 수 없습니다 (이전 값이 0이거나 데이터 없음)."

        def item(row: dict) -> str:
            rate = f", {pct(row['증감률(%)'], True)}" if row.get("증감률(%)") is not None else ""
            return f"{row[cat]}({num(row.get(prev))} → {num(row.get(cur))}{rate})"

        down = [row for row in res.rows if row.get("증감") is not None and row["증감"] < 0]
        up = [row for row in res.rows if row.get("증감") is not None and row["증감"] > 0]
        same = [row for row in res.rows if row.get("증감") == 0]
        parts = []
        if down:
            parts.append(f"감소한 {cat}: {', '.join(item(x) for x in down)}")
        if up:
            parts.append(f"증가한 {cat}: {', '.join(item(x) for x in up)}")
        if same:
            parts.append(f"변화 없음: {', '.join(str(x[cat]) for x in same)}")
        unknown = [str(x[cat]) for x in res.rows if x.get("증감") is None]
        if unknown:
            parts.append(f"한쪽 기간 데이터가 없어 비교 불가: {', '.join(unknown)}")
        if parts:
            out += f" {cat}별(그룹 기준)로는 " + " / ".join(parts) + "."
        return out
    out = f"{prev} {num(r[prev])} → {cur} {num(r[cur])}, 증감 {num(r.get('증감'))}"
    if r.get("증감률(%)") is not None:
        out += f" ({pct(r['증감률(%)'], True)})"
    return out + "."


def _ratio(res: ToolResult) -> str:
    """비율(%)·비중(%)·×100 하지 않는 나눗셈 값(값(분자÷분모)). ToolResult의 숫자만 쓴다."""
    kind = res.params.get("kind")
    if not res.rows:
        return "결과가 없습니다."
    keys = list(res.rows[0])
    key = next((k for k in ("비중(%)", "비율(%)") if k in keys), None) or next((k for k in keys if str(k).startswith("값(")), None)
    if key is None:  # 알 수 없는 결과 형식: 숫자를 추측하지 않는다
        return "결과 형식을 알 수 없어 설명을 만들지 않았습니다. 표를 확인해 주세요."
    percent = key.endswith("(%)")
    fmt = pct if percent else num
    word = "비중" if kind == "share" else ("비율" if percent else key)
    if not res.params.get("group_by"):
        r = res.rows[0]
        parts = [f"{k} {num(v)}" for k, v in r.items() if k not in ("구분", key)]
        v = res.value if res.value is not None else r.get(key)
        if v is None:
            return "분모 합계가 0이라 계산할 수 없습니다."
        return f"전체 {word}은(는) {fmt(v)}입니다 ({', '.join(parts)})."
    cat = keys[0]
    valid = [r for r in res.rows if r.get(key) is not None]
    if not valid:
        return f"모든 그룹의 분모 합계가 0이라 {'비율' if percent else '값'}을 계산할 수 없습니다."
    if kind == "share":
        top = max(valid, key=lambda r: r[key])
        tied = [r for r in valid if r[key] == top[key]]
        names = ", ".join(str(r[cat]) for r in tied)
        out = f"{cat}별 비중은 {names}이(가) {pct(top[key])}로 가장 큽니다{' (공동)' if len(tied) > 1 else ''}. ({_listing(res.rows, key, '%')})"
        return out + " 비중은 반올림해 합이 100%와 조금 다를 수 있습니다."
    hi = max(valid, key=lambda r: r[key])
    lo = min(valid, key=lambda r: r[key])
    nums = [k for k in keys if k not in (cat, key)]

    def detail(r):
        return ", ".join(f"{k} {num(r.get(k))}" for k in nums)

    out = (f"{cat}별 {word}은(는) {hi[cat]}이(가) {fmt(hi[key])}로 가장 높고 ({detail(hi)}), "
           f"{lo[cat]}이(가) {fmt(lo[key])}로 가장 낮습니다 ({detail(lo)}).")
    if res.value is not None:
        out += f" 전체 {'비율은' if percent else '값은'} {fmt(res.value)}입니다."
    if not percent:
        out += " (합계 ÷ 합계, ×100 하지 않음)"
    if len(valid) < len(res.rows):
        out += f" 분모가 0인 {len(res.rows) - len(valid)}개 그룹은 계산하지 않았습니다."
    return out


_BUILDERS = {
    "calculate_ratio": _ratio,
    "count_overdue_tasks": _overdue,
    "summarize_by_category": _summary,
    "calculate_completion_rate": _completion,
    "mh_overrun": _mh,
    "cost_vs_budget": _cost,
    "check_missing_data": _missing,
    "aggregate": _aggregate,
    "find_recurring_issues": _recurring,
    "compare_periods": _periods,
}


def notices_from_summary(summary: Optional[str]) -> list[str]:
    return [line[2:].strip() for line in (summary or "").splitlines() if line.startswith("※ ")]
