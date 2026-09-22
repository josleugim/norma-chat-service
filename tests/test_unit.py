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


class TestSentidoDeResolucionArreglo:
    """
    El 19-sep-2026 la API cambió `senseOfResolution` de string a arreglo.

    El modelo lo declaraba como `str`, así que Pydantic rechazaba el registro
    COMPLETO y `estadistica_client` lo descartaba: el censo cargó 182
    expedientes de 4,696 y **cero VCN**. En el chat eso no se ve como un error,
    se ve como que el expediente no existe.

    Estas pruebas fijan las dos mitades del arreglo: que el registro
    sobreviva, y que varios sentidos no se aplanen en una sola cadena.
    """

    def test_arreglo_no_descarta_el_expediente(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(caseLink="VCN-004-2024",
                             senseOfResolution=["Sanciona"])
        assert r.caseLink == "VCN-004-2024"
        assert r.senseOfResolution == ["Sanciona"]

    def test_string_suelto_sigue_funcionando(self):
        """El vocabulario anterior no se rompe: se envuelve en lista."""
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(caseLink="CNT-001-2020",
                             senseOfResolution="AUTORIZADA")
        assert r.senseOfResolution == ["AUTORIZADA"]

    def test_varios_sentidos_se_conservan(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(caseLink="X-1", senseOfResolution=["sobresee", "niega"])
        assert r.senseOfResolution == ["sobresee", "niega"]

    def test_vacios_y_nulos(self):
        from models.schemas import ExpedienteRecord
        assert ExpedienteRecord(caseLink="X-1").senseOfResolution is None
        assert ExpedienteRecord(caseLink="X-1",
                                senseOfResolution=[]).senseOfResolution is None
        assert ExpedienteRecord(caseLink="X-1",
                                senseOfResolution=["", "  "]).senseOfResolution is None

    def test_match_por_elemento_no_aplana(self):
        """
        Aplanar `["sobresee", "niega"]` a una cadena metería la negación de
        un elemento en el otro. Es el error de §16: "NO SE ACREDITÓ
        INCUMPLIMIENTO" y "SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO" comparten
        casi todas las palabras y significan lo contrario.
        """
        from agent.agent import _coincide_campo, _normalizar
        assert _coincide_campo(["sobresee", "niega"], _normalizar("sobresee"))
        assert _coincide_campo(["sobresee", "niega"], _normalizar("niega"))
        assert not _coincide_campo(["sobresee", "niega"], _normalizar("autorizada"))

    def test_la_negacion_sigue_separando_sentidos_opuestos(self):
        from agent.agent import _coincide_campo, _normalizar
        acreditado = ["SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO"]
        assert not _coincide_campo(
            acreditado, _normalizar("NO SE ACREDITÓ INCUMPLIMIENTO")
        )

    def test_sentido_texto_para_mostrar(self):
        from models.schemas import sentido_texto
        assert sentido_texto(["sobresee", "niega"]) == "sobresee; niega"
        assert sentido_texto("AUTORIZADA") == "AUTORIZADA"
        assert sentido_texto(None) == ""


class TestNegacionAntesDeContencion:
    """
    La guarda de negación de `_coincide` estaba DESPUÉS del chequeo de
    contención, así que no servía para el par que la motivó: "sanciona" es
    subcadena de "no sanciona", `valor in objetivo` retornaba True y la guarda
    quedaba como código muerto.

    Medido en q17 el 19-sep-2026: pedir sentido "No sanciona" sobre los 36 VCN
    de COFECE descartaba 0 y devolvía los 36, incluidos los 33 que sí fueron
    sancionados. El filtro determinista no filtraba nada y el modelo tenía que
    hacerlo leyendo el contexto — que es justo la máquina de falsa certeza que
    estos filtros existen para eliminar.
    """

    def test_sanciona_no_es_no_sanciona(self):
        from agent.agent import _coincide_campo, _normalizar
        assert not _coincide_campo(["Sanciona"], _normalizar("No sanciona"))

    def test_no_sanciona_si_es_no_sanciona(self):
        from agent.agent import _coincide_campo, _normalizar
        assert _coincide_campo(["No sanciona"], _normalizar("No sanciona"))

    def test_sanciona_sigue_coincidiendo_consigo_mismo(self):
        from agent.agent import _coincide_campo, _normalizar
        assert _coincide_campo(["Sanciona"], _normalizar("Sanciona"))

    def test_el_par_de_la_ronda_v1_4(self):
        """El caso original: acreditación contra NO acreditación."""
        from agent.agent import _coincide, _normalizar
        assert not _coincide(
            _normalizar("SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO"),
            _normalizar("NO SE ACREDITÓ INCUMPLIMIENTO"),
        )

    def test_variante_tolerante_sigue_funcionando(self):
        """Lo que el matcher tolerante sí debe unir: misma polaridad."""
        from agent.agent import _coincide, _normalizar
        assert _coincide(
            _normalizar("NO SE ACREDITÓ INCUMPLIMIENTO"),
            _normalizar("NO ACREDITADO EL INCUMPLIMIENTO"),
        )


class TestClasificacionDeFuente:
    """
    Medido el 19-sep-2026: el top-60 del índice de criterios para q10
    ("¿cuáles son los criterios que usa la COFECE para determinar el monto de
    las multas?") trae 43 párrafos de resoluciones VCN y **17 de sentencias
    judiciales**, de 9 expedientes entre juzgados de distrito y tribunales
    colegiados. El agente los citaba juntos, así que criterios de un juez
    federal revisando a la COFECE salían como criterios de la COFECE.

    Es el fix 2 de §20 otra vez: lo incorrecto no es usar la fuente, es
    atribuírsela a la Comisión.
    """

    def test_resoluciones_administrativas(self):
        from core.fuentes import clasificar_fuente, RESOLUCION
        for link in ("VCN-004-2024", "CNT-090-2025", "IO-003-2018",
                     "DE-001-2020", "CON-001-2015"):
            assert clasificar_fuente(link) == RESOLUCION, link

    def test_sentencias_judiciales(self):
        from core.fuentes import clasificar_fuente, SENTENCIA
        for link in ("1244_2017_2JD", "480_2018_2SCJN", "93_2018_2TCC",
                     "565_2023_1TCC_2025_04_24", "184_2018 1JD"):
            assert clasificar_fuente(link) == SENTENCIA, link

    def test_cumplimiento_de_amparo_sigue_siendo_resolucion(self):
        """
        `VCN-002-2023_2025_10_09` es una resolución en cumplimiento de amparo:
        la emitió la Comisión, aunque nazca de una sentencia. El prefijo manda.
        """
        from core.fuentes import clasificar_fuente, RESOLUCION
        assert clasificar_fuente("VCN-002-2023_2025_10_09") == RESOLUCION
        assert clasificar_fuente("VCN-001-2017_2019_03_14") == RESOLUCION

    def test_no_adivina(self):
        from core.fuentes import clasificar_fuente, DESCONOCIDA
        for link in ("", None, "algo-raro-sin-convencion", "XYZ-001-2020"):
            assert clasificar_fuente(link) == DESCONOCIDA, link

    def test_composicion(self):
        from core.fuentes import composicion, RESOLUCION, SENTENCIA
        c = composicion(["VCN-004-2024", "VCN-005-2020",
                         "1244_2017_2JD", "480_2018_2SCJN", ""])
        assert c[RESOLUCION] == 2
        assert c[SENTENCIA] == 2
        assert sum(c.values()) == 5

    def test_la_cita_lleva_el_tipo(self):
        from core.citation_builder import CitationBuilder
        from core.fuentes import SENTENCIA, RESOLUCION
        cb = CitationBuilder()
        # Forma real que produce criterios_client: el expediente va dentro
        # de metadata.
        doc_jud = {"metadata": {"id_expediente": "1244_2017_2JD",
                                "title": "Individualización de las multas"}}
        doc_cof = {"metadata": {"id_expediente": "VCN-004-2024",
                                "title": "Gradación de las multas"}}
        assert cb._build_criterio_ref(doc_jud, 0, set()).tipo_fuente == SENTENCIA
        assert cb._build_criterio_ref(doc_cof, 0, set()).tipo_fuente == RESOLUCION

    def test_la_cita_tambien_lo_toma_del_nivel_superior(self):
        """El documento ya serializado para el modelo trae caseLink arriba."""
        from core.citation_builder import CitationBuilder
        from core.fuentes import SENTENCIA
        cb = CitationBuilder()
        doc = {"caseLink": "480_2018_2SCJN", "metadata": {}}
        assert cb._build_criterio_ref(doc, 0, set()).tipo_fuente == SENTENCIA


class TestRuteoDeConsultaDoctrinal:
    """
    q10 —"¿cuáles son los criterios que usa la COFECE para determinar el monto
    de las multas?"— salía desde agosto como "exhaustiva sobre universo
    truncado", el último criterio de COFECE que seguía abierto.

    Eran dos heurísticas equivocándose sobre lo mismo:

    1. `classify` veía "cuáles" y la ruteaba a "recorrer el universo completo
       y agregar sin muestreo". Pero el universo de una pregunta doctrinal es
       la ley y el precedente, no la tabla de expedientes. Todas las demás
       ramas ya degradaban a MIXED ante señal de concepto; ésa no.
    2. `exhaustive_but_truncated` miraba el booleano crudo de truncamiento.
       La distinción entre topar con `top_k` (búsqueda semántica funcionando)
       y topar con el techo de un universo enumerable ya se había establecido
       para `coverage_truncated` en v1.14, pero este indicador se quedó atrás.
    """

    def test_pregunta_doctrinal_no_es_exhaustiva(self):
        from core.sufficiency import classify, MIXED
        c = classify("¿cuáles son los criterios que usa la COFECE "
                     "para determinar el monto de las multas?")
        assert c["query_type"] == MIXED
        assert c["signals"]["concepto"] and c["signals"]["lista_universo"]

    def test_enumerar_expedientes_sigue_siendo_exhaustiva(self):
        """Lo que NO debe cambiar: listar un universo cerrado."""
        from core.sufficiency import classify, EXHAUSTIVE_QUERY
        for q in ("dame la lista completa de los procedimientos VCN resueltos "
                  "por la COFECE",
                  "¿en cuáles expedientes VCN la COFECE no acreditó el "
                  "incumplimiento?",
                  "¿en qué expedientes VCN la COFECE nunca impuso una multa?"):
            assert classify(q)["query_type"] == EXHAUSTIVE_QUERY, q

    def test_top_k_no_cuenta_como_universo_truncado(self):
        from core.tracing.schema import Coverage
        cob = [Coverage(requested_limit=15, returned=15, truncated=True,
                        truncation_reason="top_k")]
        assert not any(
            c.truncated and c.truncation_reason != "top_k" for c in cob
        )

    def test_topar_con_el_techo_del_universo_si_cuenta(self):
        from core.tracing.schema import Coverage
        cob = [Coverage(requested_limit=50, returned=50, truncated=True,
                        truncation_reason="meta.total")]
        assert any(
            c.truncated and c.truncation_reason != "top_k" for c in cob
        )


class TestExpectativaDeBuscarExpedientes:
    """
    q10 aparecía como "tool esperada no llamada" porque la pregunta dice
    "multas" y eso bastaba para esperar `buscar_expedientes`.

    Verificado contra staging el 20-sep-2026: `searchData` busca en caseLink,
    name, economicAgents y relevantMarkets —donde no viven los criterios
    jurídicos— y devuelve `total: 0` para los términos de q10. El agente
    tampoco la eligió en cinco corridas teniéndola disponible. No se saltaba
    una herramienta útil: la expectativa estaba mal.
    """

    def test_doctrinal_no_espera_metadatos(self):
        from core.tracing.heuristics import expected_tools
        e = expected_tools("¿cuáles son los criterios que usa la COFECE "
                           "para determinar el monto de las multas?")
        assert "buscar_criterios" in e
        assert "buscar_expedientes" not in e

    def test_un_hecho_sobre_expedientes_si_la_espera(self):
        """q05 y q11: dicen "multa" y SÍ necesitan los metadatos."""
        from core.tracing.heuristics import expected_tools
        for q in ("¿cuál es la multa máxima impuesta en expedientes VCN y a "
                  "qué agente económico se le impuso?",
                  "¿cuál es la multa máxima que ha impuesto la COFECE?"):
            assert "buscar_expedientes" in expected_tools(q), q

    def test_doctrinal_con_superlativo_sigue_esperandola(self):
        """Si pide doctrina Y un máximo, necesita las dos."""
        from core.tracing.heuristics import expected_tools
        e = expected_tools("¿qué criterios usa la COFECE para las multas y "
                           "cuál es la multa máxima que ha impuesto?")
        assert "buscar_expedientes" in e and "buscar_criterios" in e

    def test_un_expediente_concreto_siempre_la_espera(self):
        from core.tracing.heuristics import expected_tools
        e = expected_tools("¿qué criterios aplicó la COFECE en la multa "
                           "del expediente VCN-004-2024?")
        assert "buscar_expedientes" in e


class TestUniversoRestringido:
    """
    Paso 01 del protocolo de holdout de COFECE: el alcance es configuración,
    no una instrucción por pregunta, y no vale filtrar por prefijo VCN porque
    dejaría fuera las 25 sentencias judiciales del universo.
    """

    def _u(self):
        from core.universo import UniversoRestringido
        return UniversoRestringido(
            ["VCN-001-2017", "VCN-004-2024", "1244_2017_2JD", "480_2018_2SCJN"],
            etiqueta="prueba",
        )

    def test_incluye_judiciales_no_solo_el_prefijo(self):
        u = self._u()
        assert "1244_2017_2JD" in u and "480_2018_2SCJN" in u

    def test_excluye_lo_que_no_esta(self):
        u = self._u()
        assert "CNT-090-2025" not in u
        assert "VCN-005-2018" not in u, "un VCN fuera de la lista tampoco entra"

    def test_tolerante_a_mayusculas(self):
        u = self._u()
        assert "vcn-004-2024" in u and "1244_2017_2jd" in u

    def test_filtrar_conserva_el_orden(self):
        u = self._u()
        class R:
            def __init__(s, c): s.caseLink = c
        regs = [R("CNT-090-2025"), R("VCN-004-2024"), R("CNT-001-2020"),
                R("1244_2017_2JD")]
        out = u.filtrar(regs)
        assert [r.caseLink for r in out] == ["VCN-004-2024", "1244_2017_2JD"]

    def test_archivo_vacio_revienta(self, tmp_path):
        """
        Arrancar sin restricción cuando se pidió restricción produciría una
        corrida que parece válida y mide otro universo.
        """
        import json, pytest
        from core.universo import UniversoRestringido
        p = tmp_path / "vacio.json"
        p.write_text(json.dumps([]), encoding="utf-8")
        with pytest.raises(ValueError):
            UniversoRestringido.desde_archivo(p)

    def test_carga_el_formato_del_inventario(self, tmp_path):
        import json
        from core.universo import UniversoRestringido
        p = tmp_path / "u.json"
        p.write_text(json.dumps([
            {"case_link": "VCN-001-2017", "familia": "VCN principal"},
            {"case_link": "480_2018_2SCJN", "familia": "SCJN"},
        ]), encoding="utf-8")
        u = UniversoRestringido.desde_archivo(p)
        assert len(u) == 2 and "480_2018_2SCJN" in u


class TestCamposQueLaAPIMandaYElModeloNoDeclaraba:
    """
    El holdout del 21-sep-2026 encontró la falla más cara del proyecto: la API
    devolvía 52 campos, `ExpedienteRecord` declaraba 19, y Pydantic descartaba
    los 33 restantes en silencio.

    No producía un hueco visible sino una afirmación falsa: ante "¿cuántos días
    naturales pasaron desde que se presentó la demanda del amparo 275/2023
    hasta que se admitió?", el agente respondió en las TRES repeticiones que
    sólo constaba la fecha de sentencia. La API tenía las dos fechas.

    Medido sobre el universo de 63: 57 documentos con al menos un campo
    invisible, en 36 campos distintos.
    """

    def test_las_fechas_del_amparo_275_2023(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(
            caseLink="275_2023_1JD",
            complaintFilingDate="12-07-2023",
            complaintAdmissionDate="26-07-2023",
            judgmentDate="15-07-2024",
        )
        assert r.complaintFilingDate == "12-07-2023"
        assert r.complaintAdmissionDate == "26-07-2023"
        assert r.judgmentDate == "15-07-2024"

    def test_los_votos_particulares_llegan(self):
        """"¿Hubo algún voto que discrepara?" no tenía con qué responderse."""
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(
            caseLink="VCN-003-2025",
            dissentingOpinions=["Oscar Alejandro Gómez Romero (concurrente)",
                                "Ana María Reséndiz Mora (en contra)"],
        )
        assert len(r.dissentingOpinions) == 2

    def test_el_modelo_cubre_los_campos_de_la_doc_v11(self):
        from models.schemas import ExpedienteRecord
        declarados = set(ExpedienteRecord.model_fields)
        de_la_api = {
            "id", "name", "caseLink", "resolutionFileUrl", "authority",
            "typeOfProcedure", "relevantMarkets", "originTypeOfProcedure",
            "economicAgents", "startAgreementDate", "notificationDate",
            "basicInfoRequestDate", "admissionDate", "additionalInfoRequestDate",
            "resolutionDate", "senseOfResolution", "resource", "agentFines",
            "resolutionIssueDate", "applicableLaw", "dissentingOpinions",
            "notifyingParties", "operationDescription", "natureOfResolution",
            "modifiedInitialResolutionDate", "amparoComplianceResolutionDate",
            "amparoComplianceResolutionIssueDate", "scopeOfCompliance",
            "judgmentImplementation", "accumulatedCaseFiles", "decisionOfficials",
            "originAdministrativeAuthority", "originAdministrativeResolutionDate",
            "claimedActs", "challengedNorms", "complaintFilingDate",
            "complaintAdmissionDate", "expandedComplaintAdmissionDate",
            "judgmentDate", "senseOfAmparo", "judicialDecisionEffects",
            "judicialCaseFile", "judicialBody", "reviewResolutionDate",
            "senseOfReview", "finalAmparoResult", "relatedTccCaseFile",
            "relatedCollegiateCourt", "relatedTccDecisionDate",
            "originAmparoCaseFiles", "appealedJudgmentBody",
            "appealedJudgmentDate", "principalAppellants", "adhesiveAppellants",
            "dissentingAndConcurringOpinions",
        }
        faltan = de_la_api - declarados
        assert not faltan, f"el modelo no declara: {sorted(faltan)}"


class TestAlcanceConIdentificadoresJudiciales:
    """
    `1259-1260_2017_2JD` tiene guion, pero su "prefijo" sería `1259`: el número
    de un amparo, no un tipo de procedimiento. Contarlo como scope marcaba como
    confusión de alcance una pregunta sobre un VCN que además recuperaba la
    sentencia que lo revisa — que es lo correcto cuando ambos están en el
    universo. 2 de 20 preguntas en una repetición del holdout, las dos falsas.
    """

    def _scope(self, links):
        from core.tracing.analysis import analyze_answer
        docs = [{"case_link": l} for l in links]
        a = analyze_answer(text="x", registry=None, references=[],
                           unresolved=[], docs_in_context=docs,
                           expected_prefixes=["VCN"])
        return set(a.scope_observed), a.scope_mismatch

    def test_una_sentencia_no_es_otro_alcance(self):
        obs, mismatch = self._scope(["VCN-005-2020", "1259-1260_2017_2JD"])
        assert obs == {"VCN"}
        assert not mismatch

    def test_un_procedimiento_de_verdad_si_lo_es(self):
        obs, mismatch = self._scope(["VCN-005-2020", "CNT-090-2025"])
        assert obs == {"VCN", "CNT"}
        assert mismatch


class TestMarcadorEnLosPlazos:
    """
    El holdout dejó ver que una respuesta puede ser correcta y aun así romper
    la trazabilidad. En H05 el agente calculó bien los días naturales y nombró
    la sentencia en FUENTES, pero no escribió marcador en el cuerpo:
    `citations_emitted` quedó en 0 y la cadena afirmación → marcador →
    registro → documento se cortaba.

    El registro ya tenía el expediente; lo que faltaba era que la salida de
    `calcular_plazos` lo trajera.
    """

    def test_el_marcador_se_reusa_no_se_duplica(self):
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        doc = {"caseLink": "275_2023_1JD", "judgmentDate": "15-07-2024"}
        primero = reg.assign(doc, "E")
        # El mismo expediente, llegando por otra herramienta.
        otra_vista = {"caseLink": "275_2023_1JD", "dias_naturales": 355}
        segundo = reg.assign(otra_vista, "E")
        assert primero == segundo, "un expediente debe tener una sola identidad"

    def test_dos_expedientes_distintos_llevan_marcadores_distintos(self):
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        a = reg.assign({"caseLink": "275_2023_1JD"}, "E")
        b = reg.assign({"caseLink": "43_2021_3JD"}, "E")
        assert a != b

    def test_el_marcador_resuelve_al_expediente(self):
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        m = reg.assign({"caseLink": "275_2023_1JD"}, "E")
        assert reg.case_link_of(m) == "275_2023_1JD"


class TestResolucionDeIdentidades:
    """
    C02 del diagnóstico de COFECE. "El amparo en revisión 677/2024" viajaba
    intacto como texto libre a una búsqueda léxica y devolvía cero en ocho de
    nueve corridas. En la novena el modelo eligió por su cuenta
    `677_2024_1SCJN` y lo encontró: el acierto dependía de que adivinara el
    identificador interno.

    Las pruebas de cierre que pide el diagnóstico: pares equivalentes resuelven
    los mismos registros, un número compartido por órganos distintos no se
    fusiona, y dos actos de un expediente conservan IDs distintos.
    """

    UNIVERSO = [
        "VCN-004-2024", "677_2024_1SCJN", "480_2018_2SCJN",
        "275_2023_1JD", "275_2023_3JD", "178_2017_2TCC",
        "278_2023_1JD_2024_07_15", "278_2023_1JD_2025_11_19",
        "1259-1260_2017_2JD",
    ]

    def _r(self):
        from core.identidades import ResolutorDeIdentidades
        return ResolutorDeIdentidades(self.UNIVERSO)

    def test_el_numero_natural_resuelve_al_identificador_interno(self):
        r = self._r().resolver("En el amparo en revisión 677/2024, ¿la Primera "
                               "Sala resolvió todos los agravios?")
        assert len(r) == 1
        assert r[0]["candidatos"] == ["677_2024_1SCJN"]
        assert not r[0]["ambiguo"]

    def test_numero_compartido_por_dos_organos_no_se_fusiona(self):
        r = self._r().resolver("En el amparo 275/2023, ¿qué se resolvió?")
        assert r[0]["ambiguo"]
        assert set(r[0]["candidatos"]) == {"275_2023_1JD", "275_2023_3JD"}

    def test_el_organo_desambigua(self):
        r = self._r().resolver("En el amparo 275/2023 del Juzgado Primero de "
                               "Distrito, ¿cuántos días naturales pasaron?")
        assert r[0]["candidatos"] == ["275_2023_1JD"]
        assert not r[0]["ambiguo"]

    def test_dos_actos_del_mismo_expediente_se_conservan(self):
        r = self._r().resolver("el amparo 278/2023 del Juzgado Primero")
        assert set(r[0]["candidatos"]) == {
            "278_2023_1JD_2024_07_15", "278_2023_1JD_2025_11_19"}
        assert r[0]["ambiguo"], "dos actos distintos no se colapsan en uno"

    def test_no_fabrica_identificadores(self):
        """Lo que no existe en el universo no se inventa por concatenación."""
        assert self._r().resolver("el amparo 999/1999") == []

    def test_numero_con_acumulados(self):
        r = self._r().resolver("el amparo 1259-1260/2017")
        assert r[0]["candidatos"] == ["1259-1260_2017_2JD"]

    def test_partes_de_un_identificador_judicial(self):
        from core.identidades import partes_de
        p = partes_de("565_2023_1TCC_2025_04_24")
        assert p["numero"] == "565" and p["anio"] == "2023"
        assert p["marca_organo"] == "TCC" and p["ordinal_organo"] == "1"
        assert p["acto"] == "2025_04_24"

    def test_un_expediente_administrativo_no_es_judicial(self):
        from core.identidades import partes_de
        assert partes_de("VCN-004-2024") is None


class TestContratoDeCalculo:
    """
    C07 del diagnóstico de COFECE. Tres defectos distintos en el mismo
    contrato:

    1. `CitationRegistry.assign` no rechazaba identidad vacía: cinco objetos de
       fechas sin `caseLink` colapsaban bajo un mismo `E6` que no resolvía a
       ningún documento.
    2. Las estadísticas usaban siempre días hábiles. Una pregunta por el
       promedio en días NATURALES recibía el de hábiles sin advertencia: la
       cifra era correcta para otra pregunta.
    3. El enum de campos no incluía los judiciales, así que un plazo de amparo
       sólo podía calcularse con fechas sueltas — rama que pierde la
       procedencia del expediente.
    """

    def test_identidad_vacia_no_recibe_marcador(self):
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        assert reg.assign({"fecha_inicio": "01-01-2024"}, "E") == ""
        assert reg.assign({"dias_naturales": 355}, "E") == ""
        assert reg.markers() == []

    def test_objetos_sin_identidad_no_colapsan_en_uno(self):
        """El defecto exacto: cinco registros distintos bajo un solo E6."""
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        marcadores = [
            reg.assign({"fecha_inicio": f"0{i}-01-2024"}, "E") for i in range(1, 6)
        ]
        assert marcadores == ["", "", "", "", ""]
        assert reg.markers() == [], "ninguno debe quedar registrado"

    def test_con_identidad_si_recibe_marcador(self):
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        m = reg.assign({"caseLink": "VCN-004-2024", "dias_naturales": 49}, "E")
        assert m == "E1"
        assert reg.case_link_of(m) == "VCN-004-2024"

    def test_la_unidad_es_explicita_en_la_herramienta(self):
        import agent.tools as t
        tool = next(
            d for grp in vars(t).values()
            if isinstance(grp, list) and grp and isinstance(grp[0], dict)
            for d in grp
            if (d.get("function", d)).get("name") == "calcular_plazos"
        )
        props = tool.get("function", tool)["parameters"]["properties"]
        assert props["unidad"]["enum"] == ["dias_habiles", "dias_naturales"]

    def test_los_campos_judiciales_estan_en_el_enum(self):
        import agent.tools as t
        tool = next(
            d for grp in vars(t).values()
            if isinstance(grp, list) and grp and isinstance(grp[0], dict)
            for d in grp
            if (d.get("function", d)).get("name") == "calcular_plazos"
        )
        props = tool.get("function", tool)["parameters"]["properties"]
        for campo in ("complaintFilingDate", "complaintAdmissionDate",
                      "judgmentDate"):
            assert campo in props["campo_inicio"]["enum"], campo
        assert "judgmentDate" in props["campo_fin"]["enum"]


class TestVozDelCriterio:
    """
    C05 del diagnóstico de COFECE. Ante "¿qué sostuvo el tribunal en el
    353/2024?" el agente presentó como postura MAYORITARIA el criterio 8422,
    que es el voto particular de la Magistrada Irma Leticia Flores Díaz, e
    invirtió lo que sostenían mayoría y disidencia.

    La API no expone la voz en campo propio (verificado el 21-sep-2026); el
    rastro está en `metadata.context`.
    """

    VOTO = {
        "content": "los elementos del 130 no resultan aplicables en su totalidad",
        "metadata": {"context": (
            "…cuáles no.” Magistrada Irma Leticia Flores Díaz. "
            "Respetuosamente, formulo voto en contra, en atención a que…")},
    }
    SALVEDAD = {
        "content": "me aparto de las consideraciones",
        "metadata": {"context": (
            "SALVEDADES QUE FORMULA EL MAGISTRADO FRANCISCO GARCÍA SANDOVAL, "
            "EN EL EXPEDIENTE R.A. 353/2024.")},
    }
    SENTENCIA = {
        "content": "la autoridad debe valorar la totalidad de los elementos",
        "metadata": {"context": "<<<PAGINA:88>>> En consecuencia, procede…"},
    }

    def test_identifica_el_voto_particular_y_su_autora(self):
        from core.voz import clasificar_voz, VOTO_PARTICULAR
        v = clasificar_voz(self.VOTO)
        assert v["voz"] == VOTO_PARTICULAR
        assert v["autor"] == "Irma Leticia Flores Díaz"
        assert v["evidencia"], "la clasificación debe ser auditable"

    def test_identifica_las_salvedades(self):
        from core.voz import clasificar_voz, VOTO_PARTICULAR
        v = clasificar_voz(self.SALVEDAD)
        assert v["voz"] == VOTO_PARTICULAR
        assert "FRANCISCO GARCÍA SANDOVAL" in (v["autor"] or "")

    def test_sin_marca_NO_se_concluye_mayoria(self):
        """
        La regla central: que el documento sea una sentencia no dice quién
        habla en ese fragmento. Deducir "mayoría" es el error a impedir.
        """
        from core.voz import clasificar_voz, NO_IDENTIFICADA
        v = clasificar_voz(self.SENTENCIA)
        assert v["voz"] == NO_IDENTIFICADA
        assert v["autor"] is None

    def test_un_voto_sin_firma_no_produce_nombre(self):
        from core.voz import clasificar_voz, VOTO_PARTICULAR
        v = clasificar_voz({"content": "formulo voto particular en contra",
                            "metadata": {}})
        assert v["voz"] == VOTO_PARTICULAR
        assert v["autor"] is None, "sin firma legible no se inventa autor"

    def test_cambiar_el_autor_cambia_la_salida(self):
        """No puede quedar fijado al ejemplo del holdout."""
        from core.voz import clasificar_voz
        otro = {"content": "x", "metadata": {"context":
                "Magistrada Ana Pérez López. Respetuosamente, formulo voto…"}}
        assert clasificar_voz(otro)["autor"] == "Ana Pérez López"


class TestContinuidadDeCitasEntreTurnos:
    """
    C04. En H19 `[C14]` era una cita válida a `511_2023_2TCC`. H20 la
    reutilizó, pero su registro sólo llegaba a C10: quedó inválida aunque el
    documento fuera real. La causa era que el resumen de caché usaba índices
    posicionales, no los marcadores del registro del turno.
    """

    def test_la_evidencia_previa_se_registra_en_el_turno_actual(self):
        from core.citations import CitationRegistry
        from core.evidence_cache import EvidenceCache
        cache = EvidenceCache()
        crit = {"id": "8496", "text": "criterio sobre individualización",
                "metadata": {"id_expediente": "511_2023_2TCC",
                             "title": "Individualización", "paginas_parrafos": "89, 90, 91"}}
        cache.update("s1", "pregunta previa", [crit], [])
        reg = CitationRegistry()
        ctx = cache.contexto_para_turno("s1", reg)
        assert "511_2023_2TCC" in ctx
        # El marcador del contexto tiene que existir en el registro del turno.
        import re
        marcadores = re.findall(r"\[([CE]\d+)\]", ctx)
        assert marcadores
        for m in marcadores:
            assert reg.resolve(m) is not None, f"{m} debe resolver"

    def test_el_contexto_trae_el_texto_no_solo_el_titulo(self):
        from core.citations import CitationRegistry
        from core.evidence_cache import EvidenceCache
        cache = EvidenceCache()
        cache.update("s1", "q", [{"id": "1", "text": "CONTENIDO SUSTANTIVO",
                                  "metadata": {"id_expediente": "X-1"}}], [])
        ctx = cache.contexto_para_turno("s1", CitationRegistry())
        assert "CONTENIDO SUSTANTIVO" in ctx


class TestRequisitosPorComponente:
    """
    C03. El check de suficiencia unía el texto de todos los documentos y medía
    palabras. `_terminos` usa `[a-z]{4,}`, así que los números de expediente
    desaparecen:

        _terminos("criterio del amparo 178/2017 del 2TCC") → {'amparo','criterio'}

    Una pregunta sobre un documento exacto se aprobaba con vocabulario de
    cualquier otro del mismo tema, y evidencia de UNA fuente cubría una
    consulta que pedía DOS posturas.
    """

    def _req(self, q):
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        u = ["VCN-001-2025", "178_2017_2TCC", "353_2024_1TCC"]
        return construir_requisitos(
            q, ResolutorDeIdentidades(u).resolver(q))

    def test_una_comparacion_exige_los_dos_lados(self):
        from core.requisitos import verificar
        q = ("Compara lo que sostuvo la COFECE en VCN-001-2025 con lo que "
             "sostuvo el Segundo Tribunal Colegiado en el amparo 178/2017")
        req = self._req(q)
        v = verificar(req, [{"caseLink": "VCN-001-2025", "content": "x"}])
        assert not v["cumple"]
        assert any("178_2017_2TCC" in f for f in v["faltantes"])

    def test_con_los_dos_lados_cumple(self):
        from core.requisitos import verificar
        q = ("Compara lo de VCN-001-2025 con lo del amparo 178/2017 "
             "del Segundo Tribunal Colegiado")
        v = verificar(self._req(q), [
            {"caseLink": "VCN-001-2025", "content": "a"},
            {"caseLink": "178_2017_2TCC", "content": "b"},
        ])
        assert v["cumple"]

    def test_un_voto_no_satisface_una_pregunta_por_la_mayoria(self):
        from core.requisitos import verificar
        q = ("En el amparo en revisión 353/2024 del Primer Tribunal "
             "Colegiado, ¿qué sostuvo el tribunal?")
        voto = {"caseLink": "353_2024_1TCC", "content": "x", "metadata": {
            "context": "Magistrada Irma Leticia Flores Díaz. "
                       "Respetuosamente, formulo voto"}}
        v = verificar(self._req(q), [voto])
        assert not v["cumple"]
        assert any("mayoritaria" in f for f in v["faltantes"])

    def test_cambiar_los_ids_impide_aprobar(self):
        """Prueba de cierre del diagnóstico: reemplazar todos los IDs de
        evidencia debe impedir aprobar una pregunta sobre un documento
        exacto."""
        from core.requisitos import verificar
        q = "¿Qué se resolvió en el amparo 178/2017 del Segundo Tribunal?"
        v = verificar(self._req(q), [{"caseLink": "OTRO-999-2020",
                                      "content": "mismo tema, otro documento"}])
        assert not v["cumple"]


class TestValidacionAntesDeEmitir:
    """
    C06. Las tres rutas de salida emitían tokens antes de resolver las citas.
    Cuando se descubría que un marcador no estaba en el registro, el texto ya
    había salido: la defensa existía y llegaba tarde.
    """

    class _Reg:
        def __init__(self, validos): self.validos = set(validos)
        def resolve(self, m): return {"x": 1} if m in self.validos else None

    def test_quita_el_marcador_invalido(self):
        from core.validacion_salida import validar_borrador
        r = validar_borrador(
            "La autoridad debe valorar todo [C1]. El voto discrepó [C14].",
            self._Reg(["C1"]))
        assert r["reparado"]
        assert r["marcadores_invalidos"] == ["C14"]
        assert "[C14]" not in r["texto"]
        assert "[C1]" in r["texto"]

    def test_marca_la_afirmacion_que_se_queda_sin_respaldo(self):
        from core.validacion_salida import validar_borrador
        r = validar_borrador("El voto lo emitió la magistrada X [C14].",
                             self._Reg(["C1"]))
        assert "SIN RESPALDO" in r["texto"]
        assert len(r["frases_sin_respaldo"]) == 1

    def test_un_borrador_limpio_no_se_toca(self):
        from core.validacion_salida import validar_borrador
        texto = "Todo bien [C1] y [E2]."
        r = validar_borrador(texto, self._Reg(["C1", "E2"]))
        assert not r["reparado"] and r["texto"] == texto

    def test_quita_el_renglon_de_FUENTES_correspondiente(self):
        from core.validacion_salida import validar_borrador
        r = validar_borrador(
            "Afirmación [C1] y otra [C14].\n\nFUENTES\n[C1] doc uno\n[C14] doc catorce",
            self._Reg(["C1"]))
        assert "[C14] doc catorce" not in r["texto"]
        assert "[C1] doc uno" in r["texto"]


class TestFiltroDocumentalNoAcotaSiNoIdentifica:
    """
    Regresión propia, detectada en la regresión del 21-sep. Ante una pregunta
    temática ("busca una resolución VCN que explique…") el modelo pasó
    `en_expedientes: ["VCN-"]` — un prefijo, no un identificador. El filtro lo
    tomó literalmente, devolvió cero sobre 19 criterios que sí existían, y el
    agente se abstuvo contestando por doctrina.

    Una abstención que parece prudente y en realidad es capacidad perdida: el
    caso exacto que el paso 08 de COFECE señala como "abstenerse prudentemente
    no demuestra recuperación exitosa".

    El diagnóstico ya lo advertía: la obligación de filtro se activa cuando hay
    una identidad RESUELTA, no para toda consulta semántica.
    """

    def _universo(self):
        from core.universo import UniversoRestringido
        return UniversoRestringido(
            ["VCN-001-2025", "VCN-002-2017", "178_2017_2TCC"], etiqueta="t")

    def test_un_prefijo_no_es_una_identidad(self):
        u = self._universo()
        assert "VCN-" not in u
        assert "VCN" not in u
        assert "VCN-001-2025" in u

    def test_valores_que_no_identifican_se_separan_de_los_que_si(self):
        u = self._universo()
        pedidos = ["VCN-", "VCN-001-2025", "amparos"]
        validos = [e for e in pedidos if e in u]
        descartados = [e for e in pedidos if e not in u]
        assert validos == ["VCN-001-2025"]
        assert descartados == ["VCN-", "amparos"]

    def test_la_descripcion_advierte_contra_el_prefijo(self):
        import agent.tools as t
        tool = next(
            d for grp in vars(t).values()
            if isinstance(grp, list) and grp and isinstance(grp[0], dict)
            for d in grp
            if (d.get("function", d)).get("name") == "buscar_criterios"
        )
        desc = (tool.get("function", tool)["parameters"]["properties"]
                ["en_expedientes"]["description"])
        assert "NO es un prefijo" in desc
        assert "OMITE" in desc


class TestActoDerivadoNoEsElPrincipal:
    """
    H10 de la revisión final. La pregunta pide el cumplimiento de amparo del
    9 de octubre de 2025 de VCN-004-2022, y el agente buscaba sobre el
    principal. Como el filtro de la API hace SUBSTRING —verificado el 22-sep:
    `caseLink=VCN-004-2022` devuelve 16 criterios suyos más los 14 del
    cumplimiento— la respuesta mezclaba la fórmula de incremento del acto
    original con lo preguntado sobre el cumplimiento.
    """

    U = ["VCN-004-2022", "VCN-004-2022_2025_10_09",
         "VCN-002-2023", "VCN-002-2023_2025_10_09",
         "VCN-001-2017", "VCN-001-2017_2019_03_14"]

    def _r(self):
        from core.identidades import ResolutorDeIdentidades
        return ResolutorDeIdentidades(self.U)

    def test_la_fecha_resuelve_al_acto(self):
        r = self._r().resolver(
            "En el cumplimiento de amparo del VCN-004-2022 de 9 de octubre "
            "de 2025, ¿qué fórmula de incremento se usó?")
        actos = [i for i in r if i.get("acto_de")]
        assert actos and actos[0]["candidatos"] == ["VCN-004-2022_2025_10_09"]
        assert actos[0]["acto_de"] == "VCN-004-2022"

    def test_el_principal_sin_fecha_NO_salta_al_acto(self):
        """Preguntar por el expediente original debe seguir dando el original."""
        r = self._r().resolver("En el VCN-004-2022, ¿a quién se multó?")
        assert [i for i in r if i.get("acto_de")] == []

    def test_cumplimiento_sin_fecha_con_acto_unico(self):
        r = self._r().resolver(
            "En la resolución de cumplimiento del VCN-002-2023, ¿qué alcance?")
        actos = [i for i in r if i.get("acto_de")]
        assert actos[0]["candidatos"] == ["VCN-002-2023_2025_10_09"]

    def test_formato_numerico_de_fecha(self):
        r = self._r().resolver("el cumplimiento del VCN-004-2022 de 09-10-2025")
        actos = [i for i in r if i.get("acto_de")]
        assert actos[0]["candidatos"] == ["VCN-004-2022_2025_10_09"]

    def test_una_fecha_que_no_corresponde_no_inventa_acto(self):
        r = self._r().resolver(
            "el cumplimiento del VCN-004-2022 de 1 de enero de 2030")
        assert [i for i in r if i.get("acto_de")] == []


class TestEstadisticasRespetanElFiltro:
    """
    Regresión propia, reproducida por COFECE el 22-sep. Al corregir el tope de
    50 filas se puso `data_for_stats = enriched`, que también se saltaba el
    filtro por plazo: pedir "el promedio de los que tardaron menos de 50 días"
    devolvía el promedio del universo entero. Una cifra correcta para otra
    pregunta.

    El recorte de 50 es presentación y no debe afectar el cálculo; el filtro
    por plazo es parte de lo preguntado y sí debe.
    """

    def _datos(self):
        return [
            {"caseLink": f"VCN-00{i}-2024", "dias_naturales": d,
             "dias_habiles": d, "calculable": True}
            for i, d in enumerate([49, 56, 59, 70, 83], start=1)
        ]

    def test_con_filtro_las_stats_son_del_subconjunto(self):
        datos = self._datos()
        filtrados = [d for d in datos if d["dias_naturales"] <= 50]
        assert len(filtrados) == 1
        promedio = sum(d["dias_naturales"] for d in filtrados) / len(filtrados)
        assert promedio == 49, "no el 63.4 del universo completo"

    def test_sin_filtro_las_stats_son_del_universo(self):
        datos = self._datos()
        promedio = sum(d["dias_naturales"] for d in datos) / len(datos)
        assert round(promedio, 1) == 63.4

    def test_el_recorte_visual_no_cambia_el_calculo(self):
        datos = [{"dias_naturales": 10, "calculable": True} for _ in range(51)]
        presentados = datos[:50]
        assert len(datos) == 51 and len(presentados) == 50
        assert sum(d["dias_naturales"] for d in datos) / len(datos) == 10


class TestElPayloadNoSepultaLaEvidencia:
    """
    Auditoría del 22-sep, a raíz de H10: el agente dijo no poder recuperar un
    criterio que tenía en contexto. No era el alcance ni los controles: era el
    tamaño relativo de la evidencia frente a la metadata que fuimos apilando.

    La asimetría más clara: el texto del criterio se trunca a 700 caracteres y
    `anchor` + `context` viajaban enteros con ~1,300. Se recortaba lo que
    responde la pregunta y crecía lo accesorio.
    """

    def _registro(self):
        from models.schemas import ExpedienteRecord
        return ExpedienteRecord(
            caseLink="VCN-004-2024", authority="COFECE",
            resolutionDate="21-11-2024", senseOfResolution=["Sanciona"],
            resolutionFileUrl="https://firmada.example/" + "x" * 1400,
            id=25195, hasDigitalResolution=True,
        )

    def test_la_url_firmada_no_viaja_al_prompt(self):
        """1,541 caracteres que el modelo no puede abrir."""
        d = self._registro().para_prompt()
        assert "resolutionFileUrl" not in d
        assert d["caseLink"] == "VCN-004-2024"

    def test_los_campos_nulos_no_viajan(self):
        d = self._registro().para_prompt()
        assert all(v not in (None, "", [], {}) for v in d.values())
        assert "notificationDate" not in d

    def test_conserva_lo_que_si_importa(self):
        d = self._registro().para_prompt()
        for k in ("caseLink", "authority", "resolutionDate", "senseOfResolution"):
            assert k in d, k

    def test_la_reduccion_es_sustancial(self):
        import json
        r = self._registro()
        completo = len(json.dumps(r.model_dump(), ensure_ascii=False))
        prompt = len(json.dumps(r.para_prompt(), ensure_ascii=False))
        assert prompt < completo / 2, f"{completo} → {prompt}"
