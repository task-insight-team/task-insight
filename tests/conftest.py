import json
from pathlib import Path

import pytest

from engine import load_dataset, read_table

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ERR = DATA / "errors"
KAFE = DATA / "kafe"
AS_OF = "2026-11-19"  # kafe 데이터 기준일


def kafe_files(*names):
    names = names or ("tasks", "employees", "projects")
    return {f"{n}.csv": KAFE / f"{n}.csv" for n in names}


@pytest.fixture(scope="session")
def kafe():
    res = load_dataset(kafe_files())
    assert res.ok and res.join.ok
    return res.dataset


@pytest.fixture(scope="session")
def answers():
    raw = json.loads((ROOT / "tests" / "fixtures" / "kafe" / "evaluation_answers.json").read_text(encoding="utf-8"))
    return {a["id"]: a for a in raw["answers"]}


@pytest.fixture(scope="session")
def sample_df():
    df, report = read_table(DATA / "sample.csv")
    assert report.ok and report.kind == "generic"
    return df


def rows_by(result, key, value_key):
    """rows를 {범주: 값} 딕셔너리로."""
    return {row[key]: row[value_key] for row in result.rows}
