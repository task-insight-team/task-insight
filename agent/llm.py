"""LLM 연결 (Gemini function calling) + 테스트용 FakeLLM.

- API 키: 인자 → 환경변수 GEMINI_API_KEY → st.secrets["GEMINI_API_KEY"] 순서로 찾는다. 키는 로그·메시지에 넣지 않는다.
- 모델: 인자 → 환경변수 GEMINI_MODEL → DEFAULT_MODEL.
- function calling mode=ANY: 모델은 반드시 함수 호출로만 답한다 (자유 문장 답변·숫자 생성 차단).
- 모든 실패는 LLMError(kind, 한국어 메시지)로 바꿔 올린다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Union

import time

# 2026-11-22 확인: gemini-2.5-flash는 신규 키에서 404, gemini-3.8-flash/3.5-flash는 503·504(과부하·지연)가 잦음.
# flash-lite는 함수 호출 응답이 2초 안팎이고 도구 선택이 정확해 기본값으로 둔다. GEMINI_MODEL로 바꿀 수 있다.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_TIMEOUT_S = 45
RETRY_KINDS = ("server", "timeout")  # 503·504 등 일시 오류는 1번만 다시 시도
RETRY_WAIT_S = 1.5


@dataclass
class LLMReply:
    calls: list[dict] = field(default_factory=list)  # [{"name": str, "args": dict}]
    text: str = ""


class LLMError(Exception):
    """kind: no_api_key | auth | quota | timeout | model | server | bad_response | unknown"""

    MESSAGES = {
        "no_api_key": "Gemini API 키(GEMINI_API_KEY)가 설정되지 않아 AI 분석을 할 수 없습니다.",
        "auth": "Gemini API 키가 올바르지 않거나 권한이 없습니다.",
        "quota": "Gemini API 사용 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.",
        "timeout": "Gemini 응답이 시간 안에 오지 않았습니다. 다시 시도해 주세요.",
        "model": "설정한 Gemini 모델(GEMINI_MODEL)을 사용할 수 없습니다.",
        "server": "Gemini 서버 오류로 분석 계획을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
        "bad_response": "Gemini 응답 형식이 올바르지 않아 분석 계획을 만들지 못했습니다.",
        "unknown": "AI 호출 중 알 수 없는 오류가 발생했습니다.",
    }

    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind if kind in self.MESSAGES else "unknown"
        self.detail = detail  # 개발자용 (키 미포함)
        super().__init__(self.MESSAGES[self.kind])

    @property
    def user_message(self) -> str:
        return self.MESSAGES[self.kind]


class LLMClient(Protocol):
    model: str

    def generate(self, system: str, user: str, tools: list[dict]) -> LLMReply: ...


def _plain(v: Any) -> Any:
    """SDK의 Map/List 객체를 순수 dict/list로."""
    try:
        return json.loads(json.dumps(v, default=lambda o: dict(o) if hasattr(o, "keys") else list(o)))
    except (TypeError, ValueError):
        return v


def _secret_key() -> Optional[str]:
    try:
        import streamlit as st  # 배포 환경에서만 의미 있음

        return st.secrets.get("GEMINI_API_KEY")  # secrets 파일이 없으면 예외 → None
    except Exception:  # noqa: BLE001
        return None


def find_api_key() -> Optional[str]:
    return os.environ.get("GEMINI_API_KEY") or _secret_key() or None


class GeminiClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S):
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        key = api_key or find_api_key()
        if not key:
            raise LLMError("no_api_key")
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_s * 1000))

    def generate(self, system: str, user: str, tools: list[dict]) -> LLMReply:
        types = self._types
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            tools=[types.Tool(function_declarations=tools)],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = None
        for attempt in range(2):
            try:
                resp = self._client.models.generate_content(model=self.model, contents=user, config=config)
                break
            except Exception as exc:  # noqa: BLE001
                err = classify_error(exc)
                if attempt == 0 and err.kind in RETRY_KINDS:
                    time.sleep(RETRY_WAIT_S)
                    continue
                raise err from None
        try:
            calls = [{"name": fc.name, "args": _plain(fc.args or {})} for fc in (resp.function_calls or [])]
        except Exception as exc:  # noqa: BLE001
            raise LLMError("bad_response", type(exc).__name__) from None
        text = ""
        if not calls:
            try:
                text = resp.text or ""
            except Exception:  # noqa: BLE001
                text = ""
        return LLMReply(calls=calls, text=text)


def classify_error(exc: Exception) -> LLMError:
    from google.genai import errors

    name = type(exc).__name__.lower()
    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", 0) or 0
        status = str(getattr(exc, "status", "") or "")
        msg = str(getattr(exc, "message", "") or "")
        if code in (401, 403) or "API_KEY" in msg.upper() or "PERMISSION" in status:
            return LLMError("auth", f"{code} {status}")
        if code == 429:
            return LLMError("quota", f"{code} {status}")
        if code == 404:
            return LLMError("model", f"{code} {status}")
        if code >= 500:
            return LLMError("server", f"{code} {status}")
        return LLMError("bad_response", f"{code} {status}")
    if "timeout" in name or "timedout" in name:
        return LLMError("timeout", type(exc).__name__)
    if "connect" in name or "network" in name:
        return LLMError("server", type(exc).__name__)
    return LLMError("unknown", type(exc).__name__)


Script = Union[LLMReply, Exception, Callable[[str, str, list], Union[LLMReply, Exception]]]


class FakeLLM:
    """테스트용. 미리 정한 응답(또는 예외)을 순서대로 돌려준다. 받은 프롬프트는 self.prompts에 남긴다."""

    def __init__(self, *script: Script, model: str = "fake-llm"):
        self.script = list(script)
        self.model = model
        self.prompts: list[dict] = []

    @staticmethod
    def calls(*pairs: tuple[str, dict]) -> LLMReply:
        return LLMReply(calls=[{"name": n, "args": a} for n, a in pairs])

    def generate(self, system: str, user: str, tools: list[dict]) -> LLMReply:
        self.prompts.append({"system": system, "user": user, "tools": [t["name"] for t in tools]})
        if not self.script:
            raise LLMError("bad_response", "FakeLLM 응답 없음")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, Exception):
            item = item(system, user, tools)
        if isinstance(item, Exception):
            raise item
        return item
