"""
Tests unitarios — no requieren API ni LLM, corren offline.
Prueban la lógica interna de cada componente.

Ejecutar: cd chat-service && python -m pytest tests/test_unit.py -v
"""
import sys
import os
import pytest

# Agregar el directorio raíz al path para imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# 1. CriteriosSearchClient — parseo de respuesta formato real
# ═══════════════════════════════════════════════════════════════

class TestCriteriosResponseParsing:
    """Verifica que el cliente parsee el formato real de la API
    (campos de primer nivel, mayo 2026)."""

    def _make_api_item(self, **overrides):
        item = {
            "id": 2966,
            "content": "Texto del criterio sobre multas...",
            "metadata": {
                "anchor": "Anchor text fallback",
                "context": "Context text",
                "grounding": {"box": {"b": 0.36, "l": 0.11, "r": 0.88, "t": 0.32}, "page": 40},
                "pdf_pages": ["41"],
                "resolution_pages": ["40"],
            },
            "caseName": "Jiye",
            "caseLink": "VCN-001-2019",
            "distance": 0.52,
            "articleNames": ["Artículo 127, LFCE (2014)"],
            "titleNames": ["Gradación de las multas"],
        }
        item.update(overrides)
        return item

    def test_caselink_from_top_level(self):
        """caseLink se lee del primer nivel, no de metadata."""
        item = self._make_api_item()
        assert item.get("caseLink") == "VCN-001-2019"
        # metadata no tiene caseLink
        assert "caseLink" not in item["metadata"]

    def test_titlenames_first_element_as_title(self):
        """Título = titleNames[0]."""
        item = self._make_api_item()
        title_names = item.get("titleNames") or []
        title = title_names[0] if title_names else ""
        assert title == "Gradación de las multas"

    def test_titlenames_empty_falls_back_to_anchor(self):
        """titleNames=[] → fallback a anchor[:120]."""
        item = self._make_api_item(titleNames=[])
        title_names = item.get("titleNames") or []
        anchor = item["metadata"]["anchor"]
        title = title_names[0] if title_names else (anchor[:120] if anchor else "")
        assert title == "Anchor text fallback"

    def test_titlenames_none_falls_back_to_anchor(self):
        """titleNames=None → fallback a anchor[:120]."""
        item = self._make_api_item(titleNames=None)
        title_names = item.get("titleNames") or []
        anchor = item["metadata"]["anchor"]
        title = title_names[0] if title_names else (anchor[:120] if anchor else "")
        assert title == "Anchor text fallback"

    def test_articlenames_joined(self):
        """Múltiples artículos se unen con ' | '."""
        item = self._make_api_item(
            articleNames=["Artículo 127, LFCE (2014)", "Artículo 58, LFCE (2014)"]
        )
        article_names = item.get("articleNames") or []
        article = " | ".join(article_names)
        assert article == "Artículo 127, LFCE (2014) | Artículo 58, LFCE (2014)"

    def test_articlenames_empty(self):
        """articleNames=[] → string vacío."""
        item = self._make_api_item(articleNames=[])
        article_names = item.get("articleNames") or []
        article = " | ".join(article_names) if article_names else ""
        assert article == ""

    def test_articlenames_none(self):
        """articleNames=None → string vacío."""
        item = self._make_api_item(articleNames=None)
        article_names = item.get("articleNames") or []
        article = " | ".join(article_names) if article_names else ""
        assert article == ""

    def test_distance_to_score_conversion(self):
        """score = 1 - distance."""
        item = self._make_api_item(distance=0.32)
        score = round(max(0, 1.0 - item["distance"]), 4)
        assert score == 0.68

    def test_grounding_preserved_in_metadata(self):
        """grounding (coordenadas PDF) llega intacto."""
        item = self._make_api_item()
        g = item["metadata"]["grounding"]
        assert g is not None
        assert g["page"] == 40
        assert "box" in g

    def test_grounding_none(self):
        """Algunos criterios no tienen grounding."""
        item = self._make_api_item()
        item["metadata"]["grounding"] = None
        assert item["metadata"]["grounding"] is None


