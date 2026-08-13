"""
Pruebas de la trazabilidad estructurada.

Lo que se garantiza aquí:

1. La traza se escribe con todos sus bloques.
2. `coverage.truncated` se enciende solo cuando la respuesta se construyó
   sobre una fracción del universo (meta.total > devueltos).
3. `context_condensed` se enciende en la ruta de streaming, que es donde el
   modelo redacta sin ver la evidencia completa.
4. `out_of_coverage` detecta plazos calculados fuera del rango del catálogo.
5. `citations_unresolved` detecta citas alucinadas.
6. `scope_mismatch` detecta el error VCN→CNT.
7. Con la trazabilidad apagada, el agente corre exactamente igual.
8. La traza se escribe también cuando el turno revienta a media respuesta.

Los tests que tocan red usan el mock server (puerto 3000); se saltan solos si
no está levantado.

    uvicorn mock_search_server:app --port 3000
    python -m pytest tests/test_tracing.py -v
"""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from agent.agent import NormaPlusAgent
from core.citation_builder import CitationBuilder
from core.evidence_cache import EvidenceCache
from core.tracing import (
    Request as TraceRequest, TraceCollector, analyze_answer, build_versions,
    interpret,
)
from core.tracing.schema import Versions
from core.tracing.sinks import JsonlFileSink, NullSink
from models.schemas import LLMToolResponse, LLMStreamChunk, ToolCallRequest
from retrieval.criterios_client import CriteriosSearchClient
from retrieval.estadistica_client import EstadisticaSearchClient
from temporal.analyzer import TemporalAnalyzer
from temporal.holidays import HolidayCalendar

MOCK_URL = "http://localhost:3000"


def mock_running() -> bool:
    try:
        return httpx.get(f"{MOCK_URL}/health", timeout=1.0).status_code == 200
    except Exception:
        return False


requires_mock = pytest.mark.skipif(
    not mock_running(), reason="mock_search_server no está en :3000"
)


# ── Dobles de prueba ────────────────────────────────────────

class FakeAdapter:
    """
    Adaptador LLM que sigue un guion fijo. Permite probar el agente completo
    sin llamar a ningún proveedor.
    """

    def __init__(self, script: list[LLMToolResponse], stream_text: str = ""):
        self.script = list(script)
        self.stream_text = stream_text
        self.calls = 0

    async def completion_with_tools(self, messages, model, tools):
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return LLMToolResponse(content="Respuesta final.", input_tokens=10,
                               output_tokens=5)

    async def stream_completion(self, messages, model):
        yield LLMStreamChunk(text=self.stream_text, input_tokens=20,
                             output_tokens=8)

    async def quick_completion(self, messages, model, max_tokens=20):
        return "Título"


class FakeRegistry:
    def __init__(self, adapter):
        self.adapter = adapter
        self.providers = ["openai"]

    def get_adapter(self, provider):
        return self.adapter


def build_agent(tmp_path, adapter, run_id="test_run", enabled=True, settings=None):
    from config import Settings

    settings = settings or Settings(
        tracing_enabled=enabled,
        traces_dir=str(tmp_path),
        run_id=run_id,
        search_api_base_url=MOCK_URL,
        holidays_path="data/dias_inhabiles.xlsx",
    )
    calendar = HolidayCalendar(settings.holidays_path)
    sink = JsonlFileSink(str(tmp_path), run_id) if enabled else NullSink()

    return NormaPlusAgent(
        llm_registry=FakeRegistry(adapter),
        criterios_client=CriteriosSearchClient(MOCK_URL, "1.test"),
        estadistica_client=EstadisticaSearchClient(MOCK_URL, "1.test"),
        temporal_analyzer=TemporalAnalyzer(calendar=calendar),
        citation_builder=CitationBuilder(),
        evidence_cache=EvidenceCache(),
        trace_sink=sink,
        settings=settings,
    ), settings


async def drain(agent, **kwargs):
    events = []
    async for ev in agent.run(**kwargs):
        events.append(ev)
    return events


def read_trace(tmp_path, run_id="test_run") -> dict:
    traces = list((Path(tmp_path) / run_id / "traces").glob("*.json"))
    assert traces, "no se escribió ninguna traza"
    return json.loads(traces[0].read_text(encoding="utf-8"))


# ── Interpretación heurística ───────────────────────────────

