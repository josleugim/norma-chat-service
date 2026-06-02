"""
Agente de Norma+ con loop de tool-calling.

El LLM decide qué herramientas usar, en qué orden, y cuántas veces.
Cada tool call emite un evento SSE de razonamiento para el frontend.
"""
import json
import logging
from typing import AsyncIterator

from llm.registry import LLMRegistry
from retrieval.criterios_client import CriteriosSearchClient
from retrieval.estadistica_client import EstadisticaSearchClient
from temporal.analyzer import TemporalAnalyzer
from core.citation_builder import CitationBuilder
from core.evidence_cache import EvidenceCache
from agent.tools import TOOLS
from prompts.system import AGENT_SYSTEM_PROMPT, TITLE_GENERATION_PROMPT
from models.schemas import (
    StreamEvent, LLMMessage,
)

logger = logging.getLogger(__name__)


class NormaPlusAgent:

    def __init__(
        self,
        llm_registry: LLMRegistry,
        criterios_client: CriteriosSearchClient,
        estadistica_client: EstadisticaSearchClient,
        temporal_analyzer: TemporalAnalyzer,
        citation_builder: CitationBuilder,
        evidence_cache: EvidenceCache,
        max_tool_calls: int = 6,
    ):
        self.llm_registry = llm_registry
        self.criterios = criterios_client
        self.estadistica = estadistica_client
        self.temporal = temporal_analyzer
        self.citations = citation_builder
        self.evidence_cache = evidence_cache
        self.max_tool_calls = max_tool_calls

        self.tool_executors = {
            "buscar_criterios": self._exec_buscar_criterios,
            "buscar_expedientes": self._exec_buscar_expedientes,
            "calcular_plazos": self._exec_calcular_plazos,
        }

    async def run(
        self,
        session_id: str,
        user_query: str,
        provider: str,
        model: str,
        chat_history: list[dict],
        is_first_message: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """
        Ejecuta el agente. Yields StreamEvents para el frontend.
        """
        adapter = self.llm_registry.get_adapter(provider)

        # Construir mensajes para el LLM en formato nativo
        messages = self._build_messages(user_query, chat_history, provider, session_id)

        all_criterios_results: list[list] = []
        all_expedientes_results: list[list] = []
        total_input_tokens = 0
        total_output_tokens = 0
        tool_call_count = 0
        final_text = ""

        # ── Agent loop ──────────────────────────────────────
        exhausted_tools = False
        while tool_call_count < self.max_tool_calls:
            response = await adapter.completion_with_tools(
                messages=messages,
                model=model,
                tools=TOOLS,
            )
            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens

            if response.tool_calls:
                # El LLM quiere usar herramientas
                for tc in response.tool_calls:
                    tool_call_count += 1

                    # Emitir evento de razonamiento
                    yield StreamEvent(
                        type="thinking",
                        data={
                            "tool": tc.name,
                            "description": self._describe_tool_call(tc.name, tc.arguments),
                            "step": tool_call_count,
                        },
                    )

                    # Ejecutar herramienta
                    try:
                        result = await self.tool_executors[tc.name](tc.arguments)
                    except Exception as e:
                        logger.error(f"Error ejecutando {tc.name}: {e}")
                        result = {"error": str(e)}

                    # Rastrear resultados para citation_builder
                    if tc.name == "buscar_criterios":
                        all_criterios_results.append(
                            [r.__dict__ if hasattr(r, "__dict__") else r for r in result]
                            if isinstance(result, list) else []
                        )
                    elif tc.name == "buscar_expedientes":
                        all_expedientes_results.append(
                            [r.model_dump() if hasattr(r, "model_dump") else r for r in result]
                            if isinstance(result, list) else []
                        )

                    # Agregar al historial del LLM (formato depende del proveedor)
                    result_str = json.dumps(
                        self._serialize_tool_result(tc.name, result),
                        ensure_ascii=False, default=str,
                    )
                    messages = self._append_tool_result(
                        messages, tc, result_str, provider
                    )
            else:
                # El LLM tiene la respuesta final — hacer streaming
                if response.content:
                    # Si ya generó texto sin tools, emitirlo
                    final_text = response.content
                    for chunk in self._chunk_text(final_text):
                        yield StreamEvent(type="token", data={"text": chunk})
                else:
                    # Hacer streaming de la respuesta final
                    final_parts = []
                    stream_messages = self._prepare_messages_for_stream(
                        messages, provider
                    )
                    async for chunk in adapter.stream_completion(
                        messages=stream_messages,
                        model=model,
                    ):
                        if chunk.text:
                            final_parts.append(chunk.text)
                            yield StreamEvent(type="token", data={"text": chunk.text})
                        if chunk.input_tokens:
                            total_input_tokens += chunk.input_tokens
                        if chunk.output_tokens:
                            total_output_tokens += chunk.output_tokens
                    final_text = "".join(final_parts)
                break
        else:
            # El while terminó porque tool_call_count >= max_tool_calls
            # sin que el LLM generara respuesta final.
            exhausted_tools = True
            logger.warning(
                f"Sesión {session_id}: agente agotó {self.max_tool_calls} "
                f"tool calls sin generar respuesta. Forzando respuesta final."
            )

        # ── Fallback: forzar respuesta final si se agotaron tools ──
        if exhausted_tools:
            yield StreamEvent(
                type="thinking",
                data={
                    "tool": "_synthesis",
                    "description": "Sintetizando respuesta con la información recopilada...",
                    "step": tool_call_count + 1,
                },
            )
            # Inyectar instrucción para que el LLM responda con lo que tiene
            fallback_instruction = (
                "Has alcanzado el límite de herramientas para esta consulta. "
                "Con la información que ya recuperaste, genera la MEJOR "
                "respuesta posible ahora. Incluye las citas [C1], [E1], etc. "
                "y la sección FUENTES. Si la información es incompleta, "
                "indícalo al usuario y sugiere reformular la consulta."
            )
            messages = self._append_user_message(messages, fallback_instruction, provider)

            # Forzar una completion sin tools para que el LLM responda
            try:
                fallback_response = await adapter.completion_with_tools(
                    messages=messages,
                    model=model,
                    tools=[],  # sin tools → forzar respuesta de texto
                )
                total_input_tokens += fallback_response.input_tokens
                total_output_tokens += fallback_response.output_tokens

                if fallback_response.content:
                    final_text = fallback_response.content
                    for chunk in self._chunk_text(final_text):
                        yield StreamEvent(type="token", data={"text": chunk})
                else:
                    # Último recurso: streaming
                    final_parts = []
                    stream_messages = self._prepare_messages_for_stream(
                        messages, provider
                    )
                    async for chunk in adapter.stream_completion(
                        messages=stream_messages,
                        model=model,
                    ):
                        if chunk.text:
                            final_parts.append(chunk.text)
                            yield StreamEvent(type="token", data={"text": chunk.text})
                        if chunk.input_tokens:
                            total_input_tokens += chunk.input_tokens
                        if chunk.output_tokens:
                            total_output_tokens += chunk.output_tokens
                    final_text = "".join(final_parts)
            except Exception as e:
                logger.error(f"Error en fallback de respuesta: {e}")
                final_text = (
                    "La consulta requirió más búsquedas de las que puedo "
                    "realizar en un solo turno. Por favor, reformula tu "
                    "pregunta de forma más específica o divídela en partes."
                )
                yield StreamEvent(type="token", data={"text": final_text})

        # ── Actualizar cache de evidencia ───────────────────
        flat_criterios = [item for sublist in all_criterios_results for item in sublist]
        flat_expedientes = [item for sublist in all_expedientes_results for item in sublist]
        if flat_criterios or flat_expedientes:
            self.evidence_cache.update(
                session_id=session_id,
                query=user_query,
                criterios=flat_criterios,
                expedientes=flat_expedientes,
            )

        # ── Parsear citas ───────────────────────────────────
        _, references = self.citations.build_references(
            final_text,
            all_criterios_results,
            all_expedientes_results,
        )

        if references:
            yield StreamEvent(
                type="references",
                data={"items": [ref.model_dump() for ref in references]},
            )

        # ── Generar título si es primer mensaje ─────────────
        session_title = None
        if is_first_message and final_text:
            try:
                session_title = await self._generate_title(
                    user_query, adapter, model
                )
            except Exception as e:
                logger.warning(f"Error generando título: {e}")
                session_title = user_query[:60]

        # ── Done ────────────────────────────────────────────
        yield StreamEvent(
            type="done",
            data={
                "tokens_input": total_input_tokens,
                "tokens_output": total_output_tokens,
                "tool_calls_count": tool_call_count,
                "session_title": session_title,
                "exhausted_tools": exhausted_tools,
            },
        )

    # ── Constructores de mensajes ───────────────────────────

    def _build_messages(
        self, user_query: str, chat_history: list[dict],
        provider: str, session_id: str,
    ) -> list[dict]:
        """Construye mensajes en formato dict genérico, incluyendo cache."""
        # Obtener evidencia cacheada relevante
        cached_criterios, cached_expedientes, used_cache = \
            self.evidence_cache.select(session_id, user_query)

        # Construir contexto del cache si hay evidencia
        cache_context = ""
        if used_cache and (cached_criterios or cached_expedientes):
            cache_context = self.evidence_cache.get_context_summary(session_id)

        # System prompt + cache context
        system_content = AGENT_SYSTEM_PROMPT
        if cache_context:
            system_content += (
                "\n\n## EVIDENCIA DE TURNOS ANTERIORES\n"
                "La siguiente evidencia fue recuperada en turnos anteriores de "
                "esta conversación. Puedes usarla si es relevante para la consulta "
                "actual, sin necesidad de volver a buscar. Si necesitas información "
                "adicional o más reciente, usa las herramientas.\n\n"
                + cache_context
            )

        messages = [
            {"role": "system", "content": system_content},
        ]
        # Últimos 8 turnos del historial
        for m in chat_history[-8:]:
            messages.append({
                "role": m.get("role", "user"),
                "content": m.get("content", ""),
            })
        messages.append({"role": "user", "content": user_query})
        return messages

    def _append_tool_result(
        self,
        messages: list[dict],
        tool_call,
        result_str: str,
        provider: str,
    ) -> list[dict]:
        """Agrega resultado de tool call al historial (formato del proveedor)."""
        if provider == "openai":
            # OpenAI: assistant message with tool_calls + tool result message
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                    },
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_str,
            })
        elif provider == "anthropic":
            # Anthropic: assistant content with tool_use + user content with tool_result
            messages.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": tool_call.arguments,
                    }
                ],
            })
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": result_str,
                    }
                ],
            })
        return messages

    # ── Ejecutores de herramientas ──────────────────────────

    async def _exec_buscar_criterios(self, args: dict) -> list:
        results = await self.criterios.search(
            query=args["query"],
            top_k=args.get("top_k", 15),
        )
        return [
            {
                "id": r.id,
                "text": r.text[:700],  # truncar para no explotar el contexto
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
        ]

    async def _exec_buscar_expedientes(self, args: dict) -> list:
        text_search = args.get("text_search")
        limit = args.get("limit", 50)
        has_multas = args.get("has_multas", False)

        filters = {}
        # Mapeo de parámetros del agente → filtros del cliente
        filter_keys = {
            "autoridad": "authority",
            "tipo_procedimiento": "typeOfProcedure",
            "sentido_resolucion": "senseOfResolution",
            "id_expediente": "caseLink",
            # Rango de años
            "fecha_resolucion_desde": "senseOfResolutionFrom",
            "fecha_resolucion_hasta": "senseOfResolutionTo",
        }
        for agent_key, api_key in filter_keys.items():
            if agent_key in args and args[agent_key] is not None:
                filters[api_key] = args[agent_key]

        # Si piden multas, traer más resultados para filtrar después
        fetch_limit = limit * 3 if has_multas else limit

        # text_search se envía como searchData (búsqueda libre cross-field
        # en caseLink, nombre, agentes económicos y mercados relevantes)
        results = await self.estadistica.search(
            text_search=text_search,
            filters=filters if filters else None,
            limit=fetch_limit,
        )

        serialized = [r.model_dump() for r in results]

        # Post-filtro: has_multas (la API no tiene este filtro nativo,
        # se filtra localmente por agentFines no vacío)
        if has_multas:
            def _has_fines(r: dict) -> bool:
                fines = r.get("agentFines")
                if fines is None:
                    return False
                if isinstance(fines, dict):
                    return bool(fines)  # {} = False
                return str(fines).strip() not in ("", "{}", "None", "null")

            serialized = [r for r in serialized if _has_fines(r)]
            serialized = serialized[:limit]

        return serialized

    async def _exec_calcular_plazos(self, args: dict) -> dict:
        expedientes = args.get("expedientes", [])
        enriched = self.temporal.enrich_with_plazos(expedientes)

        plazo_field = "dias_habiles_notif_resol"
        fecha_inicio = args.get("fecha_inicio", "fecha_notificacion")
        if fecha_inicio == "fecha_admision":
            plazo_field = "dias_habiles_admis_resol"

        result = {"total_expedientes": len(enriched)}

        # Filtrar por plazo si se pidió
        max_dh = args.get("max_dias_habiles")
        min_dh = args.get("min_dias_habiles")
        if max_dh is not None or min_dh is not None:
            filtered = self.temporal.filter_by_plazo(
                enriched,
                max_dias_habiles=max_dh,
                min_dias_habiles=min_dh,
                plazo_field=plazo_field,
            )
            result["filtered_count"] = len(filtered)
            result["expedientes"] = filtered[:50]  # cap para no explotar contexto
        else:
            result["expedientes"] = enriched[:50]

        # Stats si se pidieron
        if args.get("compute_stats"):
            data_for_stats = result.get("expedientes", enriched)
            result["stats"] = self.temporal.compute_stats(
                data_for_stats, plazo_field=plazo_field
            )

        return result

    # ── Helpers ─────────────────────────────────────────────

    def _serialize_tool_result(self, tool_name: str, result) -> dict | list:
        """Serializa resultado de tool para el LLM."""
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"results": result, "count": len(result)}
        return {"result": str(result)}

    def _prepare_messages_for_stream(
        self, messages: list[dict], provider: str,
    ) -> list[LLMMessage]:
        """
        Convierte mensajes dict (que pueden contener tool_calls/tool_results
        con content como list[dict]) a LLMMessage(content=str) para
        stream_completion.

        Los mensajes de tool call intermedios se condensan en un resumen
        textual que el LLM puede usar como contexto para generar la
        respuesta final.
        """
        clean: list[LLMMessage] = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content")

            # System message → siempre string
            if role == "system":
                clean.append(LLMMessage(
                    role="system",
                    content=content if isinstance(content, str) else str(content),
                ))
                continue

            # Mensajes con content como lista (Anthropic tool_use / tool_result)
            if isinstance(content, list):
                # Extraer texto legible de los bloques
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            text_parts.append(
                                f"[Herramienta: {block.get('name', '?')}]"
                            )
                        elif block.get("type") == "tool_result":
                            # Condensar resultado de tool a un resumen corto
                            tool_content = block.get("content", "")
                            if isinstance(tool_content, str) and len(tool_content) > 500:
                                tool_content = tool_content[:500] + "..."
                            text_parts.append(
                                f"[Resultado de herramienta: {tool_content[:200]}...]"
                                if len(str(tool_content)) > 200
                                else f"[Resultado: {tool_content}]"
                            )
                condensed = "\n".join(text_parts) if text_parts else "[contenido de herramienta]"
                # Reasignar role: Anthropic tool_result viene como "user"
                clean.append(LLMMessage(role=role, content=condensed))
                continue

            # OpenAI assistant message con tool_calls (content=None)
            if content is None and m.get("tool_calls"):
                tool_names = [
                    tc.get("function", {}).get("name", "?")
                    for tc in m.get("tool_calls", [])
                ]
                clean.append(LLMMessage(
                    role="assistant",
                    content=f"[Herramientas llamadas: {', '.join(tool_names)}]",
                ))
                continue

            # OpenAI tool result message
            if role == "tool":
                tool_content = content or ""
                if len(tool_content) > 500:
                    tool_content = tool_content[:500] + "..."
                clean.append(LLMMessage(
                    role="user",  # stream_completion no soporta role=tool
                    content=f"[Resultado de herramienta: {tool_content[:200]}...]"
                    if len(tool_content) > 200
                    else f"[Resultado: {tool_content}]",
                ))
                continue

            # Mensaje normal (user/assistant con content string)
            clean.append(LLMMessage(
                role=role,
                content=content if isinstance(content, str) else str(content or ""),
            ))

        return clean

    def _append_user_message(
        self, messages: list[dict], text: str, provider: str,
    ) -> list[dict]:
        """Agrega un mensaje de usuario al historial en formato del proveedor."""
        if provider == "anthropic":
            # Anthropic requiere alternancia user/assistant.
            # Si el último mensaje es un user (tool_result), fusionar.
            if messages and messages[-1].get("role") == "user":
                last = messages[-1]
                if isinstance(last.get("content"), list):
                    last["content"].append({"type": "text", "text": text})
                else:
                    last["content"] = str(last.get("content", "")) + "\n\n" + text
            else:
                messages.append({"role": "user", "content": text})
        else:
            messages.append({"role": "user", "content": text})
        return messages

    def _describe_tool_call(self, name: str, args: dict) -> str:
        """Descripción legible para el panel de razonamiento del frontend."""
        if name == "buscar_criterios":
            return f"Buscando criterios sobre: {args.get('query', '')}"
        elif name == "buscar_expedientes":
            return self._describe_expedientes_search(args)
        elif name == "calcular_plazos":
            n = len(args.get("expedientes", []))
            return f"Calculando plazos en días hábiles para {n} expedientes..."
        return f"Ejecutando {name}"

    def _describe_expedientes_search(self, args: dict) -> str:
        parts = ["Buscando expedientes"]
        if args.get("text_search"):
            parts.append(f"con '{args['text_search']}'")
        if args.get("sentido_resolucion"):
            parts.append(f"({args['sentido_resolucion'].lower()})")
        if args.get("autoridad"):
            parts.append(f"de {args['autoridad']}")
        if args.get("has_multas"):
            parts.append("con multas")
        if args.get("tipo_procedimiento"):
            parts.append(f"— {args['tipo_procedimiento'].lower()}")
        return " ".join(parts)

    def _chunk_text(self, text: str, chunk_size: int = 20) -> list[str]:
        """Divide texto en chunks para simular streaming."""
        words = text.split(" ")
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i + chunk_size])
            if i > 0:
                chunk = " " + chunk
            chunks.append(chunk)
        return chunks

    async def _generate_title(
        self, user_query: str, adapter, model: str
    ) -> str:
        title = await adapter.quick_completion(
            messages=[
                LLMMessage(role="system", content=TITLE_GENERATION_PROMPT),
                LLMMessage(role="user", content=user_query),
            ],
            model=model,
            max_tokens=20,
        )
        return title.strip()[:100]
