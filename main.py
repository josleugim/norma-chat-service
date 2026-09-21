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
from core.tracing.census import take_census
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

    # ── Universo consultable ────────────────────────────────
    # Paso 01 del protocolo de holdout: el alcance es configuración, no una
    # instrucción añadida a cada pregunta. Se engancha al cliente antes del
    # censo para que la foto del acervo mida el universo real del agente y no
    # otro más grande.
    if settings.universo_path:
        from core.universo import UniversoRestringido
        universo = UniversoRestringido.desde_archivo(settings.universo_path)
        estadistica_client.universo = universo
        logger.info(
            f"UNIVERSO RESTRINGIDO a {len(universo)} expedientes "
            f"({universo.etiqueta}). Las consultas estructuradas y las "
            f"agregaciones no pueden salir de esa lista."
        )

    # ── Trazabilidad ────────────────────────────────────────
    trace_sink = build_sink(settings)
    manifest_store = None
    if settings.tracing_enabled:
        manifest_store = RunManifestStore(settings.traces_dir, settings.run_id)
        versions = build_versions(settings, "", "", calendar=calendar)

        # Foto del universo al arrancar. El acervo se sigue cargando, así que
        # sin esto dos corridas no son comparables en exhaustividad.
        census = await take_census(estadistica_client)
        if census.get("errors"):
            logger.warning(f"Censo del acervo incompleto: {census['errors']}")

        manifest_store.load_or_create(
            versions,
            label=settings.run_label or settings.run_id,
            question_set=settings.question_set or None,
            corpus_census=census,
        )
        logger.info(
            f"Censo del acervo: {census.get('total')} expedientes — "
            + ", ".join(f"{k}:{v}" for k, v in census.get("by_prefix", {}).items())
        )

        # Con universo restringido, el censo debe dar exactamente la lista.
        # Si da menos, el servicio no tiene todos los documentos que la
        # configuración declara, y una corrida así mediría un universo más
        # chico sin que se note: las preguntas de exhaustividad saldrían
        # "completas" sobre un conjunto incompleto. Es la verificación que
        # pide el paso 02 del protocolo, y falla ruidosamente a propósito.
        if estadistica_client.universo is not None:
            esperados = len(estadistica_client.universo)
            medidos = census.get("total") or 0
            if medidos != esperados:
                faltantes = sorted(
                    c for c in estadistica_client.universo.case_links
                    if c not in {
                        r for r in census.get("case_links_vistos", [])
                    }
                ) if census.get("case_links_vistos") else []
                logger.error(
                    f"UNIVERSO INCOMPLETO EN EL SERVICIO: la configuración "
                    f"declara {esperados} expedientes y el censo encontró "
                    f"{medidos}. No se puede afirmar exhaustividad sobre este "
                    f"universo."
                    + (f" Faltan: {', '.join(faltantes[:10])}" if faltantes else "")
                )
            else:
                logger.info(
                    f"Universo verificado: los {esperados} expedientes de "
                    f"{estadistica_client.universo.etiqueta} están disponibles."
                )
        # Un campo que la API deja de mandar no produce ningún error: el
        # registro se parsea igual, el campo queda en None y el agente
        # responde "no hay dato" sobre información que sí existe. Tiene que
        # gritar al arrancar.
        if census.get("campos_no_declarados"):
            nd = census["campos_no_declarados"]
            logger.error(
                f"CAMPOS QUE LA API MANDA Y EL MODELO NO DECLARA ({len(nd)}): "
                + ", ".join(f"{k}({v})" for k, v in list(nd.items())[:12])
                + (" …" if len(nd) > 12 else "")
                + ". Pydantic los descarta en silencio y el agente los va a "
                  "reportar como inexistentes."
            )

        if census.get("campos_ausentes"):
            logger.error(
                "CAMPOS AUSENTES en la respuesta de la API: "
                + ", ".join(census["campos_ausentes"])
                + ". El agente va a reportar esos datos como inexistentes."
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
