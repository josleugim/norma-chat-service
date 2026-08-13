"""
Norma+ Chat Agent Service — FastAPI entry point.

Inicializa todos los componentes e inyecta dependencias.
"""
import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from llm import LLMRegistry, OpenAIAdapter, AnthropicAdapter
from retrieval import CriteriosSearchClient, EstadisticaSearchClient
from temporal import HolidayCalendar, TemporalAnalyzer
from core import CitationBuilder, EvidenceCache
from core.tracing import RunManifestStore, build_sink
from core.tracing.versioning import build_versions
from agent import NormaPlusAgent
from routers.chat import router as chat_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización al arrancar, cleanup al parar."""
    settings = get_settings()

    # ── LLM Registry ────────────────────────────────────────
    registry = LLMRegistry()

    if settings.openai_api_key:
        registry.register("openai", OpenAIAdapter(api_key=settings.openai_api_key))
        logger.info("OpenAI adapter registrado")

    if settings.anthropic_api_key:
        registry.register("anthropic", AnthropicAdapter(api_key=settings.anthropic_api_key))
        logger.info("Anthropic adapter registrado")

    if not registry.providers:
        logger.warning("No hay adaptadores LLM registrados. Configura API keys en .env")

    # ── Retrieval Clients ───────────────────────────────────
    criterios_client = CriteriosSearchClient(
        base_url=settings.search_api_base_url,
        api_key=settings.search_api_key,
        timeout=settings.search_api_timeout,
    )
    estadistica_client = EstadisticaSearchClient(
        base_url=settings.search_api_base_url,
        api_key=settings.search_api_key,
        timeout=settings.search_api_timeout,
    )
    logger.info(f"Clientes de búsqueda configurados → {settings.search_api_base_url}")

    # ── Temporal ────────────────────────────────────────────
    calendar = HolidayCalendar(holidays_path=settings.holidays_path)
    temporal_analyzer = TemporalAnalyzer(calendar=calendar)

    # ── Citation Builder ────────────────────────────────────
    citation_builder = CitationBuilder()

    # ── Evidence Cache ───────────────────────────────────────
    evidence_cache = EvidenceCache(
        max_turns=15,
        prev_turn_top_k=5,
        max_cached_criterios=10,
        max_cached_expedientes=15,
    )
    logger.info("Evidence cache inicializado")

    # ── Trazabilidad ────────────────────────────────────────
    trace_sink = build_sink(settings)
    manifest_store = None
    if settings.tracing_enabled:
        manifest_store = RunManifestStore(settings.traces_dir, settings.run_id)
        versions = build_versions(settings, "", "", calendar=calendar)
        manifest_store.load_or_create(
            versions,
            label=settings.run_label or settings.run_id,
            question_set=settings.question_set or None,
        )
        logger.info(
            f"Corrida '{settings.run_id}' — prompt {versions.prompt_sha256}, "
            f"tools {versions.tools_sha256}, agente {versions.agent_git_sha}"
        )
        if versions.unknown:
            logger.warning(
                "Reproducibilidad parcial: sin "
                f"{', '.join(versions.unknown)}. Ver docs/solicitud-jose-miguel.md"
            )

    # ── Agent ───────────────────────────────────────────────
    agent = NormaPlusAgent(
        llm_registry=registry,
        criterios_client=criterios_client,
        estadistica_client=estadistica_client,
        temporal_analyzer=temporal_analyzer,
        citation_builder=citation_builder,
        evidence_cache=evidence_cache,
        max_tool_calls=settings.agent_max_tool_calls,
        trace_sink=trace_sink,
        manifest_store=manifest_store,
        settings=settings,
    )

    app.state.agent = agent
    app.state.settings = settings

    logger.info("Chat Agent Service inicializado correctamente")
    yield
    if manifest_store is not None:
        manifest_store.finish()
    trace_sink.close()
    logger.info("Chat Agent Service detenido")


# ── App ─────────────────────────────────────────────────────

app = FastAPI(
    title="Norma+ Chat Agent Service",
    description="Agente de competencia económica con tool-calling",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(chat_router, prefix="/api")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "providers": app.state.agent.llm_registry.providers if hasattr(app.state, "agent") else [],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
