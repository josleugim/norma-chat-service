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

# Que OpenAI no guarde una copia de cada conversación en la cuenta.
#
# `store=True` deja la petición y la respuesta en el panel de la cuenta, para
# sus productos de evals y destilación, hasta que alguien la borre: una copia
# más, aparte de los 30 días de monitoreo de abuso. Se fija explícito en vez de
# depender del valor por defecto, que no controlamos y que en la API de
# Responses es el contrario. Lo que se le dijo a COFECE el 6-oct sobre dónde se
# almacenan los datos depende de esto.
STORE = False

TIEMPO_LIMITE_S = 120.0

# Esfuerzo de razonamiento para los modelos que razonan. `medium`, igual que
# Claude Sonnet 5.5, para que la comparación de 6-oct sea pareja.
ESFUERZO_RAZONAMIENTO = "medium"


def es_de_razonamiento(model: str) -> bool:
    """
    gpt-5.x y la serie o razonan: no aceptan `temperature` distinta de 1 y
    piden `max_completion_tokens` en vez de `max_tokens`. Verificado el 6-oct
    con gpt-5.1 y gpt-5.6-terra: con los parámetros de gpt-4.1 dan 400.
    """
    m = (model or "").lower()
    return m.startswith("gpt-5") or (m[:1] == "o" and m[1:2].isdigit())


def _parametros(model: str, temperature: float, max_tokens: int) -> dict:
    """
    Los parámetros de muestreo según el modelo. En uno que razona, el
    razonamiento cuenta dentro de `max_completion_tokens`, así que el tope se
    agranda para que no se coma la respuesta.
    """
    if es_de_razonamiento(model):
        return {"max_completion_tokens": max(max_tokens, 16000),
                "reasoning_effort": ESFUERZO_RAZONAMIENTO}
    return {"temperature": temperature, "max_tokens": max_tokens}


def a_entrada_responses(messages: list[dict]) -> tuple[str, list[dict]]:
    """
    Historial en formato chat → `instructions` + `input` de /v1/responses.

    Un modelo que razona necesita sus bloques de razonamiento de vuelta junto
    con los resultados de herramientas ("any reasoning items returned in model
    responses with tool calls must also be passed back"). El agente los guarda
    en `_responses_output` del turno del asistente; aquí se reemiten tal cual,
    una sola vez aunque el agente parta el turno en un mensaje por herramienta.
    """
    instrucciones: list[str] = []
    entrada: list[dict] = []
    emitidos: set[str] = set()
    for m in messages:
        rol = m.get("role")
        if rol == "system":
            instrucciones.append(m.get("content") or "")
        elif rol == "tool":
            entrada.append({"type": "function_call_output",
                            "call_id": m["tool_call_id"],
                            "output": m.get("content") or ""})
        elif rol == "assistant" and (m.get("tool_calls") or m.get("_responses_output")):
            originales = m.get("_responses_output") or []
            for item in originales:
                clave = item.get("id") or item.get("call_id") or repr(item)
                if clave not in emitidos:
                    emitidos.add(clave)
                    entrada.append(item)
            for tc in m.get("tool_calls") or []:
                if tc["id"] in emitidos or any(
                        i.get("call_id") == tc["id"] for i in originales):
                    continue
                emitidos.add(tc["id"])
                entrada.append({"type": "function_call", "call_id": tc["id"],
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"]})
        elif rol in ("user", "assistant"):
            entrada.append({"role": rol, "content": m.get("content") or ""})
    return "\n\n".join(i for i in instrucciones if i), entrada


class OpenAIAdapter(BaseLLMAdapter):

    def __init__(self, api_key: str):
        # Tiempo límite explícito. Sin él rige el del SDK (10 minutos por
        # intento, con reintentos): la noche del 6-oct, en la banda del
        # holdout, varias llamadas se quedaron colgadas ~15 minutos cada una
        # antes de reintentar, y una banda de 30 minutos tardó casi 6 horas.
        # Para el usuario eso es un chat que no contesta. Una respuesta
        # completa tarda menos de un minuto (la más larga medida: 54 s); hasta
        # 4,096 tokens sin streaming caben con margen. El reintento sigue.
        self.client = AsyncOpenAI(api_key=api_key, timeout=TIEMPO_LIMITE_S)

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
            **_parametros(model, temperature, max_tokens),
            stream=True,
            stream_options={"include_usage": True},
            store=STORE,
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
        solo_texto: bool = False,
    ) -> LLMToolResponse:
        # Un modelo que razona no acepta herramientas con razonamiento en
        # /v1/chat/completions (400 verificado el 6-oct con gpt-5.6-terra):
        # va por /v1/responses. gpt-4.1 sigue por chat, sin cambios.
        if es_de_razonamiento(model):
            return await self._con_herramientas_responses(
                messages, model, tools, max_tokens, solo_texto)
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
            **_parametros(model, temperature, max_tokens),
            store=STORE,
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

    async def _con_herramientas_responses(self, messages, model, tools,
                                          max_tokens, solo_texto) -> LLMToolResponse:
        instrucciones, entrada = a_entrada_responses(messages)
        kwargs = dict(
            model=model,
            input=entrada,
            store=STORE,
            # Con store=False el razonamiento sólo viaja cifrado: hay que
            # pedirlo para poder devolverlo en el turno siguiente.
            include=["reasoning.encrypted_content"],
            reasoning={"effort": ESFUERZO_RAZONAMIENTO},
            max_output_tokens=max(max_tokens, 16000),
        )
        if instrucciones:
            kwargs["instructions"] = instrucciones
        if tools:
            kwargs["tools"] = [
                {"type": "function", "name": t["name"],
                 "description": t["description"], "parameters": t["parameters"],
                 "strict": False}
                for t in tools
            ]
            if solo_texto:
                kwargs["tool_choice"] = "none"
        response = await self.client.responses.create(**kwargs)

        tool_calls, textos = [], []
        for item in response.output:
            if item.type == "function_call":
                tool_calls.append(ToolCallRequest(
                    id=item.call_id, name=item.name,
                    arguments=json.loads(item.arguments or "{}")))
            elif item.type == "message":
                for c in item.content or []:
                    if getattr(c, "type", "") == "output_text":
                        textos.append(c.text)
        usage = response.usage
        return LLMToolResponse(
            content="".join(textos) or None,
            tool_calls=tool_calls,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            raw_content=[i.model_dump(exclude_none=True) for i in response.output],
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
            **_parametros(model, 0.5, max_tokens),
            store=STORE,
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
            ModelInfo(provider="openai", model_id="gpt-5.6-terra",
                      display_name="GPT-5.6 Terra"),
        ]