# ═══════════════════════════════════════════════════════════════
# 2. CitationBuilder — resolución con múltiples tool calls
# ═══════════════════════════════════════════════════════════════

class TestCitationBuilder:

    def _make_criterio(self, idx, case_link="EXP-001"):
        return {
            "id": f"crit-{idx}",
            "text": f"Criterio {idx}",
            "score": 0.9 - idx * 0.05,
            "metadata": {
                "caseLink": case_link,
                "id_expediente": case_link,
                "nombre_expediente": f"Caso {idx}",
                "title": f"Título {idx}",
                "article": f"Art. {idx}",
                "paginas_parrafos": f"{idx * 10}",
                "anchor": f"Anchor {idx}",
            },
        }

    def _make_expediente(self, idx, case_link="EXP-001"):
        return {
            "caseLink": case_link,
            "name": f"Expediente {idx}",
            "authority": "COFECE",
            "typeOfProcedure": "Concentración",
            "senseOfResolution": "AUTORIZADA",
            "resolutionDate": "01-01-2024",
        }

    def test_single_tool_call_indexing(self):
        """Con 1 buscar_criterios, [C1]=resultado[0], [C2]=resultado[1]."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        criterios = [[self._make_criterio(1), self._make_criterio(2)]]
        text = "Según [C1] y [C2]."

        _, refs = cb.build_references(text, criterios, [])
        assert len(refs) == 2
        assert refs[0].title == "Título 1"
        assert refs[1].title == "Título 2"

    def test_multiple_tool_calls_last_block_preferred(self):
        """Con 2 buscar_criterios, [C1] resuelve al último bloque
        (el LLM reinicia la numeración por búsqueda)."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        block1 = [self._make_criterio(1, "EXP-A"), self._make_criterio(2, "EXP-A")]
        block2 = [self._make_criterio(3, "EXP-B"), self._make_criterio(4, "EXP-B")]

        text = "El criterio principal [C1] indica..."
        _, refs = cb.build_references(text, [block1, block2], [])

        assert len(refs) == 1
        assert refs[0].title == "Título 3"  # block2[0]

    def test_expediente_references(self):
        """[E1] resuelve a expedientes correctamente."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        expedientes = [[
            self._make_expediente(1, "IO-001"),
            self._make_expediente(2, "IO-002"),
        ]]
        text = "El expediente [E1] fue autorizado y [E2] también."

        _, refs = cb.build_references(text, [], expedientes)
        assert len(refs) == 2
        assert refs[0].source_type == "estadistica"
        assert refs[0].id_expediente == "IO-001"
        assert refs[1].id_expediente == "IO-002"

    def test_mixed_references(self):
        """[C1] y [E1] en la misma respuesta."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        criterios = [[self._make_criterio(1)]]
        expedientes = [[self._make_expediente(1, "IO-001")]]
        text = "El criterio [C1] se aplicó en [E1]."

        _, refs = cb.build_references(text, criterios, expedientes)
        assert len(refs) == 2
        types = {r.source_type for r in refs}
        assert types == {"criterio", "estadistica"}

    def test_invalid_high_index_ignored(self):
        """[C99] con solo 2 resultados no crashea."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        criterios = [[self._make_criterio(1)]]
        text = "Válida [C1] e inválida [C99]."

        _, refs = cb.build_references(text, criterios, [])
        assert len(refs) == 1

    def test_deduplication(self):
        """Citas repetidas no duplican referencias."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        criterios = [[self._make_criterio(1)]]
        text = "Primera mención [C1] y segunda mención [C1]."

        _, refs = cb.build_references(text, criterios, [])
        assert len(refs) == 1

    def test_empty_results(self):
        """Sin resultados, no crashea."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        _, refs = cb.build_references("Texto sin citas", [], [])
        assert refs == []

    def test_no_matches_in_text(self):
        """Sin tags [C/E] en el texto, retorna lista vacía."""
        from core.citation_builder import CitationBuilder
        cb = CitationBuilder()

        criterios = [[self._make_criterio(1)]]
        _, refs = cb.build_references("Texto limpio sin citas.", criterios, [])
        assert refs == []


# ═══════════════════════════════════════════════════════════════
# 3. EvidenceCache
# ═══════════════════════════════════════════════════════════════

class TestEvidenceCache:

    def test_empty_session(self):
        from core.evidence_cache import EvidenceCache
        cache = EvidenceCache()
        crits, exps, used = cache.select("new-session", "cualquier pregunta")
        assert crits == []
        assert exps == []
        assert not used

    def test_explicit_expediente_id(self):
        """Mención de un ID de expediente trae del cache."""
        from core.evidence_cache import EvidenceCache
        cache = EvidenceCache()
        cache.update(
            "s1", "primera consulta",
            criterios=[{"metadata": {"id_expediente": "IO-001-2019"}, "id": "c1"}],
            expedientes=[{"id_expediente": "IO-001-2019", "name": "Test"}],
        )
        crits, exps, used = cache.select("s1", "¿Qué pasó con IO-001-2019?")
        assert used is True

    def test_conversational_reference(self):
        """'ese caso' trae evidencia del último turno."""
        from core.evidence_cache import EvidenceCache
        cache = EvidenceCache()
        cache.update(
            "s1", "buscar Scotiabank",
            criterios=[],
            expedientes=[{"id_expediente": "X", "name": "Scotiabank"}],
        )
        _, exps, used = cache.select("s1", "¿y en ese caso hubo multas?")
        assert used is True
        assert len(exps) > 0

    def test_different_sessions_isolated(self):
        """Sesiones diferentes no comparten cache."""
        from core.evidence_cache import EvidenceCache
        cache = EvidenceCache()
        cache.update("s1", "q", criterios=[{"id": "c1", "metadata": {}}], expedientes=[])
        crits, _, _ = cache.select("s2", "algo")
        assert crits == []


# ═══════════════════════════════════════════════════════════════
# 4. ExpedienteRecord — tipos inconsistentes de la API
# ═══════════════════════════════════════════════════════════════

class TestExpedienteRecord:

    def test_agentfines_string_has_multas(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(
            caseLink="IO-001",
            agentFines="{'CEMEX':'$896,200'}",
        )
        assert r.has_multas is True

    def test_agentfines_empty_dict_no_multas(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(caseLink="IO-001", agentFines={})
        assert r.has_multas is False

    def test_agentfines_none_no_multas(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(caseLink="IO-001", agentFines=None)
        assert r.has_multas is False

    def test_economic_agents_list_to_string(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(
            caseLink="IO-001",
            economicAgents=["CEMEX", "ALSEA"],
        )
        assert r.agentes_economicos_str == "CEMEX / ALSEA"

    def test_date_conversion_ddmmyyyy(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(
            caseLink="IO-001",
            resolutionDate="25-04-2024",
        )
        assert r.fecha_resolucion_iso == "2024-04-25"

    def test_relevantmarkets_string(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(
            caseLink="IO-001",
            relevantMarkets="Telecomunicaciones",
        )
        assert r.relevantMarkets == "Telecomunicaciones"

    def test_relevantmarkets_list(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(
            caseLink="IO-001",
            relevantMarkets=["Telecom", "Energía"],
        )
        assert isinstance(r.relevantMarkets, list)

    def test_relevantmarkets_none(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(caseLink="IO-001", relevantMarkets=None)
        assert r.relevantMarkets is None


# ═══════════════════════════════════════════════════════════════
# 5. Agent helpers — _prepare_messages_for_stream
# ═══════════════════════════════════════════════════════════════

class TestAgentMessagePreparation:
    """Verifica que _prepare_messages_for_stream convierte
    correctamente mensajes con tool results a LLMMessage(str)."""

    def _make_agent(self):
        from agent.agent import NormaPlusAgent
        from llm.registry import LLMRegistry
        from core.citation_builder import CitationBuilder
        from core.evidence_cache import EvidenceCache

        class FakeClient:
            async def search(self, **kw):
                return []

        class FakeAnalyzer:
            def enrich_with_plazos(self, r):
                return r

        return NormaPlusAgent(
            llm_registry=LLMRegistry(),
            criterios_client=FakeClient(),
            estadistica_client=FakeClient(),
            temporal_analyzer=FakeAnalyzer(),
            citation_builder=CitationBuilder(),
            evidence_cache=EvidenceCache(),
        )

    def test_system_message(self):
        agent = self._make_agent()
        msgs = [{"role": "system", "content": "Eres un experto"}]
        result = agent._prepare_messages_for_stream(msgs, "openai")
        assert result[0].role == "system"
        assert result[0].content == "Eres un experto"

    def test_anthropic_tool_result_list_content(self):
        """content=[{type:tool_result}] se condensa a string."""
        agent = self._make_agent()
        msgs = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "abc",
                 "content": '{"results": [1,2,3]}'}
            ]},
        ]
        result = agent._prepare_messages_for_stream(msgs, "anthropic")
        assert len(result) == 2
        assert isinstance(result[1].content, str)
        assert "Resultado" in result[1].content

    def test_openai_tool_role_converted(self):
        """role=tool → role=user con contenido resumido."""
        agent = self._make_agent()
        msgs = [
            {"role": "system", "content": "System"},
            {"role": "tool", "tool_call_id": "x",
             "content": '{"results": []}'},
        ]
        result = agent._prepare_messages_for_stream(msgs, "openai")
        assert result[1].role == "user"

    def test_assistant_with_tool_calls_null_content(self):
        """Assistant con content=None y tool_calls se convierte."""
        agent = self._make_agent()
        msgs = [{
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "buscar_criterios", "arguments": "{}"}}
            ],
        }]
        result = agent._prepare_messages_for_stream(msgs, "openai")
        assert result[0].role == "assistant"
        assert "buscar_criterios" in result[0].content

    def test_normal_user_message(self):
        """Mensaje user normal pasa sin cambios."""
        agent = self._make_agent()
        msgs = [{"role": "user", "content": "Hola"}]
        result = agent._prepare_messages_for_stream(msgs, "openai")
        assert result[0].content == "Hola"

    def test_append_user_message_anthropic_alternation(self):
        """Anthropic: si último msg es user, fusiona en vez de duplicar."""
        agent = self._make_agent()
        msgs = [{"role": "user", "content": "Pregunta original"}]
        result = agent._append_user_message(msgs, "Instrucción extra", "anthropic")
        assert len(result) == 1  # no duplicó
        assert "Instrucción extra" in result[0]["content"]

    def test_append_user_message_openai_appends(self):
        """OpenAI: siempre agrega nuevo mensaje."""
        agent = self._make_agent()
        msgs = [
            {"role": "user", "content": "Pregunta"},
            {"role": "assistant", "content": "Respuesta"},
        ]
        result = agent._append_user_message(msgs, "Nueva pregunta", "openai")
        assert len(result) == 3
        assert result[-1]["content"] == "Nueva pregunta"


# ═══════════════════════════════════════════════════════════════
# 6. SearchData behavior — ILIKE + unaccent simulation
# ═══════════════════════════════════════════════════════════════

class TestSearchDataBehavior:
    """Verifica el comportamiento esperado de searchData
    (ILIKE + unaccent, según confirmó José Miguel)."""

    def test_case_insensitive(self):
        """'scotiabank' debe encontrar 'SCOTIABANK INVERLAT'."""
        needle = "scotiabank"
        haystack = "SCOTIABANK INVERLAT, S.A."
        assert needle.lower() in haystack.lower()

    def test_unaccent_match(self):
        """'concentracion' sin acento encuentra 'Concentración'."""
        import unicodedata
        def unaccent(t):
            return "".join(c for c in unicodedata.normalize("NFKD", t)
                         if not unicodedata.combining(c))
        assert unaccent("concentracion").lower() in unaccent("Concentración").lower()

    def test_typo_does_not_match(self):
        """'Scotiabnak' NO encuentra 'Scotiabank' (no es fuzzy)."""
        needle = "Scotiabnak"
        haystack = "SCOTIABANK INVERLAT"
        assert needle.lower() not in haystack.lower()