class TestInterpretation:

    def test_detecta_prefijo_y_autoridad(self):
        i = interpret("¿Cuántos VCN ha resuelto la COFECE?")
        assert i.scope.procedure_prefix == ["VCN"]
        assert i.scope.authority == "COFECE"
        assert i.constraints.exhaustive is True

    def test_prefijo_implicito_por_expediente(self):
        i = interpret("¿Cuánto tardó el IO-001-2019?")
        assert "IO" in i.scope.procedure_prefix
        assert i.scope.case_links == ["IO-001-2019"]

    def test_intent_nunca_es_del_agente(self):
        """El agente no tiene paso de interpretación: no inventamos uno."""
        i = interpret("¿Qué es mercado relevante?")
        assert i.intent is None
        assert all(v == "heuristic" for v in i.provenance.values())

    def test_computo_temporal(self):
        i = interpret("¿Cuántos días hábiles tardó la resolución?")
        assert i.constraints.requires_computation is True

    def test_preposicion_de_no_es_prefijo(self):
        """
        'DE' es un prefijo de expediente y también una preposición. Buscarlo
        suelto marcaba scope=['DE','VCN'] en "los VCN de COFECE" y envenenaba
        scope_mismatch.
        """
        i = interpret("Dame todos los VCN de COFECE")
        assert i.scope.procedure_prefix == ["VCN"]

    def test_prefijo_ambiguo_si_viene_en_expediente(self):
        i = interpret("Revisa el DE-001-2020")
        assert i.scope.procedure_prefix == ["DE"]


# ── Análisis de la respuesta ────────────────────────────────

class TestAnswerAnalysis:

    def test_cita_alucinada(self):
        criterios = [[{"id": "1", "metadata": {"id_expediente": "CNT-001-2020"}}]]
        answer = analyze_answer(
            text="Según el criterio [C1] y también [C7].",
            citation_builder=CitationBuilder(),
            criterio_results=criterios,
            expediente_results=[],
            references=[],
            docs_in_context=[],
            expected_prefixes=[],
        )
        assert answer.citations_emitted == ["C1", "C7"]
        assert answer.citations_unresolved == ["C7"]

    def test_scope_mismatch_vcn_vs_cnt(self):
        """El error del comentario #7: se pidió VCN y contestó con CNT."""
        answer = analyze_answer(
            text="El expediente CNT-095-2013 resolvió el asunto.",
            citation_builder=CitationBuilder(),
            criterio_results=[], expediente_results=[], references=[],
            docs_in_context=[],
            expected_prefixes=["VCN"],
        )
        assert answer.scope_mismatch is True

    def test_scope_ok_cuando_coincide(self):
        answer = analyze_answer(
            text="El expediente VCN-001-2022 fue sancionado.",
            citation_builder=CitationBuilder(),
            criterio_results=[], expediente_results=[], references=[],
            docs_in_context=[],
            expected_prefixes=["VCN"],
        )
        assert answer.scope_mismatch is False

    def test_markdown_crudo_es_medible(self):
        answer = analyze_answer(
            text="### Título\n**negritas** | tabla |",
            citation_builder=CitationBuilder(),
            criterio_results=[], expediente_results=[], references=[],
            docs_in_context=[], expected_prefixes=[],
        )
        assert answer.format_markers["h3"] == 1
        assert answer.format_markers["bold"] == 1
        assert answer.format_markers["pipes"] == 2

    def test_docs_recuperados_sin_citar(self):
        answer = analyze_answer(
            text="Respuesta sin citas.",
            citation_builder=CitationBuilder(),
            criterio_results=[], expediente_results=[], references=[],
            docs_in_context=[{"doc_id": "d1", "case_link": "CNT-001-2020"}],
            expected_prefixes=[],
        )
        assert answer.docs_in_context_uncited == ["d1"]


# ── Cobertura del calendario ────────────────────────────────

class TestHolidayCoverage:

    @pytest.fixture(scope="class")
    @classmethod
    def cal(cls):
        return HolidayCalendar("data/dias_inhabiles.xlsx")

    def test_rangos_por_institucion(self, cal):
        ranges = cal.coverage_ranges()
        assert "COFECE" in ranges and "CNA" in ranges
        # La cobertura es desigual: por eso hace falta registrarla por traza.
        assert ranges["COFECE"][0] < ranges["CNA"][0]

    def test_fecha_dentro_de_rango(self, cal):
        assert cal.is_covered(date(2019, 1, 24), "COFECE") is True

    def test_fecha_fuera_de_rango_cofece(self, cal):
        assert cal.is_covered(date(2030, 1, 1), "COFECE") is False

    def test_cna_no_cubre_fechas_viejas(self, cal):
        """El catálogo de CNA arranca en 2025: antes no hay datos."""
        assert cal.is_covered(date(2019, 1, 24), "CNA") is False

    def test_hash_del_catalogo(self, cal):
        assert cal.source_sha256 and len(cal.source_sha256) == 12


