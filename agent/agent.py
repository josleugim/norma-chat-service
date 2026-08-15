"""
Agente de Norma+ con loop de tool-calling.

El LLM decide qué herramientas usar, en qué orden, y cuántas veces.
Cada tool call emite un evento SSE de razonamiento para el frontend.
"""
import json
import logging
from typing import AsyncIterator, Optional

from llm.registry import LLMRegistry
from retrieval.criterios_client import CriteriosSearchClient
from retrieval.estadistica_client import EstadisticaSearchClient
from temporal.analyzer import TemporalAnalyzer
from core.citation_builder import CitationBuilder
from core.citations import CitationRegistry
from core.evidence_cache import EvidenceCache
from agent.turn_state import TurnState
from core.tracing import (
    NullSink, Request as TraceRequest, TraceCollector, analyze_answer,
    build_versions, interpret,
)
from core.tracing.versioning import sha256_short
from agent.tools import TOOLS
from prompts.system import AGENT_SYSTEM_PROMPT, TITLE_GENERATION_PROMPT
from models.schemas import (
    StreamEvent, LLMMessage,
)

logger = logging.getLogger(__name__)

# Truncado del texto de criterios al serializarlos para el LLM.
# Es una de las tres etapas de retrieval: lo que entra al contexto no es lo
# mismo que lo que devolvió el buscador.
CRITERIO_CONTEXT_CHARS = 700


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
        trace_sink=None,
        manifest_store=None,
        settings=None,
    ):
        self.llm_registry = llm_registry
        self.criterios = criterios_client
        self.estadistica = estadistica_client
        self.temporal = temporal_analyzer
        self.citations = citation_builder
        self.evidence_cache = evidence_cache
        self.max_tool_calls = max_tool_calls

        # ── Trazabilidad ────────────────────────────────────
        # Observa; nunca altera el comportamiento del agente.
        self.trace_sink = trace_sink or NullSink()
        self.manifest_store = manifest_store
        self.settings = settings

        self.tool_executors = {
            "buscar_criterios": self._exec_buscar_criterios,
            "buscar_expedientes": self._exec_buscar_expedientes,
            "contar_expedientes": self._exec_contar_expedientes,
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
        turn_index: int = 0,
        question_set_id: Optional[str] = None,
        client: str = "frontend",
    ) -> AsyncIterator[StreamEvent]:
        """
        Ejecuta el agente. Yields StreamEvents para el frontend.
        """
        adapter = self.llm_registry.get_adapter(provider)

        collector = self._new_collector(
            session_id=session_id,
            user_query=user_query,
            provider=provider,
            model=model,
            chat_history=chat_history,
            is_first_message=is_first_message,
            turn_index=turn_index,
            question_set_id=question_set_id,
            client=client,
        )

        try:
            async for event in self._run_traced(
                collector, adapter, session_id, user_query, provider, model,
                chat_history, is_first_message,
            ):
                if collector is not None:
                    collector.count_sse_event(event.type)
                yield event
        finally:
            # La traza se escribe también si la petición falla o el cliente
            # corta la conexión — que son justo los casos del comentario #5,
            # los más valiosos de conservar.
            self._flush_trace(collector)

    async def _run_traced(
        self,
        collector,
        adapter,
        session_id: str,
        user_query: str,
        provider: str,
        model: str,
        chat_history: list[dict],
        is_first_message: bool,
    ) -> AsyncIterator[StreamEvent]:
        # Todo lo que dura este turno. El agente es un singleton, así que
        # nada de esto puede vivir en self: dos peticiones simultáneas se
        # pisarían.
        state = TurnState()

        # Construir mensajes para el LLM en formato nativo
        messages = self._build_messages(
            user_query, chat_history, provider, session_id, collector
        )

        all_criterios_results: list[list] = []
        all_expedientes_results: list[list] = []
        total_input_tokens = 0
        total_output_tokens = 0
        tool_call_count = 0
        final_text = ""

        # ── Agent loop ──────────────────────────────────────
        exhausted_tools = False
        while tool_call_count < self.max_tool_calls:
            if collector is not None:
                collector.begin_step("llm_call")
            response = await adapter.completion_with_tools(
                messages=messages,
                model=model,
                tools=TOOLS,
            )
            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            if collector is not None:
                collector.end_step(tokens={
                    "input": response.input_tokens,
                    "output": response.output_tokens,
                })

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
                    if collector is not None:
                        collector.begin_step("tool_call", tool=tc.name,
                                             arguments=tc.arguments)
                    try:
                        result = await self.tool_executors[tc.name](
                            tc.arguments, collector, state
                        )
                        if collector is not None:
                            collector.record_result(result)
                            collector.end_step("ok")
                    except Exception as e:
                        logger.error(f"Error ejecutando {tc.name}: {e}")
                        result = {"error": str(e)}
                        if collector is not None:
                            collector.end_step("error", error=str(e))
                            collector.add_error(f"tool:{tc.name}", str(e))

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
                        self._serialize_tool_result(tc.name, result, state),
                        ensure_ascii=False, default=str,
                    )
                    messages = self._append_tool_result(
                        messages, tc, result_str, provider
                    )
            else:
                # El LLM tiene la respuesta final — hacer streaming
                if response.content:
                    # Si ya generó texto sin tools, emitirlo
                    if collector is not None:
                        collector.set_decision("final_answer_path", "content")
                    final_text = response.content
                    for chunk in self._chunk_text(final_text):
                        yield StreamEvent(type="token", data={"text": chunk})
                else:
                    # Hacer streaming de la respuesta final
                    if collector is not None:
                        collector.set_decision("final_answer_path", "stream")
                        collector.begin_step("llm_call")
                    final_parts = []
                    stream_messages = self._prepare_messages_for_stream(
                        messages, provider, collector
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
                    if collector is not None:
                        collector.end_step("ok")
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
            if collector is not None:
                collector.set_decision("final_answer_path", "forced_synthesis")
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
                        messages, provider, collector
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
                if collector is not None:
                    collector.add_error("fallback_synthesis", str(e))
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

        # ── Resolver citas contra el registro del turno ─────
        # Por diccionario, no por posición: es lo que impide que una cita
        # termine apuntando a un expediente distinto del que se usó.
        references, citas_sin_resolver = self.citations.build_from_registry(
            final_text, state.registry
        )

        if references:
            yield StreamEvent(
                type="references",
                data={"items": [ref.model_dump() for ref in references]},
            )

        # ── Analizar la respuesta para la traza ─────────────
        if collector is not None:
            try:
                collector.set_answer(analyze_answer(
                    text=final_text,
                    registry=state.registry,
                    references=references,
                    unresolved=citas_sin_resolver,
                    docs_in_context=collector.context.docs_in_context,
                    expected_prefixes=collector.interpretation.scope.procedure_prefix,
                ))
            except Exception as e:
                logger.warning(f"Error analizando respuesta para la traza: {e}")
                collector.add_error("analyze_answer", str(e))

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
        # El trace_id viaja al frontend para que un reporte de "esta respuesta
        # salió mal" apunte a una traza concreta.
        yield StreamEvent(
            type="done",
            data={
                "tokens_input": total_input_tokens,
                "tokens_output": total_output_tokens,
                "tool_calls_count": tool_call_count,
                "session_title": session_title,
                "exhausted_tools": exhausted_tools,
                "trace_id": collector.trace_id if collector else None,
            },
        )

        if collector is not None:
            collector.pending_finish = {
                "status": "ok",
                "exhausted_tools": exhausted_tools,
                "tokens_input": total_input_tokens,
                "tokens_output": total_output_tokens,
            }

    # ── Trazabilidad ────────────────────────────────────────

    def _new_collector(
        self, session_id: str, user_query: str, provider: str, model: str,
        chat_history: list[dict], is_first_message: bool, turn_index: int,
        question_set_id: Optional[str], client: str,
    ):
        """Crea el recolector. Si algo falla, se devuelve None y el agente
        corre exactamente igual, sin traza."""
        if isinstance(self.trace_sink, NullSink):
            return None
        try:
            versions = build_versions(
                self.settings, provider, model,
                calendar=getattr(self.temporal, "cal", None),
            )
            collector = TraceCollector(
                conversation_id=session_id,
                versions=versions,
                request=TraceRequest(
                    query=user_query,
                    query_sha256=sha256_short(user_query),
                    provider=provider,
                    model=model,
                    chat_history_len=len(chat_history or []),
                    is_first_message=is_first_message,
                    client=client,
                    question_set_id=question_set_id,
                ),
                turn_index=turn_index,
                run_id=getattr(self.settings, "run_id", None),
                full_text=getattr(self.settings, "tracing_full_text", False),
            )
            collector.pending_finish = {}
            collector.set_interpretation(interpret(user_query))
            if self.manifest_store is not None:
                store = self.manifest_store
                store.load_or_create(
                    versions,
                    label=getattr(self.settings, "run_label", "") or "",
                    question_set=getattr(self.settings, "question_set", "") or None,
                )
                collector.drift = collector.check_drift(store.frozen_versions)
            return collector
        except Exception as e:
            logger.error(f"No se pudo inicializar la traza: {e}")
            return None

    def _flush_trace(self, collector) -> None:
        """Cierra y escribe la traza. Nunca propaga excepciones."""
        if collector is None:
            return
        try:
            pending = getattr(collector, "pending_finish", None) or {}
            if not pending:
                # El generador se interrumpió antes del evento `done`:
                # cliente desconectado o excepción aguas arriba.
                pending = {"status": "error", "exhausted_tools": False,
                           "tokens_input": 0, "tokens_output": 0}
                collector.add_error("run", "turno interrumpido antes de `done`")
            trace = collector.finish(
                status=pending.get("status", "ok"),
                exhausted_tools=pending.get("exhausted_tools", False),
                tokens_input=pending.get("tokens_input", 0),
                tokens_output=pending.get("tokens_output", 0),
            )
            self.trace_sink.write(trace)
            if self.manifest_store is not None:
                self.manifest_store.record_trace(
                    trace.trace_id,
                    getattr(collector, "drift", []) or [],
                    model=trace.versions.model,
                )
        except Exception as e:
            logger.error(f"No se pudo cerrar la traza: {e}")

    # ── Constructores de mensajes ───────────────────────────

    def _build_messages(
        self, user_query: str, chat_history: list[dict],
        provider: str, session_id: str, collector=None,
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

        # El cache inyecta evidencia de turnos anteriores en el system prompt.
        # Sin registrarla, habría documentos en el contexto que no vienen del
        # retrieval de este turno y la respuesta parecería salir de la nada.
        if collector is not None:
            collector.set_context(
                system_prompt=system_content,
                messages_count=len(messages),
                cached_evidence_used=bool(used_cache and cache_context),
                cached_evidence_items=self._describe_cached_evidence(
                    cached_criterios, cached_expedientes
                ),
                docs_in_context=[],
                total_context_chars=len(system_content),
            )
        return messages

    def _describe_cached_evidence(
        self, cached_criterios: list, cached_expedientes: list,
    ) -> list[dict]:
        items = []
        for c in cached_criterios or []:
            meta = c.get("metadata", {}) if isinstance(c, dict) else {}
            items.append({
                "doc_id": str(c.get("id", "")) if isinstance(c, dict) else "",
                "case_link": meta.get("id_expediente", ""),
                "source_type": "criterio",
            })
        for e in cached_expedientes or []:
            items.append({
                "doc_id": str(e.get("id", "")) if isinstance(e, dict) else "",
                "case_link": e.get("id_expediente") or e.get("caseLink", ""),
                "source_type": "estadistica",
            })
        return items

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

    async def _exec_buscar_criterios(self, args: dict, collector=None, state=None) -> list:
        results = await self.criterios.search(
            query=args["query"],
            top_k=args.get("top_k", 15),
            collector=collector,
        )
        serialized = [
            {
                "id": r.id,
                # truncar para no explotar el contexto
                "text": r.text[:CRITERIO_CONTEXT_CHARS],
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
        ]

        # Etapa 3 — lo que realmente entra al prompt. Es aquí donde se pierde
        # texto respecto de lo que devolvió el buscador.
        if collector is not None:
            collector.record_stage(
                stage="in_context",
                method=f"truncate({CRITERIO_CONTEXT_CHARS})",
                docs=[
                    {**d, "text_len_in_context": len(d["text"])}
                    for d in serialized
                ],
                notes=(
                    f"El texto de cada criterio se recorta a "
                    f"{CRITERIO_CONTEXT_CHARS} caracteres antes de serializarse."
                ),
            )
            collector.add_docs_in_context([
                {
                    "doc_id": str(r.id),
                    "case_link": r.metadata.get("id_expediente", ""),
                    "source_type": "criterio",
                    "chars_in_context": min(len(r.text), CRITERIO_CONTEXT_CHARS),
                    "chars_full": len(r.text),
                    "truncated": len(r.text) > CRITERIO_CONTEXT_CHARS,
                }
                for r in results
            ])
        return serialized

    async def _exec_contar_expedientes(self, args: dict, collector=None, state=None) -> dict:
        """
        Devuelve el tamaño del universo sin traer los registros.

        Existe porque "¿cuántos?" no se responde con top-k: en el baseline el
        agente contestó un conteo comparativo con 1 registro de 2,793.
        """
        prefijo = args.get("prefijo_expediente")
        filters = self._build_expediente_filters(args)

        # La API une sus filtros con OR (verificado): pedir VCN + COFECE
        # devuelve 1,839 en vez de 36. Por eso, con prefijo se manda un solo
        # filtro y el resto se cuenta localmente sobre el universo real.
        if prefijo:
            registros = await self.estadistica.fetch_by_prefix(
                prefijo, collector=collector
            )
            locales = self._filtrar_local(
                [r.model_dump() for r in registros], args, state
            )
            return {
                "total": len(locales),
                "total_del_prefijo_sin_otros_filtros": len(registros),
                "exacto": True,
                "filtros_aplicados": {**filters, "prefijo_expediente": prefijo},
                "metodo": (
                    "Universo completo del prefijo traído por paginación y "
                    "filtrado localmente."
                ),
            }

        # Sin prefijo, meta.total solo es confiable con un filtro o ninguno.
        await self.estadistica.search(
            text_search=args.get("text_search"),
            filters=filters if filters else None,
            limit=1,
            collector=collector,
        )
        confiable = len(filters) + (1 if args.get("text_search") else 0) <= 1
        salida = {
            "total": self.estadistica.last_total,
            "exacto": confiable,
            "filtros_aplicados": filters,
        }
        if not confiable:
            salida["ADVERTENCIA"] = (
                "Este total NO es confiable: la API combina varios filtros con "
                "OR en vez de AND, así que el número está inflado. Vuelve a "
                "contar con un solo filtro, o usa prefijo_expediente."
            )
        return salida

    def _filtrar_local(self, registros: list[dict], args: dict, state=None) -> list[dict]:
        """
        Aplica localmente los filtros que la API no sabe intersectar.

        Dos cuidados aprendidos por las malas:

        1. El match NO puede ser exacto. El modelo escribe "NO ACREDITADO EL
           INCUMPLIMIENTO" y el valor real es "NO SE ACREDITÓ INCUMPLIMIENTO";
           con igualdad estricta el filtro daba cero y el agente concluía "no
           existe ningún caso", que es falso y peligroso.
        2. Si un filtro deja el conjunto vacío, hay que decirlo con los valores
           que sí existen, para que el agente distinga "no hay ninguno" de
           "tu filtro no coincidió".
        """
        filtro_vacio = None
        salida = registros
        equivalencias = {
            "autoridad": "authority",
            "tipo_procedimiento": "typeOfProcedure",
            "sentido_resolucion": "senseOfResolution",
        }
        for arg_key, campo in equivalencias.items():
            valor = args.get(arg_key)
            if not valor:
                continue
            antes = salida
            objetivo = _normalizar(valor)
            salida = [
                r for r in antes
                if _coincide(_normalizar(r.get(campo)), objetivo)
            ]
            if antes and not salida:
                disponibles = sorted({
                    str(r.get(campo)) for r in antes if r.get(campo)
                })
                filtro_vacio = {
                    "filtro": arg_key,
                    "valor_solicitado": valor,
                    "valores_disponibles": disponibles[:25],
                    "nota": (
                        f"Ningún expediente tiene {arg_key}='{valor}'. Esto NO "
                        f"significa que no existan casos: significa que ese valor "
                        f"no coincide con los que usa la base. Revisa la lista de "
                        f"valores disponibles y vuelve a intentar."
                    ),
                }
                break
        return salida

    def _build_expediente_filters(self, args: dict) -> dict:
        filter_keys = {
            "autoridad": "authority",
            "tipo_procedimiento": "typeOfProcedure",
            "sentido_resolucion": "senseOfResolution",
            "id_expediente": "caseLink",
            "fecha_resolucion_desde": "senseOfResolutionFrom",
            "fecha_resolucion_hasta": "senseOfResolutionTo",
        }
        return {
            api_key: args[agent_key]
            for agent_key, api_key in filter_keys.items()
            if args.get(agent_key) is not None
        }

    async def _exec_buscar_expedientes(self, args: dict, collector=None, state=None) -> list:
        text_search = args.get("text_search")
        limit = args.get("limit", 50)
        has_multas = args.get("has_multas", False)

        filters = self._build_expediente_filters(args)
        prefijo = args.get("prefijo_expediente")
        exhaustivo = args.get("exhaustivo", False)

        # Si piden multas, traer más resultados para filtrar después
        fetch_limit = limit * 3 if has_multas else limit

        if prefijo:
            # La API une filtros con OR, así que combinar prefijo con autoridad
            # o tipo devolvería MÁS resultados, no menos. Se trae el universo
            # completo del prefijo con un solo filtro y se acota localmente.
            registros = await self.estadistica.fetch_by_prefix(
                prefijo, max_results=500, collector=collector
            )
            serialized = self._filtrar_local(
                [r.model_dump() for r in registros], args, state
            )
            # Se recorrió el universo completo del prefijo: no hay que
            # advertir de cobertura parcial aunque los filtros locales
            # hayan reducido el conjunto.
            state.universo_completo = True
            state.universo_tamano = len(registros)
            if not exhaustivo:
                serialized = serialized[:fetch_limit]
                state.universo_completo = len(serialized) == len(registros)
        elif exhaustivo:
            results = await self.estadistica.search_all_pages(
                text_search=text_search,
                filters=filters if filters else None,
                max_results=500,
                collector=collector,
            )
            serialized = [r.model_dump() for r in results]
            state.universo_completo = False
        else:
            # text_search se envía como searchData (búsqueda libre cross-field
            # en caseLink, nombre, agentes económicos y mercados relevantes)
            results = await self.estadistica.search(
                text_search=text_search,
                filters=filters if filters else None,
                limit=fetch_limit,
                collector=collector,
            )
            serialized = [r.model_dump() for r in results]
            state.universo_completo = False

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

        # Guardado para usar_ultima_busqueda, que evita que el modelo tenga
        # que devolver el arreglo completo y truncar sus propios argumentos.
        state.last_expedientes = serialized

        if collector is not None:
            collector.record_stage(
                stage="in_context",
                method="post_filter(has_multas)" if has_multas else "serialize_full",
                docs=serialized,
                notes=(
                    "has_multas se filtra localmente: la API no tiene ese filtro."
                    if has_multas else None
                ),
            )
            collector.add_docs_in_context([
                {
                    "doc_id": str(r.get("id", "")),
                    "case_link": r.get("caseLink", ""),
                    "source_type": "estadistica",
                    "chars_in_context": len(json.dumps(r, default=str)),
                    "chars_full": len(json.dumps(r, default=str)),
                    "truncated": False,
                }
                for r in serialized
            ])

        return serialized

    async def _exec_calcular_plazos(self, args: dict, collector=None, state=None) -> dict:
        # ── Modo A: dos fechas sueltas ──────────────────────
        # Antes no existía: ante "¿cuántos días hábiles entre X e Y?" el modelo
        # no tenía herramienta que llamar y estimaba de memoria.
        f_ini = args.get("fecha_inicio_explicita")
        f_fin = args.get("fecha_fin_explicita")
        if f_ini and f_fin:
            return self._calcular_entre_fechas(
                f_ini, f_fin, args.get("institucion", "COFECE"), collector
            )

        # ── Modo B: expedientes ─────────────────────────────
        expedientes = args.get("expedientes") or []
        if args.get("usar_ultima_busqueda") or not expedientes:
            if state and state.last_expedientes:
                expedientes = state.last_expedientes
        if not expedientes:
            return {
                "error": "No hay expedientes sobre los que calcular.",
                "sugerencia": (
                    "Usa fecha_inicio_explicita y fecha_fin_explicita para dos "
                    "fechas sueltas, o llama primero a buscar_expedientes."
                ),
            }

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

        if collector is not None:
            collector.record_computation(
                self._describe_computation(
                    enriched, plazo_field, fecha_inicio, result.get("stats")
                )
            )

        return result

    def _calcular_entre_fechas(
        self, f_ini: str, f_fin: str, institucion: str, collector=None,
    ) -> dict:
        """Cómputo entre dos fechas sueltas, con el calendario oficial."""
        d_ini = self.temporal._parse_date(f_ini)
        d_fin = self.temporal._parse_date(f_fin)
        if not d_ini or not d_fin:
            return {"error": f"No pude interpretar las fechas: '{f_ini}', '{f_fin}'."}

        cal = self.temporal.cal
        habiles = cal.business_days_between(d_ini, d_fin, institucion)
        naturales = (d_fin - d_ini).days
        cubierto = cal.is_covered(d_ini, institucion) and cal.is_covered(d_fin, institucion)

        resultado = {
            "fecha_inicio": d_ini.isoformat(),
            "fecha_fin": d_fin.isoformat(),
            "dias_habiles": habiles,
            "dias_naturales": naturales,
            "institucion": institucion,
            "convencion": "excluye el día inicial, incluye el día final",
            "dentro_de_cobertura_del_calendario": cubierto,
        }
        if not cubierto:
            resultado["ADVERTENCIA"] = (
                f"Alguna de las fechas cae fuera del rango del catálogo de días "
                f"inhábiles para {institucion} "
                f"({cal.coverage_ranges().get(institucion.upper())}). "
                f"El conteo puede ser incorrecto: avísale al usuario."
            )

        if collector is not None:
            collector.record_computation({
                "tool_called": True,
                "modo": "fechas_explicitas",
                "convention": {
                    "excludes_start_day": True,
                    "includes_end_day": True,
                    "calendar": institucion,
                },
                "per_case": [{
                    "case_link": None,
                    "authority": institucion,
                    "date_start": d_ini.isoformat(),
                    "date_field_start": "explicita",
                    "date_end": d_fin.isoformat(),
                    "date_field_end": "explicita",
                    "business_days": habiles,
                    "calendar_days": naturales,
                    "out_of_coverage": not cubierto,
                    "coverage_note": resultado.get("ADVERTENCIA"),
                }],
                "stats": None,
            })
        return resultado

    def _describe_computation(
        self, enriched: list[dict], plazo_field: str,
        fecha_inicio: str, stats: dict | None,
    ) -> dict:
        """
        Audita el cómputo de plazos caso por caso.

        Registra dos cosas que hoy no se pueden ver desde fuera: la convención
        de conteo vigente, y si la fecha cayó fuera del rango del catálogo de
        días inhábiles para esa institución — en cuyo caso los fines de semana
        se cuentan como hábiles y el plazo no es confiable.
        """
        cal = getattr(self.temporal, "cal", None)
        start_field = (
            "admissionDate" if fecha_inicio == "fecha_admision" else "notificationDate"
        )

        per_case = []
        for rec in enriched:
            authority = rec.get("authority") or rec.get("autoridad")
            d_start = self.temporal._parse_date(
                rec.get(start_field) or rec.get(fecha_inicio)
            )
            d_end = self.temporal._parse_date(
                rec.get("resolutionDate") or rec.get("fecha_resolucion")
            )
            out_of_coverage = False
            note = None
            if cal is not None and d_start and d_end:
                covered = cal.is_covered(d_start, authority) and \
                    cal.is_covered(d_end, authority)
                if not covered:
                    out_of_coverage = True
                    ranges = cal.coverage_ranges().get(
                        (authority or "ALL").upper(), []
                    )
                    note = (
                        f"Fechas fuera del rango del catálogo para "
                        f"{authority or 'ALL'} ({ranges}); los fines de semana "
                        f"fuera de rango se contaron como hábiles."
                    )
            per_case.append({
                "case_link": rec.get("caseLink") or rec.get("id_expediente", ""),
                "authority": authority,
                "date_start": d_start.isoformat() if d_start else None,
                "date_field_start": start_field,
                "date_end": d_end.isoformat() if d_end else None,
                "date_field_end": "resolutionDate",
                "business_days": rec.get(plazo_field),
                "calendar_days": rec.get("dias_calendario_notif_resol"),
                "out_of_coverage": out_of_coverage,
                "coverage_note": note,
            })

        return {
            "tool_called": True,
            "convention": {
                # Regla general confirmada por COFECE: excluir el día inicial,
                # incluir el final (temporal/holidays.py: business_days_between).
                "excludes_start_day": True,
                "includes_end_day": True,
                "calendar": "por institución del expediente (authority)",
                "plazo_field": plazo_field,
            },
            "per_case": per_case[:200],
            "stats": stats,
        }

    # ── Helpers ─────────────────────────────────────────────

    def _serialize_tool_result(self, tool_name: str, result, state=None) -> dict | list:
        """
        Serializa resultado de tool para el LLM.

        Cada documento sale con su marcador de cita ya asignado (`ref`). El
        modelo cita ese marcador tal cual, así que resolver una cita después
        es una búsqueda en un diccionario y no una inferencia por posición.
        Antes se adivinaba, y con dos búsquedas en un turno la cita podía
        acabar apuntando a otro expediente.
        """
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            if state is not None and tool_name in (
                "buscar_criterios", "buscar_expedientes"
            ):
                kind = "C" if tool_name == "buscar_criterios" else "E"
                result = [
                    {"ref": state.registry.assign(doc, kind), **doc}
                    for doc in result if isinstance(doc, dict)
                ]
            payload = {"results": result, "count": len(result)}
            # El modelo no tenía forma de saber que su búsqueda estaba
            # truncada: recibía 50 de 1,796 sin enterarse y respondía con
            # falsa certeza. Ahora se le dice explícitamente.
            if tool_name == "buscar_expedientes":
                # Un filtro que no coincidió con nada NO significa "no existen
                # casos". Sin este aviso el agente concluye que no hay ninguno.
                vacio = state.filtro_vacio if state else None
                if vacio:
                    payload["FILTRO_SIN_COINCIDENCIAS"] = vacio
                total = getattr(self.estadistica, "last_total", None)
                if state and state.universo_completo:
                    payload["universo_completo_revisado"] = True
                    payload["total_del_universo"] = state.universo_tamano
                elif total is not None:
                    payload["total_en_la_base"] = total
                    if len(result) < total:
                        payload["ADVERTENCIA_COBERTURA"] = (
                            f"Solo estás viendo {len(result)} de {total} expedientes "
                            f"que cumplen estos filtros. NO puedes afirmar 'todos', "
                            f"'el mayor', 'el menor' ni un conteo con esta muestra. "
                            f"Vuelve a llamar con exhaustivo=true, o dile al usuario "
                            f"explícitamente sobre cuántos expedientes te basaste."
                        )
            return payload
        return {"result": str(result)}

    def _prepare_messages_for_stream(
        self, messages: list[dict], provider: str, collector=None,
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
        # Esta ruta condensa cada resultado de herramienta a ~200-500 chars:
        # el modelo redacta la respuesta final sin ver la evidencia completa
        # que acababa de recuperar. Se marca en la traza para poder medir su
        # efecto sobre la calidad de las respuestas.
        if collector is not None and any(
            isinstance(m.get("content"), list) or m.get("role") == "tool"
            or (m.get("content") is None and m.get("tool_calls"))
            for m in messages
        ):
            collector.set_decision("context_condensed", True, "derived")

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
        elif name == "contar_expedientes":
            prefijo = args.get("prefijo_expediente")
            return f"Contando expedientes{f' {prefijo}' if prefijo else ''}..."
        elif name == "calcular_plazos":
            if args.get("fecha_inicio_explicita"):
                return (
                    f"Calculando días hábiles entre "
                    f"{args['fecha_inicio_explicita']} y "
                    f"{args.get('fecha_fin_explicita', '?')}..."
                )
            n = len(args.get("expedientes") or [])
            return f"Calculando plazos en días hábiles para {n} expedientes..."
        return f"Ejecutando {name}"

    def _describe_expedientes_search(self, args: dict) -> str:
        parts = ["Buscando expedientes"]
        if args.get("prefijo_expediente"):
            parts.append(args["prefijo_expediente"])
        if args.get("exhaustivo"):
            parts.append("(universo completo)")
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


def _normalizar(valor) -> str:
    """Minúsculas, sin acentos y sin puntuación, para comparar etiquetas."""
    import unicodedata
    s = str(valor or "").strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(s.replace("/", " ").replace(",", " ").split())


def _coincide(valor: str, objetivo: str) -> bool:
    """
    Coincidencia tolerante: igualdad, contención, o que compartan las
    palabras significativas. El modelo no reproduce las etiquetas literales.
    """
    if not valor or not objetivo:
        return False
    if valor == objetivo or objetivo in valor or valor in objetivo:
        return True
    # La negación decide el sentido de la resolución y NO puede tratarse como
    # palabra vacía: "NO SE ACREDITÓ INCUMPLIMIENTO" y "SANCIÓN/ACREDITACIÓN
    # DEL INCUMPLIMIENTO" comparten casi todas las palabras y significan lo
    # contrario. Si una lado niega y el otro no, no coinciden.
    NEGACIONES = {"no", "sin", "ningun", "ninguna", "improcedente", "niega"}
    niega = lambda t: bool(NEGACIONES & set(t.split()))
    if niega(valor) != niega(objetivo):
        return False

    # Se compara por raíz de 6 caracteres: el modelo escribe "acreditado"
    # donde la base dice "acreditó", y palabra completa no las une.
    vacias = {"de", "del", "la", "el", "los", "las", "en", "se", "al", "y"}
    raices = lambda t: {
        p[:6] for p in t.split() if p not in vacias and len(p) > 2
    }
    pv, po = raices(valor), raices(objetivo)
    if not pv or not po:
        return False
    return len(pv & po) / len(po) >= 0.6
