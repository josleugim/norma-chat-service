"""
Adaptador Anthropic — soporta streaming y tool use nativo.
Incluye retry con backoff exponencial para rate limiting (429).
"""
import json
import asyncio
import logging
from typing import AsyncIterator

import anthropic

from llm.base import BaseLLMAdapter
from models.schemas import (
    LLMMessage, LLMStreamChunk, LLMToolResponse,
    ToolCallRequest, ModelInfo,
)

logger = logging.getLogger(__name__)

# Retry config para 429 rate limiting
MAX_RETRIES = 3
INITIAL_BACKOFF = 5  # segundos
BACKOFF_MULTIPLIER = 2


class AnthropicAdapter(BaseLLMAdapter):

    def __init__(self, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    # ── Retry helper ───────────────────────────────────────────

    async def _retry_on_rate_limit(self, coro_factory, description: str = ""):
        """
        Ejecuta una coroutine factory con retry en caso de 429.
        coro_factory es una función que retorna un nuevo awaitable cada vez.
        """
        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await coro_factory()
            except anthropic.RateLimitError as e:
                if attempt == MAX_RETRIES:
                    logger.error(
                        f"Anthropic rate limit: agotados {MAX_RETRIES} reintentos "
                        f"para {description}"
                    )
                    raise
                wait = backoff + (attempt * 2)  # jitter simple
                logger.warning(
                    f"Anthropic 429 rate limit ({description}). "
                    f"Reintento {attempt}/{MAX_RETRIES} en {wait}s..."
                )
                await asyncio.sleep(wait)
                backoff *= BACKOFF_MULTIPLIER
            except anthropic.APIStatusError as e:
                if e.status_code == 529:  # API overloaded
                    if attempt == MAX_RETRIES:
                        raise
                    wait = backoff * 2
                    logger.warning(
                        f"Anthropic 529 overloaded ({description}). "
                        f"Reintento {attempt}/{MAX_RETRIES} en {wait}s..."
                    )
                    await asyncio.sleep(wait)
                    backoff *= BACKOFF_MULTIPLIER
                else:
                    raise

    # ── Helpers ─────────────────────────────────────────────

    def _split_system(self, messages: list[LLMMessage]) -> tuple[str, list[dict]]:
        """Anthropic separa system prompt del messages array."""
        system_msg = ""
        user_msgs = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                user_msgs.append({"role": m.role, "content": m.content})
        return system_msg, user_msgs

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convierte formato genérico de tools al formato Anthropic."""
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]

    # ── Streaming (respuesta final) ─────────────────────────

    async def stream_completion(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[LLMStreamChunk]:
        system_msg, user_msgs = self._split_system(messages)

        backoff = INITIAL_BACKOFF
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self.client.messages.stream(
                    model=model,
                    system=system_msg,
                    messages=user_msgs,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ) as stream:
                    async for text in stream.text_stream:
                        yield LLMStreamChunk(text=text)

                    final_message = await stream.get_final_message()
                    usage = final_message.usage
                    yield LLMStreamChunk(
                        text="",
                        finish_reason="end_turn",
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                    )
                return  # éxito, salir del retry loop
            except anthropic.RateLimitError:
                if attempt == MAX_RETRIES:
                    raise
                wait = backoff + (attempt * 2)
                logger.warning(
                    f"Anthropic 429 en stream_completion. "
                    f"Reintento {attempt}/{MAX_RETRIES} en {wait}s..."
                )
                await asyncio.sleep(wait)
                backoff *= BACKOFF_MULTIPLIER

    # ── Tool calling ────────────────────────────────────────

    async def completion_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMToolResponse:
        # Extraer system si está en messages
        system_msg = ""
        api_msgs = []
        for m in messages:
            if m.get("role") == "system":
                system_msg = m.get("content", "")
            else:
                api_msgs.append(m)

        anthropic_tools = self._convert_tools(tools) if tools else []

        kwargs = dict(
            model=model,
            system=system_msg,
            messages=api_msgs,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = await self._retry_on_rate_limit(
            lambda: self.client.messages.create(**kwargs),
            description=f"completion_with_tools({model})",
        )

        tool_calls = []
        content_text = None

        for block in response.content:
            if block.type == "text":
                content_text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCallRequest(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict)
                              else json.loads(block.input),
                ))

        return LLMToolResponse(
            content=content_text,
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    # ── Quick completion ────────────────────────────────────

    async def quick_completion(
        self,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int = 50,
    ) -> str:
        system_msg, user_msgs = self._split_system(messages)

        response = await self._retry_on_rate_limit(
            lambda: self.client.messages.create(
                model=model,
                system=system_msg,
                messages=user_msgs,
                max_tokens=max_tokens,
                temperature=0.5,
            ),
            description=f"quick_completion({model})",
        )
        text_parts = [
            b.text for b in response.content if b.type == "text"
        ]
        return " ".join(text_parts)

    # ── Models ──────────────────────────────────────────────

    def supported_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(provider="anthropic", model_id="claude-sonnet-4-20250514",
                      display_name="Claude Sonnet 4"),
            ModelInfo(provider="anthropic", model_id="claude-opus-4-20250514",
                      display_name="Claude Opus 4"),
        ]