# ── Versionado y baseline ───────────────────────────────────

class TestVersioning:

    def test_hashes_calculados(self):
        from config import Settings
        v = build_versions(Settings(), "openai", "gpt-4.1")
        assert len(v.prompt_sha256) == 12
        assert len(v.tools_sha256) == 12

    def test_declara_lo_que_no_puede_versionar(self):
        """Nunca null silencioso: la reproducibilidad parcial se declara."""
        from config import Settings
        v = build_versions(Settings(), "openai", "gpt-4.1")
        assert "index_version" in v.unknown
        assert "embeddings_model" in v.unknown

    def test_drift_detectado(self):
        base = Versions(prompt_sha256="aaa", tools_sha256="bbb")
        collector = TraceCollector(
            conversation_id="s1",
            versions=Versions(prompt_sha256="ZZZ", tools_sha256="bbb"),
            request=TraceRequest(query="q"),
        )
        drift = collector.check_drift(base.fingerprint())
        assert any(d["field"] == "prompt_sha256" for d in drift)
        assert collector.decisions.baseline_drift is True

    def test_sin_drift_cuando_coincide(self):
        v = Versions(prompt_sha256="aaa", tools_sha256="bbb")
        collector = TraceCollector(
            conversation_id="s1", versions=v, request=TraceRequest(query="q"),
        )
        assert collector.check_drift(v.fingerprint()) == []
        assert collector.decisions.baseline_drift is False

    def test_modelo_no_cuenta_como_drift_del_entorno(self):
        """
        provider/model varían por petición: si entraran al fingerprint, toda
        traza marcaría drift. Quedan fuera y se acumulan en models_observed.
        """
        base = Versions(prompt_sha256="aaa", provider="openai", model="gpt-4.1")
        otro = Versions(prompt_sha256="aaa", provider="anthropic",
                        model="claude-sonnet-4-20250514")
        assert base.fingerprint() == otro.fingerprint()
        assert "model" not in base.fingerprint()


# ── Agente end-to-end ───────────────────────────────────────

