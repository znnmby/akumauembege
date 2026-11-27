from __future__ import annotations

import asyncio
import json
import logging
from openai import APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError
from pathlib import Path
from pydantic import ValidationError
from typing import Any
from app.core.config import Settings
from app.schemas.llm import LLMSelectionRequest, LLMSelectionResponse


logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "data" / "select_menu.txt"


class LLMClientError(RuntimeError):
    """Base class for sanitized provider failures."""


class LLMTimeoutError(LLMClientError):
    """The provider call exceeded its configured total deadline."""


class LLMUnavailableError(LLMClientError):
    """The provider could not serve the request."""


class LLMInvalidResponseError(LLMClientError):
    """The provider response failed size, JSON, or schema validation."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON provider memiliki kunci duplikat")
        result[key] = value
    return result


def _reject_non_finite_constant(_: str) -> None:
    raise ValueError("JSON provider memuat nilai non-finite")


class LLMClient:
    """Call a chat-completions-compatible provider exactly once per selection."""

    def __init__(
        self,
        settings: Settings,
        *,
        prompt_path: Path = DEFAULT_PROMPT_PATH,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = settings.llm_model
        self._timeout_seconds = settings.llm_timeout_seconds
        self._output_mode = settings.llm_output_mode
        self._max_output_tokens = settings.llm_max_output_tokens
        self._max_response_bytes = settings.llm_max_response_bytes
        self._max_selection_reason_length = settings.max_selection_reason_length
        self._system_prompt = prompt_path.read_text(encoding="utf-8").strip()
        if not self._system_prompt:
            raise ValueError("System prompt LLM tidak boleh kosong")
        if self._output_mode not in {"json_schema", "json_object", "text"}:
            raise ValueError("LLM_OUTPUT_MODE tidak didukung")

        self._client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=self._timeout_seconds,
            max_retries=0,
        )
        self._closed = False

    @property
    def model(self) -> str:
        return self._model

    def _response_format(self) -> dict[str, Any] | None:
        if self._output_mode == "json_schema":
            response_schema = LLMSelectionResponse.model_json_schema()
            response_schema["properties"]["selection_reason"]["maxLength"] = (
                self._max_selection_reason_length
            )
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "menu_selection",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        if self._output_mode == "json_object":
            return {"type": "json_object"}
        return None

    async def select_menu(self, request: LLMSelectionRequest) -> LLMSelectionResponse:
        if self._closed:
            raise LLMUnavailableError("Klien LLM sudah ditutup")

        user_payload = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        arguments: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_payload},
            ],
            "max_tokens": self._max_output_tokens,
        }
        response_format = self._response_format()
        if response_format is not None:
            arguments["response_format"] = response_format

        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.chat.completions.create(**arguments)
        except (APITimeoutError, TimeoutError) as exc:
            raise LLMTimeoutError("Provider LLM melewati batas waktu") from exc
        except APIStatusError as exc:
            logger.warning("Provider LLM mengembalikan status HTTP %s", exc.status_code)
            raise LLMUnavailableError("Provider LLM tidak tersedia") from exc
        except OpenAIError as exc:
            logger.warning("Komunikasi dengan provider LLM gagal (%s)", type(exc).__name__)
            raise LLMUnavailableError("Provider LLM tidak tersedia") from exc

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError("Provider LLM tidak mengembalikan konten") from exc
        if not isinstance(content, str):
            raise LLMInvalidResponseError("Provider LLM tidak mengembalikan teks JSON")
        return self._parse_response(content)

    def _parse_response(self, raw_content: str) -> LLMSelectionResponse:
        if len(raw_content.encode("utf-8")) > self._max_response_bytes:
            raise LLMInvalidResponseError("Respons provider LLM terlalu besar")

        normalized = raw_content.strip()
        if not normalized.startswith("{") or not normalized.endswith("}"):
            raise LLMInvalidResponseError("Respons provider LLM harus berupa satu objek JSON")
        try:
            payload = json.loads(
                normalized,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMInvalidResponseError("Respons provider LLM bukan JSON yang valid") from exc
        if not isinstance(payload, dict):
            raise LLMInvalidResponseError("Respons provider LLM harus berupa objek JSON")
        try:
            selection = LLMSelectionResponse.model_validate(payload)
        except ValidationError as exc:
            raise LLMInvalidResponseError("Respons provider LLM tidak sesuai schema") from exc
        if len(selection.selection_reason) > self._max_selection_reason_length:
            raise LLMInvalidResponseError("Respons provider LLM tidak sesuai schema")
        return selection

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.close()
