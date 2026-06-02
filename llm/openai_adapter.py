"""
Adaptador OpenAI — soporta streaming y tool calling nativo.
"""
import json
from typing import AsyncIterator

from openai import AsyncOpenAI

from llm.base import BaseLLMAdapter
from models.schemas import (
    LLMMessage, LLMStreamChunk, LLMToolResponse,
    ToolCallRequest, ModelInfo,
)


class OpenAIAdapter(BaseLLMAdapter):

    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key)

    # ── Streaming (respuesta final) ─────────────────────────

    async def stream_completion(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[LLMStreamChunk]:
        oai_msgs = [{"role": m.role, "content": m.content} for m in messages]

        stream = await self.client.chat.completions.create(
            model=model,
            messages=oai_msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        input_tokens = 0
        output_tokens = 0

        async for chunk in stream:
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            if chunk.choices and chunk.choices[0].delta.content:
                yield LLMStreamChunk(
                    text=chunk.choices[0].delta.content,
                    finish_reason=chunk.choices[0].finish_reason,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

    # ── Tool calling ────────────────────────────────────────

    async def completion_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMToolResponse:
        # Convertir tools al formato OpenAI
        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }
            }
            for t in tools
        ]

        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Solo incluir tools si hay alguna; OpenAI rechaza tools=[] con tool_choice
        if oai_tools:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        tool_calls = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        return LLMToolResponse(
            content=choice.message.content,
            tool_calls=tool_calls,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )

    # ── Quick completion ────────────────────────────────────

    async def quick_completion(
        self,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int = 50,
    ) -> str:
        oai_msgs = [{"role": m.role, "content": m.content} for m in messages]
        response = await self.client.chat.completions.create(
            model=model,
            messages=oai_msgs,
            max_tokens=max_tokens,
            temperature=0.5,
        )
        return response.choices[0].message.content or ""

    # ── Models ──────────────────────────────────────────────

    def supported_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(provider="openai", model_id="gpt-4.1",
                      display_name="GPT-4.1"),
            ModelInfo(provider="openai", model_id="gpt-4.1-mini",
                      display_name="GPT-4.1 Mini"),
            ModelInfo(provider="openai", model_id="o3-mini",
                      display_name="o3-mini"),
        ]
