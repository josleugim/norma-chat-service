"""
Pruebas de la trazabilidad estructurada.

Lo que se garantiza aquí:

1. La traza se escribe con todos sus bloques.
2. `coverage.truncated` se enciende cuando la respuesta pudo construirse
   sobre una fracción del universo. Sin `meta.total` en la API nueva, la
   señal es `returned == limit`, y el total se deja en None en vez de
   inventarlo.
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
from datetime import date, timedelta
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

    @pytest.mark.parametrize("palabra", [
        "todos", "cuántos", "cuáles", "mayor", "menor", "promedio", "nunca",
    ])
    def test_disparadores_de_exhaustividad_de_cofece(self, palabra):
        """
        COFECE listó explícitamente estas palabras como señal de que no basta
        un top-k semántico normal.
        """
        assert interpret(f"¿{palabra} de los expedientes?").constraints.exhaustive

    def test_herramientas_esperadas(self):
        from core.tracing.heuristics import expected_tools
        assert "calcular_plazos" in expected_tools(
            "¿cuántos días hábiles tardó el VCN-002-2020?"
        )
        assert "buscar_criterios" in expected_tools(
            "¿qué criterios usa la COFECE para el mercado relevante?"
        )
        assert expected_tools("hola") == []

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
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        reg.assign({"id": "1", "metadata": {"id_expediente": "CNT-001-2020"}}, "C")
        refs, sin_resolver = CitationBuilder().build_from_registry(
            "Según el criterio [C1] y también [C7].", reg
        )
        answer = analyze_answer(
            text="Según el criterio [C1] y también [C7].",
            registry=reg, references=refs, unresolved=sin_resolver,
            docs_in_context=[], expected_prefixes=[],
        )
        assert answer.citations_emitted == ["C1", "C7"]
        assert answer.citations_unresolved == ["C7"]

    def test_scope_mismatch_vcn_vs_cnt(self):
        """El error del comentario #7: se pidió VCN y contestó con CNT."""
        answer = analyze_answer(
            text="El expediente CNT-095-2013 resolvió el asunto.",
            registry=None, references=[], unresolved=[],
            docs_in_context=[],
            expected_prefixes=["VCN"],
        )
        assert answer.scope_mismatch is True

    def test_scope_mismatch_por_retrieval_sin_citas(self):
        """
        El caso real de q01: se pregunta por VCN, el agente busca
        concentraciones notificadas y contesta con un promedio agregado sin
        citar ningún expediente. El texto no delata nada; los documentos que
        entraron al contexto sí.
        """
        answer = analyze_answer(
            text="El tiempo promedio es de 49 días hábiles.",
            registry=None, references=[], unresolved=[],
            docs_in_context=[
                {"doc_id": "1", "case_link": "CNT-030-2015"},
                {"doc_id": "2", "case_link": "CNT-011-2017"},
            ],
            expected_prefixes=["VCN"],
        )
        assert answer.case_links_mentioned == []
        assert answer.scope_observed == ["CNT"]
        assert answer.scope_mismatch is True

    def test_scope_ok_cuando_coincide(self):
        answer = analyze_answer(
            text="El expediente VCN-001-2022 fue sancionado.",
            registry=None, references=[], unresolved=[],
            docs_in_context=[],
            expected_prefixes=["VCN"],
        )
        assert answer.scope_mismatch is False

    def test_markdown_crudo_es_medible(self):
        answer = analyze_answer(
            text="### Título\n**negritas** | tabla |",
            registry=None, references=[], unresolved=[],
            docs_in_context=[], expected_prefixes=[],
        )
        assert answer.format_markers["h3"] == 1
        assert answer.format_markers["bold"] == 1
        assert answer.format_markers["pipes"] == 2

    def test_docs_recuperados_sin_citar(self):
        answer = analyze_answer(
            text="Respuesta sin citas.",
            registry=None, references=[], unresolved=[],
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
        # `agent-search` no expone `meta.total`, así que no hay un universo
        # contra el cual comparar: la señal es que la API devolvió justo el
        # tope pedido. Y precisamente por eso no se afirma ningún total —
        # inventar uno sería la falsa certeza que este test vigila.
        assert cov["truncated"] is True
        assert cov["total_available"] is None
        assert cov["truncated"] is True
        assert cov["truncation_reason"] == "returned==limit"

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


# ── Atribución de fallas por etapa ──────────────────────────

class TestDiagnose:
    """
    Criterio de aceptación de COFECE: ante una respuesta incorrecta, poder
    determinar si el error fue de pregunta/filtros, retrieval, selección de
    contexto, tool calling, generación o cita.
    """

    def _trace(self, **kw):
        from core.tracing.schema import Trace
        t = Trace(trace_id="t", conversation_id="c",
                  timestamp_utc="2026-08-14T00:00:00Z")
        for bloque, valores in kw.items():
            for k, v in valores.items():
                setattr(getattr(t, bloque), k, v)
        return t

    def test_scope_mismatch_apunta_a_pregunta_filtros(self):
        from core.tracing.diagnose import diagnose
        t = self._trace(answer={"scope_mismatch": True, "scope_observed": ["CNT"]})
        t.interpretation.scope.procedure_prefix = ["VCN"]
        d = diagnose(t)
        assert d["stage"] == "pregunta/filtros"
        assert "VCN" in d["reason"] and "CNT" in d["reason"]

    def test_exhaustiva_truncada_apunta_a_retrieval(self):
        from core.tracing.diagnose import diagnose
        t = self._trace(decisions={"exhaustive_but_truncated": True})
        assert diagnose(t)["stage"] == "retrieval"

    def test_tool_faltante_apunta_a_tool_calling(self):
        from core.tracing.diagnose import diagnose
        t = self._trace(decisions={"tools_expected_not_called": ["calcular_plazos"]})
        d = diagnose(t)
        assert d["stage"] == "tool_calling"
        assert "calcular_plazos" in d["reason"]

    def test_cita_alucinada_apunta_a_cita(self):
        from core.tracing.diagnose import diagnose
        t = self._trace(answer={"citations_unresolved": ["C7"]})
        assert diagnose(t)["stage"] == "cita"

    def test_reporta_la_etapa_mas_temprana(self):
        """Los errores se propagan: si el filtro estuvo mal, culpar a la
        generación despista."""
        from core.tracing.diagnose import diagnose
        t = self._trace(
            answer={"scope_mismatch": True, "citations_unresolved": ["C7"]},
            decisions={"tools_expected_not_called": ["calcular_plazos"]},
        )
        d = diagnose(t)
        assert d["stage"] == "pregunta/filtros"
        assert len(d["signals"]) == 3   # las otras quedan registradas igual

    def test_flujo_limpio(self):
        from core.tracing.diagnose import diagnose
        assert diagnose(self._trace())["stage"] is None


# ── Fixes de v1.1 ───────────────────────────────────────────

class TestConvencionPlazos:
    """
    COFECE: "el cómputo debe excluir el día inicial e incluir el día final".
    Antes se excluían ambos, lo que subcontaba un día.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def cal(cls):
        return HolidayCalendar("data/dias_inhabiles.xlsx")

    def test_ejemplo_del_reporte(self, cal):
        """21-dic-2018 → 24-ene-2019: el agente daba 13, lo correcto es 14."""
        n = cal.business_days_between(date(2018, 12, 21), date(2019, 1, 24), "COFECE")
        assert n == 14

    def test_convencion_anterior_sigue_disponible(self, cal):
        n = cal.business_days_between(
            date(2018, 12, 21), date(2019, 1, 24), "COFECE", include_end=False
        )
        assert n == 13

    def test_dia_final_inhabil_no_suma(self, cal):
        """Si el día final es inhábil, incluirlo no cambia el conteo."""
        con = cal.business_days_between(date(2019, 1, 21), date(2019, 1, 26), "COFECE")
        sin = cal.business_days_between(
            date(2019, 1, 21), date(2019, 1, 26), "COFECE", include_end=False
        )
        assert con == sin   # 26-ene-2019 es sábado

    def test_mismo_dia_es_cero(self, cal):
        assert cal.business_days_between(date(2019, 1, 21), date(2019, 1, 21)) == 0


class TestCoincidenciaTolerante:
    """
    Los filtros se aplican localmente porque la API une con OR en vez de
    intersectar. El match no puede ser exacto —el modelo no reproduce las
    etiquetas literales— pero tampoco tan laxo que confunda sentidos opuestos.
    """

    @pytest.mark.parametrize("real,pedido,esperado", [
        # El modelo parafrasea; debe coincidir igual
        ("NO SE ACREDITÓ INCUMPLIMIENTO", "NO ACREDITADO EL INCUMPLIMIENTO", True),
        ("SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO", "SANCION", True),
        ("CIERRE POR INEXISTENCIA DE ELEMENTOS", "cierre por inexistencia", True),
        ("COFECE", "Cofece", True),
        # La negación decide el sentido: NUNCA deben confundirse
        ("NO SE ACREDITÓ INCUMPLIMIENTO",
         "SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO", False),
        ("SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO",
         "NO SE ACREDITÓ INCUMPLIMIENTO", False),
        ("NO SE ACREDITÓ INCUMPLIMIENTO", "AUTORIZADA", False),
        ("CFC", "COFECE", False),
    ])
    def test_coincidencia(self, real, pedido, esperado):
        from agent.agent import _coincide, _normalizar
        assert _coincide(_normalizar(real), _normalizar(pedido)) is esperado


# ── Citas: el bug crítico reportado por COFECE ──────────────

class TestRegistroDeCitas:
    """
    COFECE, 14-ago-2026: respuestas materialmente correctas cuya cita apuntaba
    a OTRO expediente (q05, q06, q12, q18, q19). Severidad crítica: manda a un
    abogado a leer el expediente equivocado.

    Causa: la resolución era posicional. Con dos búsquedas en un turno, [E1] se
    resolvía contra el último bloque donde el índice fuera válido.

    Ahora el marcador se entrega junto con el documento y se resuelve por
    diccionario. Criterio de aceptación: 0 citas al expediente incorrecto.
    """

    def _registry(self):
        from core.citations import CitationRegistry
        return CitationRegistry()

    def test_dos_busquedas_no_confunden_expedientes(self):
        """El caso exacto que reportaron."""
        reg = self._registry()
        m1 = reg.assign({"caseLink": "VCN-004-2024"}, "E")   # 1ª búsqueda
        m2 = reg.assign({"caseLink": "VCN-004-2022"}, "E")   # 2ª búsqueda
        assert m1 == "E1" and m2 == "E2"
        assert reg.case_link_of("E1") == "VCN-004-2024"
        assert reg.case_link_of("E2") == "VCN-004-2022"

    def test_la_numeracion_no_se_reinicia_entre_busquedas(self):
        reg = self._registry()
        for i in range(3):
            reg.assign({"caseLink": f"CNT-00{i}-2020"}, "E")
        # Segunda búsqueda: debe continuar en E4, no volver a E1
        assert reg.assign({"caseLink": "VCN-001-2022"}, "E") == "E4"

    def test_mismo_expediente_mismo_marcador(self):
        reg = self._registry()
        a = reg.assign({"caseLink": "VCN-001-2022"}, "E")
        b = reg.assign({"caseLink": "VCN-001-2022"}, "E")
        assert a == b

    def test_criterios_y_expedientes_numeran_aparte(self):
        reg = self._registry()
        assert reg.assign({"id": "1"}, "C") == "C1"
        assert reg.assign({"caseLink": "VCN-001-2022"}, "E") == "E1"

    def test_marcador_inexistente_no_se_muestra(self):
        """Mejor sin cita que con una fuente equivocada."""
        reg = self._registry()
        reg.assign({"caseLink": "VCN-001-2022"}, "E")
        refs, sin_resolver = CitationBuilder().build_from_registry(
            "Según [E1] y también [E9].", reg
        )
        assert [r.id_expediente for r in refs] == ["VCN-001-2022"]
        assert sin_resolver == ["E9"]

    def test_la_cita_apunta_al_expediente_del_que_salio_el_dato(self):
        """Prueba de regresión del bug: antes [E1] daba VCN-004-2022."""
        reg = self._registry()
        reg.assign({"caseLink": "VCN-004-2024"}, "E")
        reg.assign({"caseLink": "VCN-004-2022"}, "E")
        refs, _ = CitationBuilder().build_from_registry(
            "La multa máxima corresponde a VCN-004-2024 [E1].", reg
        )
        assert refs[0].id_expediente == "VCN-004-2024"
        assert refs[0].marker == "E1"

    def test_el_registro_queda_auditable_en_la_traza(self):
        reg = self._registry()
        reg.assign({"caseLink": "VCN-001-2022", "id": 7}, "E")
        fila = reg.to_trace()[0]
        assert fila["marker"] == "E1"
        assert fila["case_link"] == "VCN-001-2022"
        assert fila["source_type"] == "estadistica"


class TestFechasInconsistentes:
    """
    114 expedientes tienen la fecha de resolución ANTES que la de
    notificación. business_days_between devolvía 0 cuando start >= end, así
    que esos datos malos se disfrazaban de "resuelto el mismo día" y ganaban
    cualquier consulta de mínimo.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def analyzer(cls):
        from temporal.analyzer import TemporalAnalyzer
        return TemporalAnalyzer(HolidayCalendar("data/dias_inhabiles.xlsx"))

    def test_fecha_invertida_no_es_plazo_cero(self, analyzer):
        r = analyzer.compute_between_fields([{
            "caseLink": "CNT-085-2020", "authority": "COFECE",
            "notificationDate": "10-08-2020", "resolutionDate": "09-03-2020",
        }])[0]
        assert r["calculable"] is False
        assert r["anomalia"] == "fecha_fin_anterior_a_inicio"
        assert r["dias_habiles"] is None

    def test_vcn_usa_acuerdo_de_inicio(self, analyzer):
        """En VCN la fecha de notificación no existe por diseño."""
        r = analyzer.compute_between_fields([{
            "caseLink": "VCN-001-2022", "authority": "COFECE",
            "startAgreementDate": "21-04-2022", "resolutionDate": "07-06-2022",
        }])[0]
        assert r["campo_inicio"] == "startAgreementDate"
        assert r["calculable"] and r["dias_habiles"] > 0

    def test_cnt_usa_notificacion(self, analyzer):
        r = analyzer.compute_between_fields([{
            "caseLink": "CNT-095-2013", "authority": "COFECE",
            "notificationDate": "23-09-2013", "resolutionDate": "21-02-2014",
        }])[0]
        assert r["campo_inicio"] == "notificationDate"
        assert r["dias_habiles"] == 96

    def test_par_de_campos_arbitrario(self, analyzer):
        """La herramienta ya no está cableada a notificación → resolución."""
        r = analyzer.compute_between_fields(
            [{"caseLink": "CNT-001-2020", "authority": "COFECE",
              "notificationDate": "10-01-2020", "admissionDate": "20-01-2020"}],
            campo_inicio="notificationDate", campo_fin="admissionDate",
        )[0]
        assert r["calculable"] and r["campo_fin"] == "admissionDate"

    def test_falta_una_fecha_no_se_estima(self, analyzer):
        r = analyzer.compute_between_fields([{
            "caseLink": "VCN-002-2018", "authority": "COFECE",
            "startAgreementDate": "05-04-2018", "resolutionDate": None,
        }])[0]
        assert r["calculable"] is False
        assert "resolutionDate" in r["campos_faltantes"]


# ── Fixes de la adjudicación de v1.7 ────────────────────────

class TestFixesV17:
    """Los seis puntos que COFECE pidió corregir sobre v1.7."""

    def test_has_multas_es_triestado(self):
        """
        FIX 1 (q18): `false` se trataba como ausencia de filtro, así que al
        pedir "VCN sin multa" llegaban todos al modelo para que dedujera.
        """
        from agent.agent import _tiene_multa
        con = {"caseLink": "VCN-001-2022", "agentFines": "{'X': '$1,000.00'}"}
        sin = {"caseLink": "VCN-002-2022", "agentFines": None}
        assert _tiene_multa(con) is True
        assert _tiene_multa(sin) is False
        # El filtro compara identidad booleana, no truthiness
        for pedido, esperado in ((True, [con]), (False, [sin])):
            assert [r for r in (con, sin)
                    if _tiene_multa(r) is bool(pedido)] == esperado

    @pytest.mark.parametrize("valor,estado", [
        ("$1,400,000,00", "ambiguous"),    # el que produjo el dato falso
        ("1.400.000,00", "ambiguous"),
        ("$1,324,195.20", "valid"),
        ("$40,838,762.32", "valid"),
        ("Confidencial", "non_numeric"),
        ("N/D", "non_numeric"),
    ])
    def test_parser_conservador_de_montos(self, valor, estado):
        """
        FIX 4 (q05, q11): el parser eliminaba comas y convertía
        `$1,400,000,00` en 140,000,000 — cien veces el valor probable.
        Regla de COFECE: si hay que adivinar, no se usa.
        """
        from core.aggregation import parse_monto
        assert parse_monto(valor)["status"] == estado

    def test_monto_ambiguo_no_entra_al_calculo(self):
        from core.aggregation import parse_multas
        assert parse_multas("{'Agente': '$1,400,000,00'}") == {}

    def test_evidence_check_separa_concepto_de_atribucion(self):
        """
        FIX 2 (q16): la evidencia hablaba de otra cosa y el control lexical
        la declaraba suficiente porque las palabras aparecían.
        """
        from core.sufficiency import check_evidence
        docs = [{"text": "la omisión de notificar genera una afectación a las "
                         "atribuciones de la Comisión"}]
        r = check_evidence(
            "¿qué es el mercado relevante y cómo lo ha definido la COFECE?", docs
        )
        assert r["overall"] == "INSUFFICIENT"
        por_id = {c["id"]: c["estado"] for c in r["components"]}
        # El concepto se puede explicar de conocimiento general; la
        # atribución a COFECE no está respaldada.
        assert por_id["concepto_general"] == "SUFFICIENT"
        assert por_id["atribucion_a_autoridad"] == "INSUFFICIENT"

    def test_evidence_check_acepta_evidencia_pertinente(self):
        from core.sufficiency import check_evidence
        docs = [{"text": "el mercado relevante se define por sustituibilidad "
                         "de los productos y su dimensión geográfica"}]
        r = check_evidence("¿cómo ha definido la COFECE el mercado relevante?", docs)
        assert r["overall"] == "SUFFICIENT"

    def test_conteo_determinista_no_cuenta_como_truncado(self):
        """
        FIX 5 (q20): contar con meta.total no requiere traer 2,793
        expedientes; marcarlo truncado era un falso positivo.
        """
        from core.tracing import Request as TR, TraceCollector, Versions
        c = TraceCollector(conversation_id="s", versions=Versions(),
                           request=TR(query="¿cuántas resoluciones emitió la CFC?"))
        c.set_interpretation(interpret("¿cuántas resoluciones emitió la CFC?"))
        c.begin_step("tool_call", tool="contar_expedientes", arguments={})
        c.record_coverage(total_available=2793, requested_limit=1, returned=1)
        c.end_step()
        assert c.finish().decisions.exhaustive_but_truncated is False

    def test_mismo_dia_no_es_plazo_de_cero(self):
        """
        52 CNT tienen fecha de inicio y resolución idénticas. Matemáticamente
        da 0, pero un procedimiento resuelto el mismo día que se notifica no
        es creíble, y esos casos ganaban cualquier consulta de mínimo.
        """
        from temporal.analyzer import TemporalAnalyzer
        ta = TemporalAnalyzer(HolidayCalendar("data/dias_inhabiles.xlsx"))
        r = ta.compute_between_fields([{
            "caseLink": "CNT-045-2024", "authority": "COFECE",
            "notificationDate": "13-02-2025", "resolutionDate": "13-02-2025",
        }])[0]
        assert r["calculable"] is False
        assert r["anomalia"] == "fecha_inicio_igual_a_fin"
        assert r["dias_habiles"] is None

    def test_no_se_exige_calcular_plazos_sin_fecha_de_inicio(self):
        """
        q03 (v1.13): "cuántos días inhábiles tardó COFECE en resolver el
        VCN-002-2020". El expediente no tiene startAgreementDate ni
        notificationDate —solo fecha de resolución— así que el plazo no es
        calculable. El agente lo dijo y no llamó la herramienta, que es lo
        correcto; el evaluador lo marcaba como "tool esperada no llamada".
        """
        from core.tracing import Request as TR, TraceCollector, Versions
        q = "cuántos días inhábiles tardó cofece en resolver el VCN-002-2020"
        c = TraceCollector(conversation_id="s", versions=Versions(),
                           request=TR(query=q))
        c.set_interpretation(interpret(q))
        c.begin_step("tool_call", tool="buscar_expedientes", arguments={})
        c.record_stage(stage="in_context", method="serialize_full", docs=[{
            "id": 1, "caseLink": "VCN-002-2020", "authority": "COFECE",
            "startAgreementDate": None, "notificationDate": None,
            "resolutionDate": "16-04-2020", "senseOfResolution": "Sanciona",
        }])
        c.end_step()
        d = c.finish().decisions
        assert "calcular_plazos" not in (d.tools_expected_not_called or [])

    def test_si_hay_fecha_de_inicio_si_se_exige_calcular_plazos(self):
        """El contrapeso: con fecha de inicio, saltarse la herramienta sí es
        una falla y tiene que seguir marcándose."""
        from core.tracing import Request as TR, TraceCollector, Versions
        q = "cuántos días inhábiles tardó cofece en resolver el CNT-095-2013"
        c = TraceCollector(conversation_id="s", versions=Versions(),
                           request=TR(query=q))
        c.set_interpretation(interpret(q))
        c.begin_step("tool_call", tool="buscar_expedientes", arguments={})
        c.record_stage(stage="in_context", method="serialize_full", docs=[{
            "id": 2, "caseLink": "CNT-095-2013", "authority": "COFECE",
            "notificationDate": "10-05-2013", "resolutionDate": "20-06-2013",
        }])
        c.end_step()
        d = c.finish().decisions
        assert "calcular_plazos" in (d.tools_expected_not_called or [])

    def test_una_tool_puede_satisfacer_la_expectativa_de_otra(self):
        """
        FIX 5, segunda parte: agregar_expedientes recorre expedientes y
        calcula plazos. Exigir además buscar_expedientes o calcular_plazos
        marcaba cuatro FAIL falsos en v1.10.
        """
        from core.tracing import Request as TR, TraceCollector, Versions
        q = "¿cuál es la multa máxima impuesta en expedientes VCN?"
        c = TraceCollector(conversation_id="s", versions=Versions(),
                           request=TR(query=q))
        c.set_interpretation(interpret(q))
        c.begin_step("tool_call", tool="agregar_expedientes", arguments={})
        c.end_step()
        assert c.finish().decisions.tools_expected_not_called == []


class TestBordeDelCalendario:
    """
    Qué pasa cuando una fecha rebasa el rango del archivo de días inhábiles.

    El catálogo no cubre lo mismo para las dos instituciones: COFECE llega
    hasta el 17-nov-2025 y CNA arranca el 18-oct-2025. Fuera de su rango, el
    archivo simplemente no tiene los días —tampoco los fines de semana, que
    están cargados como registros— así que preguntar `d not in cofece_holidays`
    devolvía True para todo y el plazo degradaba a contar días naturales.
    """

    def test_fin_de_semana_fuera_de_rango_sigue_siendo_inhabil(self):
        cal = HolidayCalendar("data/dias_inhabiles.xlsx")
        tope = date.fromisoformat(cal.coverage_ranges()["COFECE"][1])
        sabado = tope + timedelta(days=(5 - tope.weekday()) % 7 + 7)
        assert sabado.weekday() == 5
        assert not cal.is_covered(sabado, "COFECE"), "el test necesita una fecha fuera de rango"
        assert cal.is_business_day(sabado, "COFECE") is False

    def test_plazo_fuera_de_rango_no_degrada_a_dias_naturales(self):
        """
        Cuatro semanas completas fuera del rango de COFECE son 20 días
        hábiles, no 28. Antes devolvía 28 —los 28 naturales— porque contaba
        los ocho días de fin de semana como hábiles.
        """
        cal = HolidayCalendar("data/dias_inhabiles.xlsx")
        ini, fin = date(2026, 2, 2), date(2026, 3, 2)
        assert not cal.is_covered(fin, "COFECE")
        habiles = cal.business_days_between(ini, fin, "COFECE")
        assert habiles == cal.business_days_between(ini, fin), (
            "fuera de su rango, la institución debe caer al calendario combinado"
        )
        assert habiles < (fin - ini).days

    def test_dentro_de_rango_manda_el_calendario_de_la_institucion(self):
        """El contrapeso: dentro de su rango no se toca nada."""
        cal = HolidayCalendar("data/dias_inhabiles.xlsx")
        d = date.fromisoformat(cal.coverage_ranges()["COFECE"][1])
        assert cal.is_covered(d, "COFECE")
        assert cal.is_business_day(d, "COFECE") == (d not in cal.cofece_holidays)
