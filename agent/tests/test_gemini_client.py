"""GeminiClient 단위 테스트: SDK를 가짜로 바꿔 요청 형식·응답 해석·오류 분류만 확인 (네트워크 없음)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("google.genai")
from google.genai import errors  # noqa: E402

from agent.llm import GeminiClient, LLMError, classify_error  # noqa: E402


class FakeModels:
    def __init__(self, outcome):
        self.outcome = outcome
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def make_client(monkeypatch, outcome, model=None):
    models = FakeModels(outcome)
    monkeypatch.setattr("google.genai.Client", lambda **kw: SimpleNamespace(models=models, kw=kw))
    return GeminiClient(api_key="test-key", model=model), models


def test_parses_function_calls_and_uses_any_mode(monkeypatch):
    resp = SimpleNamespace(function_calls=[SimpleNamespace(name="mh_overrun", args={"group_by": "프로젝트ID"})], text=None)
    client, models = make_client(monkeypatch, resp)
    reply = client.generate("sys", "user", [{"name": "mh_overrun", "description": "d",
                                             "parameters": {"type": "object", "properties": {"group_by": {"type": "string"}}}}])
    assert reply.calls == [{"name": "mh_overrun", "args": {"group_by": "프로젝트ID"}}]
    cfg = models.kwargs["config"]
    assert cfg.temperature == 0
    assert cfg.tool_config.function_calling_config.mode.value == "ANY"
    assert cfg.automatic_function_calling.disable is True


def test_model_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "my-model")
    client, _ = make_client(monkeypatch, SimpleNamespace(function_calls=[], text=""))
    assert client.model == "my-model"


def test_model_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "env-model")
    client, _ = make_client(monkeypatch, SimpleNamespace(function_calls=[], text=""), model="arg-model")
    assert client.model == "arg-model"


def test_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("agent.llm._secret_key", lambda: None)
    with pytest.raises(LLMError) as e:
        GeminiClient()
    assert e.value.kind == "no_api_key"


@pytest.mark.parametrize("code,status,kind", [(429, "RESOURCE_EXHAUSTED", "quota"), (403, "PERMISSION_DENIED", "auth"),
                                              (404, "NOT_FOUND", "model"), (500, "INTERNAL", "server"),
                                              (400, "INVALID_ARGUMENT", "bad_response")])
def test_api_errors_are_classified(monkeypatch, code, status, kind):
    err_cls = errors.ServerError if code >= 500 else errors.ClientError
    exc = err_cls(code, {"error": {"code": code, "message": "x", "status": status}})
    client, _ = make_client(monkeypatch, exc)
    with pytest.raises(LLMError) as e:
        client.generate("s", "u", [])
    assert e.value.kind == kind
    assert "test-key" not in str(e.value) and "test-key" not in e.value.detail


def test_invalid_key_message_is_auth():
    exc = errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid. API_KEY_INVALID", "status": "INVALID_ARGUMENT"}})
    assert classify_error(exc).kind == "auth"


def test_timeout_classified():
    class ReadTimeout(Exception):
        pass
    assert classify_error(ReadTimeout()).kind == "timeout"
