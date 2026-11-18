from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.errors import ModelUnavailable

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ModelToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: tuple[ModelToolCall, ...] = ()


class MistralClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        reasoning_effort: str = "low",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(25),
        )
        self.supports_function_calling = True
        self.supports_structured_output = True
        self.capability_checked = False

    async def close(self) -> None:
        await self._client.aclose()

    async def check_capabilities(self) -> None:
        try:
            response = await self._client.get(f"/models/{self.model}", timeout=10)
            response.raise_for_status()
            capabilities = response.json().get("capabilities", {})
            if capabilities.get("function_calling") is False:
                self.supports_function_calling = False
            if capabilities.get("structured_output") is False:
                self.supports_structured_output = False
            self.capability_checked = True
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ModelUnavailable("Mistral model capability check failed") from exc

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> ModelReply:
        if not 0 <= temperature <= 1:
            raise ValueError("temperature must be between 0 and 1")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": self.reasoning_effort,
        }
        if tools and self.supports_function_calling:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        data = await self._post_completion(payload)
        return self.parse_reply(data)

    async def complete_structured(
        self,
        messages: list[dict[str, Any]],
        response_model: type[T],
        *,
        schema_name: str,
        max_tokens: int = 1200,
    ) -> T:
        schema = response_model.model_json_schema()
        request_messages = list(messages)
        last_error: Exception | None = None
        for attempt in range(2):
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": request_messages,
                "temperature": 0.1,
                "max_tokens": max_tokens,
                "reasoning_effort": self.reasoning_effort,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
            }
            data = await self._post_completion(payload)
            reply = self.parse_reply(data)
            try:
                return response_model.model_validate_json(reply.text)
            except ValidationError as exc:
                last_error = exc
                if attempt == 0:
                    request_messages.extend(
                        [
                            {"role": "assistant", "content": reply.text},
                            {
                                "role": "user",
                                "content": (
                                    "The response failed schema validation. Return only valid JSON."
                                ),
                            },
                        ]
                    )
        raise ModelUnavailable("Mistral returned invalid structured output") from last_error

    async def _post_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            if not data.get("choices"):
                raise ValueError("completion has no choices")
            return data
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ModelUnavailable("Mistral completion failed") from exc

    @classmethod
    def parse_reply(cls, payload: dict[str, Any]) -> ModelReply:
        message = payload["choices"][0]["message"]
        text = cls.extract_final_text(message.get("content"))
        calls: list[ModelToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function", {})
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict) or not function.get("name"):
                raise ModelUnavailable("Mistral returned a malformed tool call")
            calls.append(
                ModelToolCall(
                    name=function["name"],
                    arguments=arguments,
                    call_id=raw.get("id"),
                )
            )
        return ModelReply(text=text, tool_calls=tuple(calls))

    @staticmethod
    def extract_final_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict) and chunk.get("type") in {"text", "output_text"}:
                value = chunk.get("text") or chunk.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts).strip()
