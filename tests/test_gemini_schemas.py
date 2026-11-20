"""Gemini function calling용 도구 선언이 SDK 형식에 맞는지 (API 키 없이 확인할 수 있는 범위)."""

import json

import pytest

from conftest import AS_OF
from engine import TOOLS, gemini_tool_declarations, run_tool

types = pytest.importorskip("google.genai.types")


def test_every_tool_is_declared():
    assert [d["name"] for d in gemini_tool_declarations()] == list(TOOLS)


@pytest.mark.parametrize("decl", gemini_tool_declarations(), ids=lambda d: d["name"])
def test_declaration_fits_gemini_schema(decl):
    text = json.dumps(decl, ensure_ascii=False)
    assert "$ref" not in text and "$defs" not in text
    schema = types.Schema.model_validate(decl["parameters"])  # Gemini 전용 Schema 형식
    assert schema.properties
    types.FunctionDeclaration(name=decl["name"], description=decl["description"], parameters=schema)


def test_filter_items_are_inlined():
    agg = next(d for d in gemini_tool_declarations() if d["name"] == "aggregate")
    item = agg["parameters"]["properties"]["filters"]["items"]
    assert set(item["properties"]) == {"column", "op", "value"}
    assert item["properties"]["value"]["type"] == "string"


def test_optional_becomes_nullable():
    overdue = next(d for d in gemini_tool_declarations() if d["name"] == "count_overdue_tasks")
    group_by = overdue["parameters"]["properties"]["group_by"]
    assert group_by["type"] == "string" and group_by["nullable"] is True
    assert overdue["parameters"]["required"] == ["as_of"]


def test_in_filter_accepts_comma_string(kafe):
    """Gemini가 보내는 형식("P01,P05")과 목록 형식이 같은 결과를 낸다."""
    base = {"op": "count", "group_by": ["프로젝트ID"]}
    as_text = run_tool(kafe, "aggregate", {**base, "filters": [{"column": "프로젝트ID", "op": "in", "value": "P01, P05"}]})
    as_list = run_tool(kafe, "aggregate", {**base, "filters": [{"column": "프로젝트ID", "op": "in", "value": ["P01", "P05"]}]})
    assert as_text.ok and as_text.rows == as_list.rows
    assert {r["프로젝트ID"] for r in as_text.rows} == {"P01", "P05"}


def test_numeric_and_date_filters_from_strings(kafe):
    r = run_tool(kafe, "aggregate", {"op": "count", "filters": [
        {"column": "기한", "op": "<", "value": AS_OF}, {"column": "상태", "op": "!=", "value": "완료"}]})
    assert r.ok and r.value == 15  # 기한 초과 미완료 (Q1과 같은 수)
    r = run_tool(kafe, "aggregate", {"op": "count", "filters": [{"column": "실적MH", "op": ">", "value": "100"}]})
    assert r.ok and r.value > 0
