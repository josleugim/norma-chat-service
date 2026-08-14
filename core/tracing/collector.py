"""
Recolector de trazas: acumula lo que ocurre durante un turno y produce un Trace.

Reglas de oro de este módulo:

1. **Nunca cambia el comportamiento del agente.** Solo observa y anota.
2. **Nunca revienta una respuesta.** Todo lo que pueda fallar va en try/except;
   una traza perdida es un problema menor, una respuesta perdida no.
3. **Distingue decisión de inferencia.** Cada campo interpretativo se registra
   con su provenance.
"""
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from core.tracing.schema import (
    Answer, ContextBlock, Coverage, Decisions, Interpretation, Outcome,
    Request, RetrievalDoc, RetrievalStage, Step, Trace, Versions,
)
from core.tracing.heuristics import expected_tools
from core.tracing.versioning import diff_fingerprints, sha256_full, sha256_short

logger = logging.getLogger(__name__)

TEXT_PREVIEW_CHARS = 2000
CHARS_PER_TOKEN = 4  # aproximación para estimated_input_tokens


def new_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:16]}"


class TraceCollector:

    def __init__(
        self,
        conversation_id: str,
        versions: Versions,
        request: Request,
        turn_index: int = 0,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        full_text: bool = False,
    ):
        self.trace_id = trace_id or new_trace_id()
        self.conversation_id = conversation_id
        self.turn_index = turn_index
        self.run_id = run_id
        self.versions = versions
        self.request = request
        self.full_text = full_text

        self.interpretation = Interpretation()
        self.decisions = Decisions()
        self.context = ContextBlock()
        self.answer = Answer()
        self.outcome = Outcome()
        self.steps: list[Step] = []
        self.errors: list[dict[str, Any]] = []

        self._t0 = time.perf_counter()
        self._started_at = datetime.now(timezone.utc)
        self._current: Optional[Step] = None
        self._current_t0: float = 0.0
        self._retrieval_ms = 0
        self._llm_ms = 0
        self._computation_ms = 0
        self._sse_counts: dict[str, int] = {}

    # ── Pasos ───────────────────────────────────────────────

    def begin_step(
        self,
        kind: str,
        tool: Optional[str] = None,
        arguments: Optional[dict] = None,
    ) -> Step:
        step = Step(
            index=len(self.steps) + 1,
            kind=kind,
            tool=tool,
            arguments=_safe_arguments(arguments),
            started_at=datetime.now(timezone.utc),
        )
        self.steps.append(step)
        self._current = step
        self._current_t0 = time.perf_counter()
        return step

    def end_step(
        self,
        status: str = "ok",
        error: Optional[str] = None,
        tokens: Optional[dict] = None,
    ) -> None:
        if self._current is None:
            return
        elapsed = int((time.perf_counter() - self._current_t0) * 1000)
        self._current.duration_ms = elapsed
        self._current.status = status if status in ("ok", "error") else "error"
        self._current.error = error
        if tokens:
            self._current.tokens = tokens

        if self._current.kind == "llm_call":
            self._llm_ms += elapsed
        elif self._current.tool == "calcular_plazos":
            self._computation_ms += elapsed
        else:
            self._retrieval_ms += elapsed

        self._current = None

    @property
    def current_step(self) -> Optional[Step]:
        return self._current

    # ── Retrieval (lo llaman los clientes HTTP) ─────────────

    def record_http_request(self, method: str, url: str, **kwargs) -> None:
        if self._current is None:
            return
        self._current.http_request = {"method": method, "url": url, **kwargs}

    def record_stage(
        self,
        stage: str,
        method: str,
        docs: list[dict],
        dropped_ids: Optional[list[str]] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Registra una etapa de retrieval en el paso actual."""
        if self._current is None:
            return
        try:
            self._current.stages.append(RetrievalStage(
                stage=stage,
                method=method,
                count=len(docs),
                docs=[_to_retrieval_doc(d, i + 1, self.full_text)
                      for i, d in enumerate(docs)],
                dropped_ids=dropped_ids or [],
                notes=notes,
            ))
        except Exception as e:
            logger.warning(f"No se pudo registrar la etapa {stage}: {e}")

    def record_coverage(
        self,
        total_available: Optional[int],
        requested_limit: int,
        returned: int,
        pages_fetched: int = 1,
        truncation_reason: Optional[str] = None,
        truncated: Optional[bool] = None,
    ) -> None:
        """
        Exhaustividad. `total_available` viene de meta.total de /cases/search:
        es lo que permite marcar sola una respuesta construida sobre 50 de 187
        expedientes.

        En criterios no hay un total conocido, así que el llamador pasa
        `truncated` explícito cuando la búsqueda topó con el techo de top_k.
        """
        if self._current is None:
            return
        if truncated is None:
            truncated = (
                total_available is not None and returned < total_available
            )
        self._current.coverage = Coverage(
            total_available=total_available,
            requested_limit=requested_limit,
            returned=returned,
            pages_fetched=pages_fetched,
            truncated=truncated,
            truncation_reason=(truncation_reason or "limit") if truncated else None,
        )

    def record_computation(self, computation: dict) -> None:
        if self._current is None:
            return
        self._current.computation = computation

    def record_result(self, result) -> None:
        """
        Resumen de lo que devolvió la herramienta. El detalle está en las
        etapas de retrieval; esto permite leer un paso de un vistazo y cubre
        el "resultado devuelto" que pidió COFECE.
        """
        if self._current is None:
            return
        try:
            if isinstance(result, list):
                self._current.result_summary = {
                    "type": "list",
                    "count": len(result),
                    "sample_ids": [
                        str(r.get("caseLink") or r.get("id", ""))
                        for r in result[:5] if isinstance(r, dict)
                    ],
                }
            elif isinstance(result, dict):
                self._current.result_summary = {
                    "type": "dict",
                    "keys": sorted(result.keys())[:15],
                    "total_expedientes": result.get("total_expedientes"),
                    "filtered_count": result.get("filtered_count"),
                    "stats": result.get("stats"),
                    "error": result.get("error"),
                }
            else:
                self._current.result_summary = {"type": type(result).__name__}
        except Exception as e:
            logger.warning(f"No se pudo resumir el resultado de la tool: {e}")

    # ── Contexto, decisiones, respuesta ─────────────────────

    def set_interpretation(self, interpretation: Interpretation) -> None:
        self.interpretation = interpretation

    def set_decision(self, field: str, value: Any, provenance: str = "derived") -> None:
        if hasattr(self.decisions, field):
            setattr(self.decisions, field, value)
            self.decisions.provenance[field] = provenance

    def set_context(
        self,
        system_prompt: str,
        messages_count: int,
        cached_evidence_used: bool,
        cached_evidence_items: list[dict],
        docs_in_context: list[dict],
        total_context_chars: int,
    ) -> None:
        self.context = ContextBlock(
            system_prompt_sha256=sha256_short(system_prompt or ""),
            cached_evidence_used=cached_evidence_used,
            cached_evidence_items=cached_evidence_items,
            messages_count=messages_count,
            docs_in_context=docs_in_context,
            total_context_chars=total_context_chars,
            estimated_input_tokens=total_context_chars // CHARS_PER_TOKEN,
        )

    def add_docs_in_context(self, docs: list[dict]) -> None:
        """Acumula lo que realmente entró al prompt, tras truncados."""
        self.context.docs_in_context.extend(docs)
        self.context.total_context_chars += sum(
            d.get("chars_in_context", 0) for d in docs
        )
        self.context.estimated_input_tokens = (
            self.context.total_context_chars // CHARS_PER_TOKEN
        )

    def set_answer(self, answer: Answer) -> None:
        self.answer = answer

    def count_sse_event(self, event_type: str) -> None:
        self._sse_counts[event_type] = self._sse_counts.get(event_type, 0) + 1

    def add_error(self, where: str, message: str) -> None:
        self.errors.append({
            "where": where,
            "message": str(message)[:1000],
            "at": datetime.now(timezone.utc).isoformat(),
        })

    # ── Baseline ────────────────────────────────────────────

    def check_drift(self, frozen_versions: Optional[dict]) -> list[dict]:
        """Compara contra el manifiesto congelado de la corrida."""
        if not frozen_versions:
            return []
        drift = diff_fingerprints(frozen_versions, self.versions.fingerprint())
        if drift:
            self.set_decision("baseline_drift", True, "derived")
            logger.warning(
                f"baseline_drift en {self.trace_id}: "
                f"{[d['field'] for d in drift]}"
            )
        return drift

    # ── Cierre ──────────────────────────────────────────────

    def finish(
        self,
        status: str = "ok",
        exhausted_tools: bool = False,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost_usd: float = 0.0,
    ) -> Trace:
        total_ms = int((time.perf_counter() - self._t0) * 1000)

        # Decisiones derivadas del flujo real
        tool_steps = [s for s in self.steps if s.kind == "tool_call"]
        tools_used = []
        for s in tool_steps:
            if s.tool and s.tool not in tools_used:
                tools_used.append(s.tool)
        retrieval_tools = [s.tool for s in tool_steps
                           if s.tool in ("buscar_criterios", "buscar_expedientes")]

        self.set_decision("tools_used", tools_used, "derived")
        self.set_decision("tool_call_count", len(tool_steps), "derived")
        self.set_decision(
            "tool_deadline_calculator",
            any(s.tool == "calcular_plazos" for s in tool_steps),
            "derived",
        )
        self.set_decision(
            "second_retrieval_triggered", len(retrieval_tools) > 1, "derived"
        )
        self.set_decision(
            "answered_without_retrieval", len(retrieval_tools) == 0, "derived"
        )
        self.set_decision("max_tool_calls_reached", exhausted_tools, "derived")

        # Herramientas que la consulta pedía y no se llamaron.
        esperadas = expected_tools(self.request.query)
        self.set_decision("tools_expected", esperadas, "heuristic")
        self.set_decision(
            "tools_expected_not_called",
            [t for t in esperadas if t not in tools_used],
            "heuristic",
        )

        # Estrategia de cobertura del universo.
        coberturas = [s.coverage for s in self.steps if s.coverage]
        truncada = any(c.truncated for c in coberturas)
        if not coberturas:
            estrategia = "sin_busqueda"
        elif truncada:
            estrategia = "una_pagina_truncada"
        elif len(retrieval_tools) > 1:
            estrategia = "multiples_busquedas"
        else:
            estrategia = "una_busqueda_completa"
        self.set_decision("coverage_strategy", estrategia, "derived")
        self.set_decision(
            "exhaustive_but_truncated",
            bool(self.interpretation.constraints.exhaustive and truncada),
            "derived",
        )

        # Espejo de la interpretación heurística, para tener el scope a mano
        if self.interpretation.scope.procedure_prefix:
            self.set_decision(
                "scope",
                ",".join(self.interpretation.scope.procedure_prefix),
                "heuristic",
            )
        self.set_decision(
            "requires_exhaustive_search",
            self.interpretation.constraints.exhaustive,
            "heuristic",
        )

        self.outcome = Outcome(
            status=status if status in ("ok", "error", "timeout") else "error",
            exhausted_tools=exhausted_tools,
            duration_ms={
                "total": total_ms,
                "retrieval": self._retrieval_ms,
                "llm": self._llm_ms,
                "computation": self._computation_ms,
            },
            tokens={"input": tokens_input, "output": tokens_output},
            cost_usd_estimate=cost_usd,
            sse_events_emitted=dict(self._sse_counts),
        )

        return Trace(
            trace_id=self.trace_id,
            conversation_id=self.conversation_id,
            turn_index=self.turn_index,
            run_id=self.run_id,
            timestamp_utc=self._started_at,
            versions=self.versions,
            request=self.request,
            interpretation=self.interpretation,
            decisions=self.decisions,
            steps=self.steps,
            context=self.context,
            answer=self.answer,
            outcome=self.outcome,
            errors=self.errors,
        )


# ── Helpers ─────────────────────────────────────────────────

def _to_retrieval_doc(raw: dict, rank: int, full_text: bool) -> RetrievalDoc:
    """Normaliza un resultado de cualquiera de las dos APIs a RetrievalDoc."""
    text = raw.get("text") or raw.get("content") or ""
    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    case_link = (
        raw.get("caseLink")
        or raw.get("case_link")
        or metadata.get("caseLink")
        or metadata.get("id_expediente")
        or None
    )
    distance = raw.get("distance")
    if distance is None:
        distance = metadata.get("distance")

    preview = text if full_text else text[:TEXT_PREVIEW_CHARS]

    return RetrievalDoc(
        rank=rank,
        doc_id=str(raw.get("id", "") or raw.get("doc_id", "")),
        case_link=case_link,
        score=raw.get("score"),
        distance=distance,
        text_preview=preview,
        text_sha256=sha256_full(text) if text else "",
        text_len_full=len(text),
        text_len_in_context=raw.get("text_len_in_context"),
        metadata=_compact_metadata(raw, metadata),
    )


CRITERIO_KEYS = (
    "anchor", "pdf_pages", "resolution_pages", "articleNames", "titleNames",
    "title", "article", "nombre_expediente", "caseName", "paginas_parrafos",
    "grounding",
)
EXPEDIENTE_KEYS = (
    "name", "authority", "typeOfProcedure", "senseOfResolution",
    "notificationDate", "admissionDate", "resolutionDate",
    "economicAgents", "relevantMarkets", "agentFines", "resolutionFileUrl",
)


def _compact_metadata(raw: dict, metadata: dict) -> dict:
    """
    Conserva lo útil para auditar sin arrastrar el registro completo.

    En criterios, la API deja algunos campos (caseName, articleNames,
    titleNames) al nivel superior y otros dentro de `metadata`, así que hay
    que mirar en ambos. En expedientes no hay `metadata`: se guardan los
    campos procesales, que son los que alimentan el cómputo de plazos.
    """
    if metadata:
        out = {k: metadata[k] for k in CRITERIO_KEYS if k in metadata}
        out.update({k: raw[k] for k in CRITERIO_KEYS if k in raw})
        return out
    return {k: raw[k] for k in EXPEDIENTE_KEYS if k in raw}


def _safe_arguments(arguments: Optional[dict]) -> Optional[dict]:
    """
    Los argumentos son provenance=agent y hay que conservarlos tal cual,
    salvo `expedientes`, que puede traer 50 objetos completos y ya quedan
    registrados en las etapas de retrieval del paso anterior.
    """
    if not arguments:
        return arguments
    out = dict(arguments)
    payload = out.get("expedientes")
    if isinstance(payload, list):
        out["expedientes"] = f"[{len(payload)} objetos]"
    return out
