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

# Modelos que piensan (Claude Sonnet 5.5 y posteriores). Reglas de la API,
# leídas el 6-oct en la guía de migración a Sonnet 5.5:
# - `temperature` distinta de la predeterminada es un 400;
# - el pensamiento es adaptativo y se controla con `output_config.effort`;
#   `medium` es el punto de partida para varias herramientas;
# - los bloques `thinking` se devuelven SIN CAMBIOS en el turno siguiente, y el
#   historial no se edita (si se edita, los bloques ya no valen).
# `drop_block` hace que, si alguna ruta del agente edita el historial (la
# síntesis forzada o la condensación para streaming), la API descarte esos
# bloques en lugar de rechazar la petición.
MODELOS_QUE_PIENSAN = {"claude-sonnet-5-5"}
ESFUERZO = "medium"
_PENSAMIENTO = {
    "type": "adaptive",
    "block_binding": {"prefix_mismatch_behavior": "drop_block"},
}
_BETA_PENSAMIENTO = {"anthropic-beta": "thinking-binding-controls-2026-08-01"}
# El pensamiento cuenta dentro de `max_tokens`: con 4,096 se comería la
# respuesta.
MAX_TOKENS_CON_PENSAMIENTO = 16000


def _parametros(model: str, temperature: float, max_tokens: int) -> dict:
    if model in MODELOS_QUE_PIENSAN:
        return {
            "thinking": _PENSAMIENTO,
            "output_config": {"effort": ESFUERZO},
            "max_tokens": max(max_tokens, MAX_TOKENS_CON_PENSAMIENTO),
            "extra_headers": _BETA_PENSAMIENTO,
        }
    return {"temperature": temperature, "max_tokens": max_tokens}


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
                    **_parametros(model, temperature, max_tokens),
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
        solo_texto: bool = False,
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
            **_parametros(model, temperature, max_tokens),
        )
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
            # Para forzar texto se conservan las herramientas y se prohíbe
            # usarlas: quitar la lista cambia el prefijo y, con pensamiento,
            # invalida los bloques anteriores.
            if solo_texto:
                kwargs["tool_choice"] = {"type": "none"}

        response = await self._retry_on_rate_limit(
            lambda: self.client.messages.create(**kwargs),
            description=f"completion_with_tools({model})",
        )

        tool_calls = []
        textos = []

        # Una negativa llega con HTTP 200: hay que mirar `stop_reason` antes
        # de leer el contenido.
        if getattr(response, "stop_reason", None) == "refusal":
            detalle = getattr(response, "stop_details", None)
            logger.warning(f"Claude declinó la petición ({model}): {detalle}")
            return LLMToolResponse(
                content=("No puedo responder esta consulta. Reformúlala o "
                         "intenta con otra pregunta."),
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                rechazo=str(detalle or "refusal"),
            )

        # Por tipo, no por posición: con pensamiento, la respuesta puede
        # empezar con bloques `thinking`.
        for block in response.content:
            if block.type == "text":
                textos.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCallRequest(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict)
                              else json.loads(block.input),
                ))

        return LLMToolResponse(
            content="".join(textos) or None,
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            # El turno tal cual lo produjo el modelo, pensamiento incluido,
            # para devolverlo sin cambios.
            raw_content=[b.model_dump(exclude_none=True) for b in response.content],
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
                **_parametros(model, 0.5, max_tokens),
            ),
            description=f"quick_completion({model})",
        )
        text_parts = [
            b.text for b in response.content if b.type == "text"
        ]
        return " ".join(text_parts)

    # ── Models ──────────────────────────────────────────────

    def supported_models(self) -> list[ModelInfo]:
        # Los dos que había (claude-sonnet-4-20250514, claude-opus-4-20250514)
        # daban 404 desde agosto. Sólo se ofrece lo que se midió.
        return [
            ModelInfo(provider="anthropic", model_id="claude-sonnet-5-5",
                      display_name="Claude Sonnet 5.5"),
        ]
