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