@requires_mock
class TestAgentTracing:

    @pytest.mark.asyncio
    async def test_traza_completa_con_retrieval(self, tmp_path):
        adapter = FakeAdapter(script=[
            LLMToolResponse(
                tool_calls=[ToolCallRequest(
                    id="t1", name="buscar_expedientes",
                    arguments={"text_search": "Scotiabank", "limit": 2},
                )],
                input_tokens=100, output_tokens=20,
            ),
            LLMToolResponse(content="Resultado [E1].", input_tokens=50,
                            output_tokens=10),
        ])
        agent, _ = build_agent(tmp_path, adapter)
        await drain(agent, session_id="s1", user_query="¿Operaciones de Scotiabank?",
                    provider="openai", model="gpt-4.1", chat_history=[])

        trace = read_trace(tmp_path)
        assert trace["conversation_id"] == "s1"
        assert trace["outcome"]["status"] == "ok"
        assert "buscar_expedientes" in trace["decisions"]["tools_used"]

        tool_steps = [s for s in trace["steps"] if s["kind"] == "tool_call"]
        assert len(tool_steps) == 1
        step = tool_steps[0]
        # Los parámetros literales enviados a la API quedan registrados:
        # es lo que permite replicar la búsqueda meses después.
        assert step["http_request"]["method"] == "GET"
        assert "searchData" in step["http_request"]["params"]
        assert step["coverage"]["total_available"] is not None

    @pytest.mark.asyncio
    async def test_tres_etapas_de_retrieval_en_criterios(self, tmp_path):
        adapter = FakeAdapter(script=[
            LLMToolResponse(
                tool_calls=[ToolCallRequest(
                    id="t1", name="buscar_criterios",
                    arguments={"query": "mercado relevante", "top_k": 5},
                )],
                input_tokens=100, output_tokens=20,
            ),
            LLMToolResponse(content="Ver [C1].", input_tokens=50, output_tokens=10),
        ])
        agent, _ = build_agent(tmp_path, adapter)
        await drain(agent, session_id="s2", user_query="¿Qué es mercado relevante?",
                    provider="openai", model="gpt-4.1", chat_history=[])

        trace = read_trace(tmp_path)
        step = [s for s in trace["steps"] if s["kind"] == "tool_call"][0]
        stages = {s["stage"]: s for s in step["stages"]}
        assert set(stages) == {"candidates", "after_ranking", "in_context"}
        # La distinción es material: entre la primera y la última se pierde texto.
        assert "reranker" in stages["after_ranking"]["notes"].lower()
        assert "700" in stages["in_context"]["method"]

    @pytest.mark.asyncio
    async def test_coverage_truncated_cuando_hay_mas_universo(self, tmp_path):
        """El antídoto contra la falsa certeza: pedir 1 de N lo marca solo."""
        adapter = FakeAdapter(script=[
            LLMToolResponse(
                tool_calls=[ToolCallRequest(
                    id="t1", name="buscar_expedientes", arguments={"limit": 1},
                )],
                input_tokens=100, output_tokens=20,
            ),
            LLMToolResponse(content="Listo.", input_tokens=50, output_tokens=10),
        ])
        agent, _ = build_agent(tmp_path, adapter)
        await drain(agent, session_id="s3", user_query="Dame todos los expedientes",
                    provider="openai", model="gpt-4.1", chat_history=[])

        trace = read_trace(tmp_path)
        cov = [s for s in trace["steps"] if s["kind"] == "tool_call"][0]["coverage"]
        assert cov["returned"] == 1
        assert cov["total_available"] > 1
        assert cov["truncated"] is True
        assert cov["truncation_reason"] == "limit"

    @pytest.mark.asyncio
    async def test_context_condensed_en_ruta_de_streaming(self, tmp_path):
        """
        Cuando el loop termina sin `content`, la respuesta final se arma con
        los resultados condensados a ~200 chars. La bandera lo deja registrado.
        """
        adapter = FakeAdapter(
            script=[
                LLMToolResponse(
                    tool_calls=[ToolCallRequest(
                        id="t1", name="buscar_expedientes",
                        arguments={"limit": 2},
                    )],
                    input_tokens=100, output_tokens=20,
                ),
                LLMToolResponse(content=None, input_tokens=50, output_tokens=10),
            ],
            stream_text="Respuesta desde streaming.",
        )
        agent, _ = build_agent(tmp_path, adapter)
        await drain(agent, session_id="s4", user_query="¿Qué expedientes hay?",
                    provider="openai", model="gpt-4.1", chat_history=[])

        trace = read_trace(tmp_path)
        assert trace["decisions"]["context_condensed"] is True
        assert trace["decisions"]["final_answer_path"] == "stream"

    @pytest.mark.asyncio
    async def test_trace_id_llega_al_evento_done(self, tmp_path):
        adapter = FakeAdapter(script=[
            LLMToolResponse(content="Hola.", input_tokens=10, output_tokens=5),
        ])
        agent, _ = build_agent(tmp_path, adapter)
        events = await drain(agent, session_id="s5", user_query="Hola",
                             provider="openai", model="gpt-4.1", chat_history=[])
        done = [e for e in events if e.type == "done"][0]
        assert done.data["trace_id"].startswith("tr_")

    @pytest.mark.asyncio
    async def test_traza_se_escribe_aunque_el_turno_reviente(self, tmp_path):
        """El caso del comentario #5 es el más valioso de conservar."""
        class ExplodingAdapter(FakeAdapter):
            async def completion_with_tools(self, messages, model, tools):
                raise RuntimeError("proveedor caído")

        agent, _ = build_agent(tmp_path, ExplodingAdapter(script=[]))
        with pytest.raises(RuntimeError):
            await drain(agent, session_id="s6", user_query="Pregunta",
                        provider="openai", model="gpt-4.1", chat_history=[])

        trace = read_trace(tmp_path)
        assert trace["outcome"]["status"] == "error"
        assert trace["errors"]

    @pytest.mark.asyncio
    async def test_sin_trazabilidad_el_agente_corre_igual(self, tmp_path):
        adapter = FakeAdapter(script=[
            LLMToolResponse(content="Respuesta.", input_tokens=10, output_tokens=5),
        ])
        agent, _ = build_agent(tmp_path, adapter, enabled=False)
        events = await drain(agent, session_id="s7", user_query="Hola",
                             provider="openai", model="gpt-4.1", chat_history=[])
        assert [e for e in events if e.type == "token"]
        assert not (Path(tmp_path) / "test_run").exists()

    @pytest.mark.asyncio
    async def test_resumen_jsonl(self, tmp_path):
        adapter = FakeAdapter(script=[
            LLMToolResponse(content="Respuesta.", input_tokens=10, output_tokens=5),
        ])
        agent, _ = build_agent(tmp_path, adapter)
        await drain(agent, session_id="s8", user_query="¿Cuántos VCN hay?",
                    provider="openai", model="gpt-4.1", chat_history=[])

        lines = (Path(tmp_path) / "test_run" / "run.jsonl").read_text().splitlines()
        row = json.loads(lines[0])
        assert row["scope_expected"] == "VCN"
        assert row["query"] == "¿Cuántos VCN hay?"
        assert "coverage_truncated" in row
