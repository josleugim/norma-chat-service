"""
Modelos de la traza estructurada del agente Norma+.

Spec: NormaChat-Doc-Obs/docs/schema-trazas-v1.md

Convención central: cada dato interpretativo lleva `provenance`, que distingue
lo que el agente decidió de lo que nosotros inferimos.

  agent      → el agente lo decidió (p.ej. argumentos que pasó a una tool)
  derived    → deducido determinísticamente del flujo real
  heuristic  → inferido de la pregunta con reglas nuestras
  annotated  → anotado después, offline (LLM juez o persona)
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Provenance = Literal["agent", "derived", "heuristic", "annotated"]
StageName = Literal["candidates", "after_ranking", "in_context"]


class Versions(BaseModel):
    """Todo lo necesario para reproducir una corrida."""
    # `model_params` colisiona con el namespace protegido `model_` de pydantic v2.
    model_config = {"protected_namespaces": ()}

    agent_git_sha: str = "unknown"
    agent_semver: str = "1.0.0"
    prompt_id: str = "agent_system"
    prompt_semver: str = "v1"
    prompt_sha256: str = ""
    tools_sha256: str = ""
    provider: str = ""
    model: str = ""
    model_params: dict[str, Any] = Field(default_factory=dict)
    retrieval_defaults: dict[str, Any] = Field(default_factory=dict)
    search_api_base_url: str = ""
    # Pendientes del lado de la API de datos — ver docs/solicitud-jose-miguel.md
    index_version: Optional[str] = None
    index_snapshot_at: Optional[str] = None
    embeddings_model: Optional[str] = None
    holidays_sha256: Optional[str] = None
    holidays_coverage: dict[str, list[str]] = Field(default_factory=dict)
    unknown: list[str] = Field(default_factory=list)

    def fingerprint(self) -> dict[str, Any]:
        """
        Subconjunto que define el entorno de una corrida.

        `provider` y `model` quedan FUERA a propósito: varían por petición, así
        que incluirlos marcaría drift en cada traza. Se registran igual en cada
        traza, y el manifiesto acumula los modelos vistos en `models_observed`
        — que es donde se nota si una corrida mezcló modelos.
        """
        return {
            "agent_git_sha": self.agent_git_sha,
            "agent_semver": self.agent_semver,
            "prompt_sha256": self.prompt_sha256,
            "tools_sha256": self.tools_sha256,
            "model_params": self.model_params,
            "retrieval_defaults": self.retrieval_defaults,
            "search_api_base_url": self.search_api_base_url,
            "index_version": self.index_version,
            "embeddings_model": self.embeddings_model,
            "holidays_sha256": self.holidays_sha256,
        }


class Request(BaseModel):
    query: str = ""
    query_sha256: str = ""
    provider: str = ""
    model: str = ""
    chat_history_len: int = 0
    is_first_message: bool = False
    client: str = "frontend"
    question_set_id: Optional[str] = None


class Scope(BaseModel):
    procedure_prefix: list[str] = Field(default_factory=list)
    procedure_type: Optional[str] = None
    authority: Optional[str] = None
    case_links: list[str] = Field(default_factory=list)
    economic_agents: list[str] = Field(default_factory=list)
    relevant_markets: list[str] = Field(default_factory=list)
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class Constraints(BaseModel):
    exhaustive: bool = False
    superlative: Optional[str] = None
    requires_computation: bool = False
    comparative: bool = False


class Interpretation(BaseModel):
    intent: Optional[str] = None
    scope: Scope = Field(default_factory=Scope)
    constraints: Constraints = Field(default_factory=Constraints)
    provenance: dict[str, Provenance] = Field(default_factory=dict)


class Decisions(BaseModel):
    scope: Optional[str] = None
    requires_exhaustive_search: bool = False
    tool_deadline_calculator: bool = False
    second_retrieval_triggered: bool = False
    tools_used: list[str] = Field(default_factory=list)
    tool_call_count: int = 0
    max_tool_calls_reached: bool = False
    used_cached_evidence: bool = False
    answered_without_retrieval: bool = False
    final_answer_path: Optional[str] = None
    context_condensed: bool = False
    baseline_drift: bool = False
    provenance: dict[str, Provenance] = Field(default_factory=dict)


class RetrievalDoc(BaseModel):
    rank: int = 0
    doc_id: str = ""
    case_link: Optional[str] = None
    score: Optional[float] = None
    distance: Optional[float] = None
    text_preview: str = ""
    text_sha256: str = ""
    text_len_full: int = 0
    text_len_in_context: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalStage(BaseModel):
    stage: StageName
    method: str = ""
    count: int = 0
    docs: list[RetrievalDoc] = Field(default_factory=list)
    dropped_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class Coverage(BaseModel):
    """Exhaustividad: ¿la respuesta se construyó sobre todo el universo?"""
    total_available: Optional[int] = None
    requested_limit: int = 0
    returned: int = 0
    pages_fetched: int = 1
    truncated: bool = False
    truncation_reason: Optional[str] = None


class Step(BaseModel):
    index: int
    kind: Literal["tool_call", "llm_call"]
    tool: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    http_request: Optional[dict[str, Any]] = None
    started_at: datetime
    duration_ms: int = 0
    status: Literal["ok", "error"] = "ok"
    error: Optional[str] = None
    stages: list[RetrievalStage] = Field(default_factory=list)
    coverage: Optional[Coverage] = None
    computation: Optional[dict[str, Any]] = None
    tokens: Optional[dict[str, int]] = None


class ContextBlock(BaseModel):
    system_prompt_sha256: str = ""
    cached_evidence_used: bool = False
    cached_evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    messages_count: int = 0
    docs_in_context: list[dict[str, Any]] = Field(default_factory=list)
    total_context_chars: int = 0
    estimated_input_tokens: int = 0


class Answer(BaseModel):
    text: str = ""
    length_chars: int = 0
    citations_emitted: list[str] = Field(default_factory=list)
    citations_resolved: list[dict[str, Any]] = Field(default_factory=list)
    citations_unresolved: list[str] = Field(default_factory=list)
    docs_in_context_uncited: list[str] = Field(default_factory=list)
    has_fuentes_section: bool = False
    format_markers: dict[str, int] = Field(default_factory=dict)
    case_links_mentioned: list[str] = Field(default_factory=list)
    scope_mismatch: bool = False


class Outcome(BaseModel):
    status: Literal["ok", "error", "timeout"] = "ok"
    exhausted_tools: bool = False
    duration_ms: dict[str, int] = Field(default_factory=dict)
    tokens: dict[str, int] = Field(default_factory=dict)
    cost_usd_estimate: float = 0.0
    sse_events_emitted: dict[str, int] = Field(default_factory=dict)


class Trace(BaseModel):
    schema_version: str = SCHEMA_VERSION
    trace_id: str
    conversation_id: str
    turn_index: int = 0
    run_id: Optional[str] = None
    timestamp_utc: datetime

    versions: Versions = Field(default_factory=Versions)
    request: Request = Field(default_factory=Request)
    interpretation: Interpretation = Field(default_factory=Interpretation)
    decisions: Decisions = Field(default_factory=Decisions)
    steps: list[Step] = Field(default_factory=list)
    context: ContextBlock = Field(default_factory=ContextBlock)
    answer: Answer = Field(default_factory=Answer)
    outcome: Outcome = Field(default_factory=Outcome)
    errors: list[dict[str, Any]] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """
        Fila plana para run.jsonl y para el export a XLSX.
        Las columnas son las que se miran primero al analizar una corrida.
        """
        retrieval_docs = sum(
            s.coverage.returned for s in self.steps if s.coverage is not None
        )
        truncated = any(
            s.coverage.truncated for s in self.steps if s.coverage is not None
        )
        plazo_cases = [
            case
            for s in self.steps
            if s.computation
            for case in s.computation.get("per_case", [])
        ]
        out_of_coverage = any(c.get("out_of_coverage", False) for c in plazo_cases)
        # Expedientes cuyo plazo no se pudo calcular porque la API no trae la
        # fecha de inicio. Distingue "el agente calculó mal" de "no había con
        # qué calcular", que son problemas de dueños distintos.
        plazo_inputs_missing = sum(
            1 for c in plazo_cases
            if c.get("date_start") is None or c.get("date_end") is None
        )
        return {
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "turn_index": self.turn_index,
            "run_id": self.run_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "question_set_id": self.request.question_set_id,
            "query": self.request.query,
            "query_sha256": self.request.query_sha256,
            "provider": self.request.provider,
            "model": self.request.model,
            "tools_used": ",".join(self.decisions.tools_used),
            "tool_call_count": self.decisions.tool_call_count,
            "docs_retrieved": retrieval_docs,
            "coverage_truncated": truncated,
            "deadline_tool_called": self.decisions.tool_deadline_calculator,
            "plazo_out_of_coverage": out_of_coverage,
            "plazo_cases": len(plazo_cases),
            "plazo_inputs_missing": plazo_inputs_missing,
            "second_retrieval": self.decisions.second_retrieval_triggered,
            "exhausted_tools": self.outcome.exhausted_tools,
            "context_condensed": self.decisions.context_condensed,
            "final_answer_path": self.decisions.final_answer_path,
            "used_cached_evidence": self.decisions.used_cached_evidence,
            "scope_expected": ",".join(self.interpretation.scope.procedure_prefix),
            "scope_mismatch": self.answer.scope_mismatch,
            "citations_emitted": len(self.answer.citations_emitted),
            "citations_unresolved": ",".join(self.answer.citations_unresolved),
            "answer_chars": self.answer.length_chars,
            "baseline_drift": self.decisions.baseline_drift,
            "status": self.outcome.status,
            "duration_ms": self.outcome.duration_ms.get("total", 0),
            "tokens_input": self.outcome.tokens.get("input", 0),
            "tokens_output": self.outcome.tokens.get("output", 0),
            "cost_usd_estimate": self.outcome.cost_usd_estimate,
            "errors": len(self.errors),
        }


class RunManifest(BaseModel):
    """
    Congela el baseline de una corrida. Cada traza se compara contra
    `frozen_versions`; cualquier diferencia marca baseline_drift.
    """
    run_id: str
    label: str = ""
    started_at: datetime
    finished_at: Optional[datetime] = None
    question_set: Optional[str] = None
    question_count: int = 0
    trace_count: int = 0
    frozen_versions: dict[str, Any] = Field(default_factory=dict)
    drift_detected: list[dict[str, Any]] = Field(default_factory=list)
    # Modelos usados en la corrida. Más de uno significa que la comparación
    # entre preguntas no es limpia, aunque el entorno no haya cambiado.
    models_observed: list[str] = Field(default_factory=list)
