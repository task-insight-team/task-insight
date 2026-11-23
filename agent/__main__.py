"""터미널에서 에이전트 확인용.

    python -m agent "기한이 지난 미완료 업무는 몇 건인가?"            # 계획을 보여 주고 y/n 승인
    python -m agent "..." --yes --as-of 2026-11-19 --data data/kafe   # 자동 승인 (시연·점검용)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine import load_dataset

from agent import approve_plan, create_plan, execute_plan


def load_folder(folder: Path):
    files = {p.name: p for p in sorted(folder.glob("*.csv"))}
    res = load_dataset(files)
    if not res.ok:
        msgs = [i.message for r in res.reports for i in r.issues if i.level == "error"] + list(res.errors)
        raise SystemExit("데이터를 불러오지 못했습니다: " + " / ".join(map(str, msgs)))
    return res.dataset


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m agent")
    ap.add_argument("question")
    ap.add_argument("--as-of", default="2026-11-19", help="기준일 (kafe 데이터는 2026-11-19)")
    ap.add_argument("--data", default="data/kafe", help="CSV 폴더")
    ap.add_argument("--yes", action="store_true", help="계획을 자동 승인")
    args = ap.parse_args()

    ds = load_folder(Path(args.data))
    resp = create_plan(args.question, ds, args.as_of)
    print(f"[계획 상태] {resp.status}  (모델: {resp.model or '-'})")
    if resp.status != "plan":
        print(resp.message)
        sys.exit(0 if resp.status != "error" else 1)
    print(resp.plan.summary)
    for s in resp.plan.steps:
        print(f"  {s.id}. {s.tool} {s.params}")
    for r in resp.rejected:
        print(f"  (제외) {r}")
    if not args.yes and input("이 계획을 실행할까요? [y/N] ").strip().lower() != "y":
        print("실행하지 않았습니다.")
        return
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    print(f"\n[실행 상태] {out.status} - {out.message}\n")
    print(out.explanation)
    for s in out.steps:
        print(f"\n[{s.step_id}] {s.tool}: {len(s.result.rows)}행 (차트: {s.chart_type})")
        for row in s.result.rows[:5]:
            print("   ", row)


if __name__ == "__main__":
    main()
