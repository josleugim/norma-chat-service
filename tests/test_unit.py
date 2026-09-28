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
        # Se retira, no se anota: un aviso pegado no deshace una aseveración.
        assert "AFIRMACIÓN RETIRADA" in r["texto"]
        assert "magistrada X" not in r["texto"]
        # Y sigue disponible para auditar.
        assert any("magistrada X" in f for f in r["frases_sin_respaldo"])
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
        """
        Sigue sin inventar acto —`candidatos` vacío— pero ya no lo hace en
        silencio. Antes la mención desaparecía, el filtro de alcance nunca se
        aplicaba y la búsqueda quedaba abierta sobre todo el universo.
        """
        r = self._r().resolver(
            "el cumplimiento del VCN-004-2022 de 1 de enero de 2030")
        actos = [i for i in r if i.get("acto_de")]
        assert len(actos) == 1
        assert actos[0]["candidatos"] == []
        assert "2030-01-01" in actos[0]["conflicto"]
        assert "2025-10-09" in actos[0]["conflicto"], (
            "el conflicto debe decir qué fechas SÍ existen")


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


class TestCamposDelRegistroComoRequisito:
    """
    H14 de la revisión final. La pregunta pide identificar el tribunal
    colegiado y su expediente; las tres corridas usaron sólo
    `buscar_criterios`. Los datos estaban en el registro —`relatedTccCaseFile:
    565/2023`, `relatedCollegiateCourt: Primer Tribunal Colegiado…`— y nadie
    los fue a buscar.

    Un identificador canónico correcto no equivale a haber recuperado todos
    los campos pedidos.
    """

    Q = ("En el amparo en revisión 677/2024, ¿la Primera Sala resolvió todos "
         "los agravios? ¿Qué tribunal colegiado y qué expediente dieron origen?")

    def _req(self, q=None):
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        u = ["677_2024_1SCJN", "VCN-001-2025", "178_2017_2TCC"]
        q = q or self.Q
        return construir_requisitos(q, ResolutorDeIdentidades(u).resolver(q))

    def test_solo_criterios_no_cumple(self):
        from core.requisitos import verificar
        v = verificar(self._req(), [
            {"caseLink": "677_2024_1SCJN", "content": "reserva de jurisdicción"}])
        assert not v["cumple"]
        assert any("tribunal" in f for f in v["faltantes"])

    def test_con_el_registro_cumple(self):
        from core.requisitos import verificar
        v = verificar(self._req(), [{
            "caseLink": "677_2024_1SCJN",
            "relatedTccCaseFile": "565/2023",
            "relatedCollegiateCourt": "Primer Tribunal Colegiado…",
        }])
        assert v["cumple"]

    def test_nombrar_un_tribunal_no_es_preguntarlo(self):
        """
        "del Segundo Tribunal Colegiado" identifica el documento; no pide que
        se diga cuál es. La primera versión del patrón disparaba con cualquier
        mención y rompía las comparaciones.
        """
        req = self._req("Compara lo de VCN-001-2025 con lo del amparo 178/2017 "
                        "del Segundo Tribunal Colegiado")
        assert not [r for r in req if r["tipo"] == "campos_registro"]

    def test_quien_voto_pide_los_campos_de_votos(self):
        """
        Dos datos distintos en una pregunta producen dos requisitos, no uno
        con todos los campos dentro: quiénes decidieron y quiénes discreparon
        se prueban por separado.
        """
        req = self._req("En el VCN-003-2025, ¿quiénes votaron y hubo "
                        "comisionados en contra?")
        porpapel = {r["papel"]: r["valor"]
                    for r in req if r["tipo"] == "campos_registro"}
        assert "decisionOfficials" in porpapel["quiénes decidieron"]
        assert "dissentingOpinions" in porpapel["votos disidentes"]


class TestTodasLasRutasDeSalidaValidan:
    """
    C06, completado. La validación estaba sólo en la rama `content`;
    `stream` y la síntesis forzada emitían token por token y resolvían las
    citas al final, cuando el texto ya había salido.

    Las 180 respuestas del holdout usaron `content`: eso no probaba que las
    otras rutas estuvieran protegidas, sólo que no se habían ejercido.
    """

    class _Agente:
        from core.validacion_salida import validar_borrador as _vb
        _emitir_validado = None  # se enlaza abajo

    class _Reg:
        def __init__(self, validos): self.validos = set(validos)
        def resolve(self, m): return {"x": 1} if m in self.validos else None

    class _State:
        def __init__(self, reg): self.registry = reg; self.reparacion_salida = None

    def _agente(self):
        from agent.agent import NormaPlusAgent
        return NormaPlusAgent.__new__(NormaPlusAgent)

    def test_las_cuatro_rutas_usan_el_mismo_validador(self):
        """Ninguna ruta puede emitir sin pasar por aquí."""
        import inspect
        from agent.agent import NormaPlusAgent
        src = inspect.getsource(NormaPlusAgent._run_traced)
        assert src.count("_emitir_validado") == 4, (
            "content, stream, forced_synthesis y forced_synthesis_stream")

    def test_repara_y_registra_la_ruta(self):
        ag = self._agente()
        st = self._State(self._Reg(["C1"]))
        import asyncio
        texto = asyncio.run(ag._emitir_validado(
            "Afirmación buena [C1]. Afirmación colgada [C14].",
            st, None, "stream"))
        assert "[C14]" not in texto
        assert st.reparacion_salida["ruta"] == "stream"
        assert st.reparacion_salida["marcadores_invalidos"] == ["C14"]

    def test_un_borrador_limpio_pasa_intacto(self):
        ag = self._agente()
        st = self._State(self._Reg(["C1", "E2"]))
        texto = "Todo sustentado [C1] y [E2]."
        import asyncio
        assert asyncio.run(
            ag._emitir_validado(texto, st, None, "content")) == texto
        assert st.reparacion_salida is None

    def test_sin_registro_no_revienta(self):
        ag = self._agente()
        import asyncio
        assert asyncio.run(
            ag._emitir_validado("texto [C1]", None, None, "content"))


class TestEmisorDelDocumento:
    """
    Correcciones menores de la revisión final, todas con la misma raíz: un
    criterio no dice qué órgano lo dictó, así que el agente lo infería.

    H17-B no identificó al Juzgado Tercero como emisor del criterio del
    43/2021. H18-B confundió a COFECE —que dictó el acto de 2023— con CNA, que
    era la destinataria del cumplimiento.
    """

    def _u(self):
        from core.universo import UniversoRestringido
        return UniversoRestringido(
            ["43_2021_3JD", "VCN-004-2024", "677_2024_1SCJN", "275_2023_1JD"],
            etiqueta="t")

    def test_carga_los_emisores_del_universo(self):
        u = self._u()
        n = u.cargar_emisores([
            {"caseLink": "43_2021_3JD", "authority": "Juzgado Tercero de Distrito"},
            {"caseLink": "VCN-004-2024", "authority": "COFECE"},
            {"caseLink": "FUERA-001-2020", "authority": "OTRA"},
        ])
        assert n == 2, "lo que no está en el universo no se carga"
        assert u.emisor_de("43_2021_3JD") == "Juzgado Tercero de Distrito"
        assert u.emisor_de("VCN-004-2024") == "COFECE"

    def test_sin_authority_usa_el_organo_judicial(self):
        u = self._u()
        u.cargar_emisores([{"caseLink": "677_2024_1SCJN",
                            "judicialBody": "Primera Sala de la SCJN"}])
        assert u.emisor_de("677_2024_1SCJN") == "Primera Sala de la SCJN"

    def test_un_documento_sin_emisor_devuelve_None(self):
        """No se inventa: seis de los 63 no traen authority."""
        u = self._u()
        u.cargar_emisores([{"caseLink": "275_2023_1JD"}])
        assert u.emisor_de("275_2023_1JD") is None

    def test_la_autoridad_deja_de_estar_no_disponible_si_hay_emisor(self):
        """
        `campos_no_disponibles` existe para que el modelo no invente. Si el
        emisor SÍ se conoce, declararlo no disponible sería mentir al revés.
        """
        emisor = "Juzgado Tercero de Distrito"
        campos = [c for c in ("autoridad", "sentido_resolucion",
                              "fecha_resolucion")
                  if not (c == "autoridad" and emisor)]
        assert "autoridad" not in campos
        assert "sentido_resolucion" in campos


class TestReconciliacionDelConjuntoCalculado:
    """
    C07, lo que quedaba abierto. La calculadora aceptaba el subconjunto que el
    modelo mandara sin compararlo con la búsqueda previa. COFECE lo reprodujo:
    enviar cuatro de los cinco registros daba count=4 y promedio 64.5 —en vez
    de 5 y 63.4— sin que nada justificara la exclusión.

    Una cifra sobre un subconjunto silencioso es peor que un error: se ve bien
    calculada.
    """

    def _cinco(self):
        return [{"caseLink": f"VCN-00{i}-2024", "resolutionDate": "01-01-2024",
                 "startAgreementDate": "01-01-2024"} for i in range(1, 6)]

    def test_detecta_el_subconjunto_silencioso(self):
        from core.fuentes import case_link_de
        recuperados = {case_link_de(e) for e in self._cinco()}
        enviados = {case_link_de(e) for e in self._cinco()[:4]}
        excluidos = sorted(recuperados - enviados)
        assert excluidos == ["VCN-005-2024"]

    def test_el_conjunto_completo_no_marca_exclusiones(self):
        from core.fuentes import case_link_de
        recuperados = {case_link_de(e) for e in self._cinco()}
        enviados = {case_link_de(e) for e in self._cinco()}
        assert not (recuperados - enviados)

    def test_un_registro_sin_identidad_se_descarta(self):
        from core.fuentes import case_link_de
        mezcla = self._cinco() + [{"fecha_inicio": "01-01-2024"}]
        validos = [e for e in mezcla if case_link_de(e)]
        sin_id = [e for e in mezcla if not case_link_de(e)]
        assert len(validos) == 5 and len(sin_id) == 1

    def test_los_ids_viajan_en_la_auditoria(self):
        """
        COFECE: "en las trazas H04 los IDs por cálculo siguen vacíos". Sin
        ellos no se puede reconstruir de dónde salió un promedio.
        """
        from core.fuentes import case_link_de
        ids = sorted(case_link_de(e) for e in self._cinco())
        assert len(ids) == 5 and all(ids)


class TestAbstencionSoloSiFaltaAlgoQueNombrar:
    """
    Falso positivo propio, detectado en la regresión del 22-sep: el indicador
    `abstained` saltó de 0 a 7 de 20, incluidas H08, H10 y H15 — que
    respondieron bien, con fuentes y sin inventar nada.

    La causa: un chequeo PARTIAL agotaba el reintento y escribía
    "Tras dos búsquedas la evidencia no sostiene: " con la lista de faltantes
    VACÍA. `abstained` se deriva de que esa razón exista.

    Marcar abstención donde no la hubo no es un detalle de etiqueta: COFECE lee
    ese indicador, y decir que el agente se abstuvo cuando respondió es tan
    falso como lo contrario.
    """

    def test_partial_sin_componentes_insuficientes_no_abstiene(self):
        from core.sufficiency import INSUFFICIENT
        chequeo = {"components": [
            {"descripcion": "qué es el control", "estado": "PARTIAL"},
            {"descripcion": "cómo lo define COFECE", "estado": "SUFFICIENT"},
        ]}
        faltantes = [c["descripcion"] for c in chequeo["components"]
                     if c["estado"] == INSUFFICIENT]
        assert not faltantes, "PARTIAL no es INSUFFICIENT"

    def test_con_un_componente_insuficiente_si_abstiene(self):
        from core.sufficiency import INSUFFICIENT
        chequeo = {"components": [
            {"descripcion": "el criterio del 178/2017", "estado": INSUFFICIENT},
        ]}
        faltantes = [c["descripcion"] for c in chequeo["components"]
                     if c["estado"] == INSUFFICIENT]
        assert faltantes == ["el criterio del 178/2017"]

    def test_la_razon_nunca_queda_colgando(self):
        """Nunca debe escribirse la frase con la lista vacía."""
        faltantes = []
        razon = ("Tras dos búsquedas la evidencia no sostiene: "
                 + "; ".join(faltantes)) if faltantes else None
        assert razon is None


class TestLaEvidenciaSeAcumulaEntreHerramientas:
    """
    Lo encontró COFECE leyendo el código (§1.3 de su revisión del 22-sep):
    `requisitos_verificados` se calculaba contra el `result` de la llamada en
    curso y se sobrescribía.

    H14 llama `buscar_expedientes` **y** `buscar_criterios`. Si los criterios
    corren al final, el requisito de campos del registro —que ya se cumplió
    porque el registro trajo `relatedTccCaseFile`— vuelve a leerse como
    incumplido, y el agente recibe la orden de buscar algo que ya tiene.

    Un requisito satisfecho no deja de estarlo porque después se buscara otra
    cosa.
    """

    REGISTRO = {
        "caseLink": "677_2024_1SCJN",
        "relatedTccCaseFile": "565/2023",
        "relatedCollegiateCourt": "Primer Tribunal Colegiado…",
    }
    CRITERIO = {
        "id": "c1",
        "metadata": {"id_expediente": "677_2024_1SCJN"},
        "content": "reserva de jurisdicción",
    }

    def _req(self):
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        q = ("En el amparo en revisión 677/2024, ¿la Primera Sala resolvió "
             "todos los agravios? ¿Qué tribunal colegiado y qué expediente "
             "dieron origen?")
        u = ["677_2024_1SCJN"]
        return construir_requisitos(q, ResolutorDeIdentidades(u).resolver(q))

    def test_el_criterio_despues_del_registro_no_borra_lo_cumplido(self):
        """La regresión exacta: registro primero, criterios después."""
        from agent.turn_state import TurnState
        from core.requisitos import verificar

        st = TurnState()
        st.acumular_evidencia([self.REGISTRO])
        assert verificar(self._req(), st.evidencia_acumulada)["cumple"]

        st.acumular_evidencia([self.CRITERIO])
        v = verificar(self._req(), st.evidencia_acumulada)
        assert v["cumple"], (
            "el requisito ya estaba cumplido por el registro; una búsqueda "
            f"posterior de criterios no puede revertirlo: {v['faltantes']}"
        )

    def test_el_orden_inverso_da_el_mismo_veredicto(self):
        """Criterios primero, registro después. El resultado no depende del orden."""
        from agent.turn_state import TurnState
        from core.requisitos import verificar

        st = TurnState()
        st.acumular_evidencia([self.CRITERIO])
        assert not verificar(self._req(), st.evidencia_acumulada)["cumple"]

        st.acumular_evidencia([self.REGISTRO])
        assert verificar(self._req(), st.evidencia_acumulada)["cumple"]

    def test_sin_acumular_la_verificacion_se_revierte(self):
        """
        Fija la conducta ANTERIOR como incorrecta: verificar sólo contra el
        último resultado sí revierte el requisito. Si esta prueba empieza a
        fallar es que `verificar` cambió de contrato.
        """
        from core.requisitos import verificar
        assert verificar(self._req(), [self.REGISTRO])["cumple"]
        assert not verificar(self._req(), [self.CRITERIO])["cumple"]

    def test_no_se_duplica_el_mismo_documento(self):
        from agent.turn_state import TurnState
        st = TurnState()
        assert st.acumular_evidencia([self.CRITERIO, self.REGISTRO]) == 2
        assert st.acumular_evidencia([self.CRITERIO]) == 0
        assert len(st.evidencia_acumulada) == 2

    def test_dos_fragmentos_del_mismo_expediente_son_dos(self):
        """
        Deduplicar por expediente borraría criterios distintos del mismo
        documento, que es justo la evidencia que H19 necesita para separar
        mayoría de voto particular.
        """
        from agent.turn_state import TurnState
        st = TurnState()
        otro = dict(self.CRITERIO, id="c2", content="voto particular")
        assert st.acumular_evidencia([self.CRITERIO, otro]) == 2


class TestElCacheNoBorraCondicionesEnSilencio:
    """
    §7 de la revisión del 22-sep: *"El límite de 400 caracteres del cache no
    debe eliminar autor, negación, condición o atribución"*.

    El caso testigo es H18: la conclusión pierde "una vez que cause ejecutoria"
    y una multa condicionada se lee como firme. Un efecto sin su condición es
    un hecho distinto, y desde el texto recortado no se nota.
    """

    def test_corto_pasa_intacto(self):
        from core.evidence_cache import recortar_sin_borrar_en_silencio
        t = "La Sala concedió el amparo."
        assert recortar_sin_borrar_en_silencio(t) == t

    def test_declara_que_falta_texto(self):
        from core.evidence_cache import recortar_sin_borrar_en_silencio
        t = "Hecho. " * 300
        r = recortar_sin_borrar_en_silencio(t, limite=100)
        assert "fragmento recortado" in r
        assert len(r) < len(t)

    def test_avisa_cuando_lo_omitido_lleva_una_condicion(self):
        from core.evidence_cache import recortar_sin_borrar_en_silencio
        t = ("Se ordena la reindividualización de la multa. " * 8
             + "Lo anterior una vez que cause ejecutoria la presente.")
        r = recortar_sin_borrar_en_silencio(t, limite=120)
        assert "condiciones" in r
        assert "buscar_criterios" in r

    def test_no_avisa_de_material_sensible_si_no_lo_hay(self):
        from core.evidence_cache import recortar_sin_borrar_en_silencio
        t = "Se analizó el mercado relevante de la zona. " * 20
        r = recortar_sin_borrar_en_silencio(t, limite=120)
        assert "fragmento recortado" in r
        assert "condiciones" not in r

    def test_no_corta_a_media_palabra(self):
        from core.evidence_cache import recortar_sin_borrar_en_silencio
        t = "palabra " * 200
        r = recortar_sin_borrar_en_silencio(t, limite=100)
        cabeza = r.split("[…")[0].strip()
        assert cabeza.endswith("palabra")

    def test_la_negacion_cuenta_como_material_sensible(self):
        """
        Es el par que ya nos costó una vez: "no sanciona" y "sanciona"
        comparten casi todas las palabras y significan lo contrario.
        """
        from core.evidence_cache import recortar_sin_borrar_en_silencio
        t = "El pleno resolvió. " * 12 + "En consecuencia, no se sanciona."
        r = recortar_sin_borrar_en_silencio(t, limite=100)
        assert "negaciones" in r


class TestPresupuestoDePeticionesNoDeLlamadas:
    """
    §1.7 de COFECE: *"Una llamada `buscar_criterios` para dos documentos
    realiza dos HTTP internos: contar sólo llamadas elegidas por el modelo
    oculta ese coste."*

    Tiene razón, y el punto ciego lo introdujimos nosotros con la búsqueda por
    documento del 22-sep. El límite de seis llamadas no acota nada si una sola
    puede abrir diez peticiones.

    Lo que se fija aquí no es sólo el tope: es que al agotarse **se declare**
    qué documentos quedaron sin consultar. Una comparación a la que le falta
    un lado tiene que poder saberse incompleta.
    """

    def _agente(self, limite):
        from agent.agent import NormaPlusAgent

        class _Criterio:
            def __init__(self, cid, case_link):
                self.id, self.case_link = cid, case_link

            def model_dump(self):
                return {"id": self.id,
                        "metadata": {"id_expediente": self.case_link},
                        "content": "texto"}

        class _Criterios:
            def __init__(self): self.llamadas = []

            async def search(self, query, top_k=15, filters=None, collector=None):
                cl = (filters or {}).get("caseLink")
                self.llamadas.append(cl)
                # El cliente real devuelve objetos con `.id` y `.model_dump()`,
                # no diccionarios. Un doble que devuelva dicts pasa la prueba y
                # esconde el contrato.
                return [_Criterio(f"c{len(self.llamadas)}", cl)]

        ag = NormaPlusAgent.__new__(NormaPlusAgent)
        ag.criterios = _Criterios()
        ag.estadistica = type("E", (), {"universo": None})()
        ag.max_http_requests = limite
        return ag

    async def _correr(self, ag, exps, state):
        return await ag._buscar_criterios_por_documento(
            "query", exps, 15, None, state)

    def test_cada_documento_gasta_una_peticion(self):
        import asyncio
        from agent.turn_state import TurnState
        ag, st = self._agente(12), TurnState()
        asyncio.run(self._correr(ag, ["VCN-001-2025", "VCN-002-2024"], st))
        assert st.peticiones_http == 2
        assert ag.criterios.llamadas == ["VCN-001-2025", "VCN-002-2024"]

    def test_al_agotarse_no_se_consulta_de_mas(self):
        import asyncio
        from agent.turn_state import TurnState
        ag, st = self._agente(2), TurnState()
        exps = ["VCN-001-2025", "VCN-002-2024", "VCN-003-2020", "VCN-004-2020"]
        asyncio.run(self._correr(ag, exps, st))
        assert len(ag.criterios.llamadas) == 2
        assert st.peticiones_http == 2

    def test_lo_no_consultado_queda_declarado(self):
        """Lo que importa: el recorte no puede ser silencioso."""
        import asyncio
        from agent.turn_state import TurnState
        ag, st = self._agente(1), TurnState()
        asyncio.run(self._correr(ag, ["VCN-001-2025", "VCN-002-2024"], st))

        omitidos = [r["expediente"] for r in st.recortes_por_presupuesto]
        assert omitidos == ["VCN-002-2024"]

        cob = {c["expediente"]: c for c in st.cobertura_por_documento}
        assert "presupuesto" in cob["VCN-002-2024"]["motivo"]
        assert cob["VCN-002-2024"]["recuperados"] == 0

    def test_sin_estado_no_revienta(self):
        """El agente se usa sin `TurnState` en pruebas y humos."""
        import asyncio
        ag = self._agente(1)
        asyncio.run(self._correr(ag, ["VCN-001-2025", "VCN-002-2024"], None))
        assert len(ag.criterios.llamadas) == 2


class TestUnDatoNoSeApruebaConElCampoDeOtro:
    """
    I2 de la revisión del 23-sep. Textual:

        "`AND` se aplica a los datos distintos efectivamente pedidos. `OR` se
         reserva a fuentes alternativas que prueben EL MISMO dato."

    El defecto medido: en H14 bastaba `judicialBody` —el órgano que DICTA la
    resolución— para dar por satisfecha una pregunta sobre el tribunal
    relacionado y su expediente. H14 salía PASS en las tres repeticiones
    porque el modelo acertaba, no porque el control lo sostuviera. Es el
    patrón del filtro de negación: un mecanismo que parece funcionar.

    La otra mitad importa igual: no exigir datos que nadie pidió.
    """

    Q14 = ("En el amparo en revisión 677/2024, ¿la Primera Sala resolvió todos "
           "los agravios? ¿Qué tribunal colegiado y qué expediente dieron origen?")

    def _req(self, q):
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        u = ["677_2024_1SCJN", "VCN-001-2025", "178_2017_2TCC", "43_2021_3JD"]
        return construir_requisitos(q, ResolutorDeIdentidades(u).resolver(q))

    def _papeles(self, q):
        return {r["papel"] for r in self._req(q) if r["tipo"] == "campos_registro"}

    # ── El defecto exacto ────────────────────────────────────────────
    def test_el_organo_emisor_no_satisface_el_tribunal_relacionado(self):
        from core.requisitos import verificar
        v = verificar(self._req(self.Q14), [{
            "caseLink": "677_2024_1SCJN",
            "judicialBody": "Primera Sala de la SCJN",
        }])
        assert not v["cumple"]
        assert any("tribunal relacionado" in f for f in v["faltantes"])
        assert any("expediente relacionado" in f for f in v["faltantes"])

    def test_un_solo_lado_deja_el_otro_pendiente(self):
        """El tribunal sin el expediente no completa el encargo."""
        from core.requisitos import verificar
        v = verificar(self._req(self.Q14), [{
            "caseLink": "677_2024_1SCJN",
            "relatedCollegiateCourt": "Primer Tribunal Colegiado…",
        }])
        assert not v["cumple"]
        assert any("expediente relacionado" in f for f in v["faltantes"])
        assert not any("tribunal relacionado" in f for f in v["faltantes"])

    def test_con_los_dos_campos_cumple(self):
        from core.requisitos import verificar
        v = verificar(self._req(self.Q14), [{
            "caseLink": "677_2024_1SCJN",
            "relatedCollegiateCourt": "Primer Tribunal Colegiado…",
            "relatedTccCaseFile": "565/2023",
        }])
        assert v["cumple"]

    # ── La otra mitad: no exigir de más ──────────────────────────────
    def test_pedir_solo_el_tribunal_no_exige_el_expediente(self):
        p = self._papeles("En el amparo 677/2024, ¿qué tribunal colegiado "
                          "conoció del asunto?")
        assert "tribunal relacionado" in p
        assert "expediente relacionado" not in p

    def test_pedir_solo_el_expediente_no_exige_el_tribunal(self):
        p = self._papeles("En el amparo 677/2024, ¿qué expediente le dio origen?")
        assert "expediente relacionado" in p
        assert "tribunal relacionado" not in p

    def test_nombrar_un_tribunal_sigue_sin_exigir_campos(self):
        """
        "del Segundo Tribunal Colegiado" identifica el documento; no pregunta
        cuál es. La regresión que esto evita ya nos costó una vez.
        """
        assert not self._papeles(
            "Compara lo de VCN-001-2025 con lo del amparo 178/2017 del "
            "Segundo Tribunal Colegiado")

    # ── Fuentes alternativas del MISMO dato siguen en OR ─────────────
    def _componente(self, v, papel):
        """
        El componente de un papel concreto. Mirar el `cumple` global mezcla
        requisitos: "¿hubo algún voto en contra?" pide además evidencia de
        voz, que un registro de expediente no puede satisfacer.
        """
        for c in v["componentes"]:
            if papel in c["detalle"] or papel in c["descripcion"]:
                return c
        raise AssertionError(f"no hay componente para {papel}: {v['componentes']}")

    def test_los_dos_campos_de_disidencia_son_alternativas(self):
        from core.requisitos import verificar
        q = "En el VCN-003-2025, ¿hubo algún voto en contra?"
        for campo in ("dissentingOpinions", "dissentingAndConcurringOpinions"):
            v = verificar(self._req(q), [{"caseLink": "VCN-003-2025",
                                          campo: "Voto del comisionado X"}])
            c = self._componente(v, "votos disidentes")
            assert c["cumple"], f"{campo} debería bastar por sí solo"

    def test_el_emisor_se_prueba_con_cualquiera_de_sus_dos_campos(self):
        from core.requisitos import verificar
        q = "¿Qué órgano dictó el criterio del 43/2021?"
        for campo in ("judicialBody", "authority"):
            v = verificar(self._req(q), [{"caseLink": "43_2021_3JD",
                                          campo: "Juzgado Tercero de Distrito"}])
            assert v["cumple"], f"{campo} debería bastar por sí solo"


class TestFechaContradictoriaYPrincipalSinFecha:
    """
    I3 de la revisión del 23-sep. Tres defectos reproducidos por COFECE y
    confirmados aquí contra el universo real:

    1. Una fecha explícita que ningún acto cumple caía en silencio. El filtro
       de alcance nunca se aplicaba y la búsqueda quedaba abierta.
    2. El formato ISO no se reconocía, pese a que el comentario del módulo
       decía soportarlo desde el principio.
    3. El expediente PRINCIPAL nombrado sin fecha no se resolvía: el catálogo
       sólo cubría los actos derivados.

    Textual suyo: "Si ningún candidato cumple una fecha explícita, devolver
    conflicto o falta de coincidencia, sin descartar el año pedido para elegir
    otro."
    """

    def _r(self):
        from core.identidades import ResolutorDeIdentidades
        return ResolutorDeIdentidades([
            "VCN-004-2022", "VCN-004-2022_2025_10_09",
            "VCN-001-2017", "VCN-001-2017_2019_03_14",
            "677_2024_1SCJN",
        ])

    def _acto(self, q):
        return [i for i in self._r().resolver(q) if i.get("acto_de")]

    # ── 1. Conflicto declarado ───────────────────────────────────────
    def test_fecha_inexistente_declara_conflicto(self):
        a = self._acto("el cumplimiento de VCN-004-2022 de 9 de octubre de 2024")
        assert len(a) == 1 and a[0]["candidatos"] == []
        assert "2024-10-09" in a[0]["conflicto"]

    def test_el_conflicto_no_elige_el_acto_mas_parecido(self):
        """Lo peligroso sería resolver al de 2025 porque 'se le parece'."""
        a = self._acto("el cumplimiento de VCN-004-2022 de 9 de octubre de 2024")
        assert "VCN-004-2022_2025_10_09" not in a[0]["candidatos"]

    def test_el_conflicto_dice_que_fechas_si_existen(self):
        a = self._acto("cumplimiento de VCN-001-2017 de 5 de mayo de 2020")
        assert "2019-03-14" in a[0]["conflicto"]

    # ── 2. Formatos de fecha ─────────────────────────────────────────
    def test_formato_iso(self):
        a = self._acto("el cumplimiento de VCN-004-2022 de 2025-10-09")
        assert a[0]["candidatos"] == ["VCN-004-2022_2025_10_09"]

    def test_los_tres_formatos_dan_el_mismo_acto(self):
        formas = ["9 de octubre de 2025", "09-10-2025", "2025-10-09", "9/10/2025"]
        vistos = {
            tuple(self._acto(f"cumplimiento de VCN-004-2022 de {f}")[0]["candidatos"])
            for f in formas
        }
        assert vistos == {("VCN-004-2022_2025_10_09",)}

    # ── 3. El principal sin fecha ────────────────────────────────────
    def test_el_principal_sin_fecha_se_resuelve(self):
        r = self._r().resolver("¿qué criterios tiene VCN-004-2022?")
        principal = [i for i in r if i["candidatos"] == ["VCN-004-2022"]]
        assert principal, "el principal nombrado sin fecha debe resolverse"
        assert not principal[0].get("acto_de"), (
            "es el principal, no un acto derivado de nadie")

    def test_el_principal_declara_que_tiene_derivados(self):
        """
        Saber que existe un cumplimiento es lo que permite advertir que la
        pregunta podría referirse a otro acto.
        """
        r = self._r().resolver("¿qué criterios tiene VCN-004-2022?")
        p = [i for i in r if i["candidatos"] == ["VCN-004-2022"]][0]
        assert p["tiene_derivados"] == ["VCN-004-2022_2025_10_09"]

    def test_pedir_el_cumplimiento_sigue_dando_el_derivado(self):
        """El arreglo del principal no puede revertir el cierre de H10."""
        a = self._acto("criterios del cumplimiento de amparo de VCN-004-2022")
        assert a[0]["candidatos"] == ["VCN-004-2022_2025_10_09"]

    def test_un_expediente_sin_derivados_no_se_rompe(self):
        r = self._r().resolver("¿qué dice 677/2024?")
        assert any("677_2024_1SCJN" in i["candidatos"] for i in r)


class TestLaVozEsDelPasajeNoDelDocumento:
    """
    H16-A, el FAIL CRÍTICO de la adjudicación del 23-sep.

    El criterio 4035 de VCN-005-2024 es razonamiento de la MAYORÍA, en la
    página 13. El clasificador barría los 14,911 caracteres del contexto
    completo, encontraba en la página 14 la fórmula de firmas —"Con voto
    concurrente del Comisionado José Eduardo Mendoza Contreras"— y se la
    atribuía al criterio. El gold lo identifica como `pleno_mayoria`.

    Textual de COFECE en I4: "No basta encontrar la palabra «voto» en
    cualquier parte del contexto. Se necesita vincularla a la sección que
    contiene el criterio."

    La frontera es la página del documento, no una distancia en caracteres:
    un umbral ajustado a tres observaciones sería un número inventado.
    """

    FIRMA = ("Comisionados Andrea Marván Saltiel, Giovanni Tapia Lezama y "
             "Alejandro Faya Rodríguez. Con voto concurrente del Comisionado "
             "José Eduardo Mendoza Contreras, quien considera que...")

    def _doc(self, anchor, contexto, content="texto del criterio"):
        return {"caseLink": "VCN-005-2024", "content": content,
                "metadata": {"anchor": anchor, "context": contexto}}

    # ── El caso H16-A ────────────────────────────────────────────────
    def test_la_firma_de_otra_pagina_no_es_la_voz_del_criterio(self):
        from core.voz import clasificar_voz, NO_IDENTIFICADA
        doc = self._doc(
            anchor="no se actualiza el supuesto de sucesión de actos",
            contexto=("<<<PAGINA:13>>> Los propósitos son distintos y "
                      "no se actualiza el supuesto de sucesión de actos "
                      "respecto de la INVERSIÓN 2018.\n"
                      f"<<<PAGINA:14>>> {self.FIRMA}"))
        assert clasificar_voz(doc)["voz"] == NO_IDENTIFICADA

    def test_pero_la_marca_del_documento_se_reporta(self):
        """
        Callarla sería el error opuesto: que la resolución lleve un voto
        concurrente es un hecho del documento. Lo prohibido es atribuírselo
        a este pasaje.
        """
        from core.voz import clasificar_voz
        doc = self._doc(
            anchor="no se actualiza el supuesto",
            contexto=("<<<PAGINA:13>>> no se actualiza el supuesto de "
                      "sucesión.\n"
                      f"<<<PAGINA:14>>> {self.FIRMA}"))
        v = clasificar_voz(doc)
        assert v["marca_en_documento"]
        assert "concurrente" in v["marca_en_documento"].lower()

    # ── Y lo que NO puede romperse: H19 ──────────────────────────────
    def test_la_marca_en_la_misma_pagina_si_atribuye(self):
        from core.voz import clasificar_voz, VOTO_PARTICULAR
        doc = self._doc(
            anchor="la multa debió individualizarse de otro modo",
            contexto=("<<<PAGINA:152>>> la multa debió individualizarse de "
                      "otro modo. Magistrada Irma Leticia Flores Díaz. "
                      "Respetuosamente, formulo voto particular."))
        v = clasificar_voz(doc)
        assert v["voz"] == VOTO_PARTICULAR
        assert v["autor"] == "Irma Leticia Flores Díaz"

    def test_la_marca_en_el_texto_del_criterio_siempre_atribuye(self):
        from core.voz import clasificar_voz, VOTO_PARTICULAR
        doc = self._doc(anchor="x", contexto="<<<PAGINA:9>>> otra cosa",
                        content="Formulo voto particular porque disiento.")
        assert clasificar_voz(doc)["voz"] == VOTO_PARTICULAR

    # ── Fallbacks, que es donde se decide el sesgo ───────────────────
    def test_documento_sin_paginar_se_lee_completo(self):
        """Sin paginado el contexto ES una sección: no hay de dónde separar."""
        from core.voz import clasificar_voz, VOTO_PARTICULAR
        doc = self._doc(anchor="", contexto="Magistrada X. Formulo voto particular.")
        assert clasificar_voz(doc)["voz"] == VOTO_PARTICULAR

    def test_paginado_sin_anchor_ubicable_no_atribuye(self):
        """
        Con páginas y sin poder situar el pasaje, no hay forma de afirmar que
        la marca sea suya. Se prefiere no identificar sobre atribuir mal: una
        voz perdida es una reserva; una voz inventada es H16-A.
        """
        from core.voz import clasificar_voz, NO_IDENTIFICADA
        doc = self._doc(anchor="frase que no aparece en el contexto",
                        contexto=f"<<<PAGINA:13>>> algo\n<<<PAGINA:14>>> {self.FIRMA}")
        assert clasificar_voz(doc)["voz"] == NO_IDENTIFICADA

    def test_no_inventa_mayoria_por_ausencia_de_marca(self):
        """La regla de siempre, que este cambio no puede erosionar."""
        from core.voz import clasificar_voz, NO_IDENTIFICADA, MAYORIA
        doc = self._doc(anchor="a", contexto="<<<PAGINA:1>>> a, sin marcas")
        v = clasificar_voz(doc)
        assert v["voz"] == NO_IDENTIFICADA and v["voz"] != MAYORIA


class TestLasFechasSalenDelRegistroNoDelModelo:
    """
    I7 de la revisión del 23-sep. Dos defectos reproducidos por COFECE sobre
    entradas construidas, no sobre las respuestas reales de H04:

      "Mantener los cinco IDs y alterar una fecha entregada por el modelo
       produce 63.6 sin detectar que el valor difiere del registro original."
      "Un registro artificial marcado no calculable puede entrar al promedio."

    Confirmados aquí antes de arreglar: alterar una fecha movía el promedio de
    15.0 a 20.0 sin aviso, y un no calculable aparecía en `ids_incluidos`
    aunque no aportara valor — tres identificadores para un promedio de dos.

    El modelo elige QUÉ se calcula; los valores los pone el código.
    """

    REG = [
        {"caseLink": "A-1", "startAgreementDate": "01-01-2024",
         "resolutionDate": "11-01-2024"},
        {"caseLink": "A-2", "startAgreementDate": "01-01-2024",
         "resolutionDate": "21-01-2024"},
    ]

    def _agente(self):
        from agent.agent import NormaPlusAgent
        from temporal.analyzer import TemporalAnalyzer
        from temporal.holidays import HolidayCalendar
        ag = NormaPlusAgent.__new__(NormaPlusAgent)
        ag.temporal = TemporalAnalyzer(HolidayCalendar("data/dias_inhabiles.xlsx"))
        return ag

    def _calcular(self, expedientes, recuperados=None):
        import asyncio
        from agent.turn_state import TurnState
        st = TurnState()
        st.last_expedientes = [dict(r) for r in (recuperados or self.REG)]
        return asyncio.run(self._agente()._exec_calcular_plazos(
            {"expedientes": expedientes, "campo_inicio": "startAgreementDate",
             "campo_fin": "resolutionDate", "unidad": "dias_naturales",
             "compute_stats": True}, None, st))

    def test_base(self):
        r = self._calcular([dict(x) for x in self.REG])
        assert r["stats"]["promedio"] == 15.0

    # ── 1. Fecha alterada por el modelo ──────────────────────────────
    def test_una_fecha_alterada_no_cambia_el_resultado(self):
        alterado = [dict(x) for x in self.REG]
        alterado[1]["resolutionDate"] = "31-01-2024"   # diez días más
        r = self._calcular(alterado)
        assert r["stats"]["promedio"] == 15.0, (
            "el promedio debe salir del registro, no de lo que reenvió el modelo")

    def test_la_discrepancia_se_declara(self):
        alterado = [dict(x) for x in self.REG]
        alterado[1]["resolutionDate"] = "31-01-2024"
        r = self._calcular(alterado)
        d = r["FECHAS_CORREGIDAS_DESDE_EL_REGISTRO"]["casos"]
        assert d[0]["expediente"] == "A-2"
        assert d[0]["enviado_por_el_modelo"] == "31-01-2024"
        assert d[0]["valor_del_registro"] == "21-01-2024"

    def test_sin_alteracion_no_hay_aviso(self):
        r = self._calcular([dict(x) for x in self.REG])
        assert "FECHAS_CORREGIDAS_DESDE_EL_REGISTRO" not in r

    # ── 2. Reconciliación de la auditoría ────────────────────────────
    def test_un_no_calculable_no_aparece_entre_los_incluidos(self):
        nc = {"caseLink": "A-3", "startAgreementDate": None,
              "resolutionDate": "11-01-2024"}
        todos = [dict(x) for x in self.REG] + [nc]
        r = self._calcular(todos, recuperados=todos)
        assert r["stats"]["ids_incluidos"] == ["A-1", "A-2"]
        assert r["stats"]["ids_sin_valor"] == ["A-3"]

    def test_ids_incluidos_reconcilia_con_count(self):
        """
        Es la propiedad que hace auditable la cifra: la lista con la que se
        reconstruye el promedio tiene que tener tantos elementos como
        registros se promediaron.
        """
        nc = {"caseLink": "A-3", "startAgreementDate": None,
              "resolutionDate": "11-01-2024"}
        todos = [dict(x) for x in self.REG] + [nc]
        r = self._calcular(todos, recuperados=todos)
        assert len(r["stats"]["ids_incluidos"]) == r["stats"]["count"]
        assert r["stats"]["universo_calculado"] == r["stats"]["count"]

    def test_el_no_calculable_sigue_reportandose(self):
        """Excluirlo del promedio no es ocultarlo."""
        nc = {"caseLink": "A-3", "startAgreementDate": None,
              "resolutionDate": "11-01-2024"}
        todos = [dict(x) for x in self.REG] + [nc]
        r = self._calcular(todos, recuperados=todos)
        assert r["NO_CALCULABLES"]["count"] == 1


class TestUnaAfirmacionSinCitaSeRetiraNoSeAnota:
    """
    I8 de la revisión del 23-sep. Textual:

        "Si se retira una cita inválida, no debe conservarse una afirmación
         categórica que dependía exclusivamente de ella."
        "Tener otro marcador en la frase no prueba que respalde todo su
         contenido."

    Antes la frase se conservaba con "[SIN RESPALDO…]" pegado al final. Un
    aviso no deshace una aseveración: el lector se queda con la frase.
    """

    class _Reg:
        def __init__(self, validos): self.validos = set(validos)
        def resolve(self, m): return {"id": m} if m in self.validos else None

    def _v(self, texto, validos=("C1",)):
        from core.validacion_salida import validar_borrador
        return validar_borrador(texto, self._Reg(validos))

    def test_la_afirmacion_desaparece_del_texto(self):
        r = self._v("La COFECE impuso una multa de 40 millones [C9].")
        assert "40 millones" not in r["texto"]
        assert "AFIRMACIÓN RETIRADA" in r["texto"]

    def test_pero_queda_en_la_traza(self):
        """Retirarla del texto no es borrarla del expediente."""
        r = self._v("La COFECE impuso una multa de 40 millones [C9].")
        assert any("40 millones" in f for f in r["frases_sin_respaldo"])

    def test_una_frase_con_cita_valida_no_se_toca(self):
        r = self._v("El pleno resolvió no sancionar [C1].")
        assert "no sancionar" in r["texto"]
        assert "RETIRADA" not in r["texto"]

    def test_la_frase_que_pierde_una_de_dos_citas_se_advierte(self):
        r = self._v("El criterio se sostuvo en dos precedentes [C1][C9].")
        assert "dos precedentes" in r["texto"], "conserva otra cita: no se retira"
        assert "no resolvió" in r["texto"]
        assert r["frases_con_cita_parcial"]

    def test_solo_se_toca_la_frase_afectada(self):
        r = self._v("Primero esto [C1].\nSegundo aquello [C9].\nTercero lo otro [C1].")
        assert "Primero esto" in r["texto"]
        assert "Tercero lo otro" in r["texto"]
        assert "Segundo aquello" not in r["texto"]

    def test_sin_marcadores_invalidos_no_hay_reparacion(self):
        r = self._v("Todo correcto [C1].")
        assert r["reparado"] is False
        assert r["texto"] == "Todo correcto [C1]."


class TestNingunaDecisionSeCaeEnSilencio:
    """
    El agente escribe decisiones con `set_decision(nombre, ...)` y el esquema
    de trazas las valida. Un nombre que no esté declarado **se descarta sin
    error**: la decisión se calcula bien, no llega a la traza, y nadie se
    entera hasta que alguien la busca.

    Pasó hoy con `evidencia_verificada` y `presupuesto_peticiones`, y ya había
    pasado en septiembre con `composicion_fuentes`, que vivía sólo en la traza
    completa y no en el renglón plano que leen `compare.py` y el XLSX. Es la
    misma clase de falla que perseguimos en el producto: algo que existe, se
    calcula correctamente, y no llega a donde se lee.

    Esta prueba la cierra por construcción en vez de por lista.
    """

    def test_todo_set_decision_existe_en_el_esquema(self):
        import re
        from pathlib import Path
        from core.tracing.schema import Decisions

        fuente = Path("agent/agent.py").read_text(encoding="utf-8")
        usados = set(re.findall(r'set_decision\(\s*"([a-z_]+)"', fuente))
        assert usados, "no se encontró ninguna llamada a set_decision"

        declarados = set(Decisions.model_fields)
        huerfanos = sorted(usados - declarados)
        assert not huerfanos, (
            "estas decisiones se escriben y el esquema las descarta en "
            f"silencio: {huerfanos}. Decláralas en core/tracing/schema.py"
        )


class TestVerificadorSemantico:
    """
    I5. Lo que se prueba aquí es lo determinista del verificador: que un
    veredicto positivo exija un localizador que el CÓDIGO encuentre en la
    evidencia.

    Es la regla que impide que un modelo complaciente lo vuelva inútil. COFECE
    lo dijo al corregir la prueba de aceptación que habíamos propuesto: "Un
    verificador que aprueba todo rechazaría cero PASS y sería inútil."
    """

    EV = {"C1": {"documento": "VCN-005-2018", "anchor": "",
                 "texto": ("En una sucesión de actos, la concentración debe "
                           "notificarse antes de realizar la aportación de "
                           "capital que provoque que se rebasen los umbrales "
                           "legales; en ese momento la COFECE analizará la "
                           "operación.")}}

    class _Ad:
        def __init__(self, payload): self.payload = payload
        async def quick_completion(self, messages, model, max_tokens=50):
            return self.payload

    def _run(self, payload, evidencia=None):
        import asyncio, json
        from core.verificacion_semantica import verificar
        return asyncio.run(verificar(
            "pregunta", "borrador [C1].", evidencia or self.EV,
            self._Ad(payload), "m"))

    def test_supported_sin_localizador_no_se_acepta(self):
        import json
        from core.verificacion_semantica import NOT_DETERMINED
        r = self._run(json.dumps({"afirmaciones": [
            {"afirmacion": "x", "marcadores": ["C1"], "veredicto": "supported",
             "localizador": "", "motivo": "porque sí"}]}))
        assert r["afirmaciones"][0]["veredicto"] == NOT_DETERMINED

    def test_localizador_inventado_degrada_el_veredicto(self):
        """Un modelo que aprueba todo tendría que inventar citas verificables."""
        import json
        from core.verificacion_semantica import NOT_DETERMINED
        r = self._run(json.dumps({"afirmaciones": [
            {"afirmacion": "x", "marcadores": ["C1"], "veredicto": "supported",
             "localizador": "la resolución declaró la independencia total de "
                            "ambos aumentos de capital sin condición alguna",
             "motivo": "inventado"}]}))
        a = r["afirmaciones"][0]
        assert a["veredicto_del_modelo"] == "supported"
        assert a["veredicto"] == NOT_DETERMINED
        assert a["localizador_verificado"] is False

    def test_localizador_real_se_acepta(self):
        import json
        from core.verificacion_semantica import SUPPORTED
        r = self._run(json.dumps({"afirmaciones": [
            {"afirmacion": "x", "marcadores": ["C1"], "veredicto": "supported",
             "localizador": "la concentración debe notificarse antes de "
                            "realizar la aportación de capital",
             "motivo": "está en el texto"}]}))
        assert r["afirmaciones"][0]["veredicto"] == SUPPORTED
        assert r["afirmaciones"][0]["localizador_verificado"] is True

    def test_un_contradicted_sin_respaldo_tampoco_pasa(self):
        """
        No se degrada a `contradicted`: afirmar un problema que no se probó es
        el mismo error en la otra dirección.
        """
        import json
        from core.verificacion_semantica import NOT_DETERMINED
        r = self._run(json.dumps({"afirmaciones": [
            {"afirmacion": "x", "marcadores": ["C1"], "veredicto": "contradicted",
             "localizador": "texto que no existe en ninguna parte del acervo",
             "motivo": "inventado"}]}))
        assert r["afirmaciones"][0]["veredicto"] == NOT_DETERMINED

    def test_una_afirmacion_que_cita_y_no_se_sostiene_es_hallazgo(self):
        """
        Es el caso de H16-C: el verificador dio el motivo correcto con
        veredicto `not_determined`. Contar sólo `contradicted` daba el caso
        por limpio.
        """
        import json
        r = self._run(json.dumps({"afirmaciones": [
            {"afirmacion": "los aumentos fueron independientes",
             "marcadores": ["C1"], "veredicto": "not_determined",
             "localizador": "", "motivo": "la evidencia no lo establece"}]}))
        assert r["resumen"]["sin_soporte_citando"] == 1

    def test_json_invalido_no_revienta(self):
        r = self._run("lo siento, no puedo")
        assert r["ejecutado"] is False and r["afirmaciones"] == []

    def test_el_adaptador_que_falla_no_tumba_la_respuesta(self):
        import asyncio
        from core.verificacion_semantica import verificar

        class _Roto:
            async def quick_completion(self, **kw): raise RuntimeError("502")

        r = asyncio.run(verificar("p", "b", self.EV, _Roto(), "m"))
        assert r["ejecutado"] is False
        assert "RuntimeError" in r["error"]


class TestElRegistroTambienTieneQueSerCitable:
    """
    Lo destapó la banda del 23-sep. El verificador marcaba "sin soporte" el
    57% de las afirmaciones cuando la evidencia era un registro de expediente,
    contra 20% cuando era un criterio, y 22 de 27 localizadores fallidos caían
    de ese lado.

    No era que esas respuestas estuvieran mal sustentadas: los registros se
    serializaban como JSON crudo y no había nada que citar. El contrato de
    localizador estaba pensado para prosa.
    """

    REGISTRO = {
        "ref": "E1", "caseLink": "677_2024_1SCJN",
        "relatedTccCaseFile": "565/2023",
        "relatedCollegiateCourt": "Primer Tribunal Colegiado",
        "senseOfResolution": None, "metadata": {},
    }

    def test_un_registro_se_rinde_por_renglones(self):
        from core.verificacion_semantica import texto_de_evidencia
        t = texto_de_evidencia(self.REGISTRO)
        assert "relatedTccCaseFile: 565/2023" in t
        assert "ref" not in t, "el marcador no es contenido"
        assert "senseOfResolution" not in t, "los vacíos no viajan"

    def test_un_criterio_se_deja_como_esta(self):
        from core.verificacion_semantica import texto_de_evidencia
        doc = {"ref": "C1", "content": "La concentración debe notificarse antes."}
        assert texto_de_evidencia(doc) == "La concentración debe notificarse antes."

    def test_el_renglon_de_un_registro_es_localizable(self):
        """
        Es la propiedad que faltaba: que el código pueda comprobar una cita al
        registro igual que comprueba una cita a un criterio.
        """
        from core.verificacion_semantica import (
            _localizador_existe, texto_de_evidencia)
        t = texto_de_evidencia(self.REGISTRO)
        assert _localizador_existe("relatedTccCaseFile: 565/2023", t)
        assert not _localizador_existe("relatedTccCaseFile: 999/2099", t)

    def test_la_exportacion_y_el_verificador_usan_el_mismo_render(self):
        """
        El payload de la traza guardaba `content`, que en un registro está
        vacío: la traza no llevaba el contenido de los registros. Tienen que
        salir de la misma función o vuelven a divergir.
        """
        import re
        from pathlib import Path
        src = Path("agent/agent.py").read_text(encoding="utf-8")
        bloque = src[src.index('"evidencia_payload"'):][:600]
        assert "texto_de_evidencia_semantica(d)" in bloque, (
            "la exportación debe usar el render del verificador")


class TestLaURLFirmadaNoDesplazaLosDatos:
    """
    Lo encontró COFECE en su revisión del 25-sep, y la causa es peor que el
    bug: la defensa ya existía en este repositorio.

    `resolutionFileUrl` son ~1,500 caracteres de URL firmada. Desde septiembre
    se excluye del payload del agente por eso mismo. Al escribir el render del
    verificador no se reusó la exclusión: la URL empezaba en el carácter 33 y,
    con el corte a 1,200, el revisor recibía tres campos —id, caseLink y la
    URL— y ningún dato comprobable.

    Medido: en 208 de 239 entradas de registro. Todos los juicios del
    verificador sobre registros se emitieron sin datos, y nosotros reportamos
    ese 50% de "sin soporte" como si fuera un desajuste conceptual entre cifras
    y pasajes. No lo era.

    La otra mitad de su advertencia: "No basta aumentar 1,200 a otra constante:
    el orden de campos o una descripción larga volvería a desplazar el dato."
    """

    URL = "https://s3.amazonaws.com/norma/doc.pdf?X-Amz-Signature=" + "a" * 1500

    def _reg(self, **extra):
        base = {
            "ref": "E1", "id": 25050, "caseLink": "VCN-004-2024",
            "resolutionFileUrl": self.URL,
            "authority": "COFECE",
            "startAgreementDate": "03-10-2024",
            "resolutionDate": "21-11-2024",
            "senseOfResolution": ["Sanciona"],
        }
        base.update(extra)
        return base

    def test_la_url_no_viaja(self):
        from core.verificacion_semantica import texto_de_evidencia
        t = texto_de_evidencia(self._reg())
        assert "resolutionFileUrl" not in t
        assert "X-Amz-Signature" not in t

    def test_los_datos_si_llegan_dentro_del_corte(self):
        """Es la propiedad que faltaba: que el revisor tenga qué comprobar."""
        from core.verificacion_semantica import texto_de_evidencia
        corte = texto_de_evidencia(self._reg())[:1200]
        for campo in ("startAgreementDate", "resolutionDate",
                      "senseOfResolution", "authority"):
            assert campo in corte, f"{campo} no llega al revisor"

    def test_una_url_corta_o_larga_dan_lo_mismo(self):
        """
        Su criterio de aceptación, textual: "el registro idéntico con URL corta
        y de varios miles de caracteres entrega exactamente los mismos datos
        sustantivos al revisor".
        """
        from core.verificacion_semantica import texto_de_evidencia
        corta = texto_de_evidencia(self._reg(resolutionFileUrl="http://x/y.pdf"))
        larga = texto_de_evidencia(self._reg())
        assert corta == larga

    def test_ningun_campo_largo_desplaza_a_los_demas(self):
        """
        Excluir la URL no basta: otro campo largo haría lo mismo. Se acota por
        campo y se declara el recorte.
        """
        from core.verificacion_semantica import texto_de_evidencia
        t = texto_de_evidencia(self._reg(operationDescription="x " * 2000))
        corte = t[:1200]
        assert "resolutionDate" in corte
        assert "campos recortados por longitud" in t
        assert "operationDescription" in t.split("campos recortados")[1]

    def test_una_sola_fuente_de_verdad_para_la_exclusion(self):
        """
        La exclusión vivía sólo como atributo privado del modelo, así que la
        segunda ruta de render no la reusó. Ahora es constante de módulo y las
        dos la importan. Si alguien escribe una tercera, esto se lo recuerda.
        """
        from models.schemas import NO_AL_PROMPT, ExpedienteRecord
        assert "resolutionFileUrl" in NO_AL_PROMPT
        assert ExpedienteRecord._NO_AL_PROMPT.default is NO_AL_PROMPT


class TestElExtractoDebeSerDelDocumentoQueSeAfirma:
    """
    El mecanismo detrás de los dos falsos `supported` que encontró COFECE.

    El verificador comprobaba que el extracto EXISTIERA, no que el documento
    dueño del extracto fuera el sujeto de la afirmación. Y si ningún marcador
    resolvía, buscaba en toda la evidencia del turno. Con eso, un pasaje
    auténtico de cualquier documento validaba una afirmación atribuida a otro.

      H16-A  "en VCN-005-2018 la COFECE sostuvo que no todo aumento…"
             aprobado con el criterio 4035, que es de VCN-005-2024.
      H15-B  "el único factor de graduación es la duración"
             aprobado con un pasaje que dice "un factor".

    Textual de su criterio de aceptación: "Conservar el texto auténtico de
    4035 y cambiar solamente la atribución 2024→2018: nunca puede quedar
    validada la atribución al 2018 por ese texto de 2024."
    """

    # Criterio 4035, real, de VCN-005-2024
    TEXTO_2024 = ("Dos incrementos de capital no constituyen una sucesión de "
                  "actos cuando responden a propósitos distintos, se realizan "
                  "de manera independiente y el segundo deriva de "
                  "circunstancias que no podían preverse al celebrarse el "
                  "primero.")
    # Criterios 3930/3931, reales, de VCN-005-2018
    TEXTO_2018 = ("En una sucesión de actos, la concentración debe notificarse "
                  "antes de realizar la aportación de capital que provoque que "
                  "se rebasen los umbrales legales.")

    EV = {
        "C1": {"documento": "VCN-005-2024", "texto": TEXTO_2024, "anchor": ""},
        "C5": {"documento": "VCN-005-2018", "texto": TEXTO_2018, "anchor": ""},
    }

    class _Ad:
        def __init__(self, payload): self.payload = payload
        async def quick_completion(self, messages, model, max_tokens=50):
            return self.payload

    def _run(self, payload, ev=None):
        import asyncio
        from core.verificacion_semantica import verificar
        return asyncio.run(verificar("pregunta", "borrador", ev or self.EV,
                                     self._Ad(payload), "m"))

    # ── El caso H16-A ────────────────────────────────────────────────
    def test_atribuir_al_2018_el_texto_del_2024_no_queda_validado(self):
        import json
        from core.verificacion_semantica import NOT_DETERMINED
        r = self._run(json.dumps({"afirmaciones": [{
            "afirmacion": "En VCN-005-2018 la COFECE sostuvo que los aumentos "
                          "eran independientes",
            "marcadores": ["C5"],          # cita el 2018
            "veredicto": "supported",
            "localizador": self.TEXTO_2024[:120],   # pero el texto es del 2024
            "motivo": "está en la evidencia"}]}))
        a = r["afirmaciones"][0]
        assert a["veredicto"] == NOT_DETERMINED
        assert a["integridad"] == "atribucion_no_acreditada", (
            "el extracto es auténtico pero de otro documento: es una "
            "atribución cruzada, no un localizador inventado")

    def test_atribuir_al_2024_su_propio_texto_si_se_acepta(self):
        import json
        from core.verificacion_semantica import SUPPORTED
        r = self._run(json.dumps({"afirmaciones": [{
            "afirmacion": "En VCN-005-2024 los aumentos fueron independientes",
            "marcadores": ["C1"], "veredicto": "supported",
            "localizador": self.TEXTO_2024[:120], "motivo": "su propio texto"}]}))
        assert r["afirmaciones"][0]["veredicto"] == SUPPORTED

    def test_un_marcador_que_no_resuelve_no_busca_respaldo_en_otra_parte(self):
        import json
        from core.verificacion_semantica import NOT_DETERMINED
        r = self._run(json.dumps({"afirmaciones": [{
            "afirmacion": "x", "marcadores": ["C999"], "veredicto": "supported",
            "localizador": self.TEXTO_2024[:120], "motivo": "y"}]}))
        a = r["afirmaciones"][0]
        assert a["veredicto"] == NOT_DETERMINED
        assert a["integridad"] == "referencia_invalida"

    def test_sin_marcador_no_se_aprueba_por_coincidencia(self):
        import json
        from core.verificacion_semantica import NOT_DETERMINED
        r = self._run(json.dumps({"afirmaciones": [{
            "afirmacion": "x", "marcadores": [], "veredicto": "supported",
            "localizador": self.TEXTO_2024[:120], "motivo": "y"}]}))
        assert r["afirmaciones"][0]["veredicto"] == NOT_DETERMINED
        assert r["afirmaciones"][0]["integridad"] == "sin_referencia"

    # ── Los ejemplos, que es donde vive H16-A ────────────────────────
    def test_un_ejemplo_se_valida_contra_su_documento(self):
        import json
        from core.verificacion_semantica import NOT_DETERMINED
        r = self._run(json.dumps({"afirmaciones": [], "ejemplos": [{
            "documento": "VCN-005-2018",
            "propiedad_atribuida": "dos aumentos tratados como independientes",
            "veredicto": "supported",
            "localizador": self.TEXTO_2024[:120],
            "motivo": "el texto lo dice"}]}))
        e = r["ejemplos"][0]
        assert e["veredicto"] == NOT_DETERMINED
        assert e["integridad"] == "atribucion_no_acreditada"

    def test_el_ejemplo_correcto_pasa(self):
        import json
        from core.verificacion_semantica import SUPPORTED
        r = self._run(json.dumps({"afirmaciones": [], "ejemplos": [{
            "documento": "VCN-005-2024",
            "propiedad_atribuida": "dos aumentos tratados como independientes",
            "veredicto": "supported",
            "localizador": self.TEXTO_2024[:120], "motivo": "su texto"}]}))
        assert r["ejemplos"][0]["veredicto"] == SUPPORTED

    # ── Y el resumen tiene que distinguirlos ─────────────────────────
    def test_el_resumen_separa_atribucion_cruzada_de_referencia_rota(self):
        import json
        r = self._run(json.dumps({"afirmaciones": [
            {"afirmacion": "a", "marcadores": ["C5"], "veredicto": "supported",
             "localizador": self.TEXTO_2024[:120], "motivo": ""},
            {"afirmacion": "b", "marcadores": ["C999"], "veredicto": "supported",
             "localizador": self.TEXTO_2024[:120], "motivo": ""},
        ]}))
        assert r["resumen"]["atribucion_no_acreditada"] == 1
        assert r["resumen"]["referencias_invalidas"] == 1


class TestAmpliarNoEsRefutar:
    """
    §4.2 de la revisión del 25-sep. La instrucción anterior convertía toda
    generalización en `contradicted`:

        "Una afirmación que invierte, generaliza o suprime una condición de la
         fuente es contradicted, aunque el tema coincida."

    COFECE lo rechazó con razón: *"Una ampliación sin prueba suficiente no
    equivale siempre a una proposición refutada."* Una fuente que dice "un
    factor" no dice nada sobre exclusividad — no la refuta, no la sostiene.

    Marcar `contradicted` lo que es `not_determined` produce falsas alarmas, y
    las falsas alarmas son lo que vuelve tímido al agente.

    Lo que se prueba aquí es la instrucción, no el juicio del modelo: que el
    contrato pida distinguir los tres estados y no imponga palabras prohibidas.
    """

    def test_la_instruccion_distingue_los_tres_estados(self):
        from core.verificacion_semantica import _INSTRUCCIONES
        t = _INSTRUCCIONES.lower()
        assert "no queda por eso refutada" in t or "queda sin demostrar" in t
        assert "not_determined" in t and "contradicted" in t

    def test_ya_no_convierte_toda_generalizacion_en_contradiccion(self):
        from core.verificacion_semantica import _INSTRUCCIONES
        assert "generaliza o suprime una condición de la fuente es" not in \
            _INSTRUCCIONES

    def test_pide_separar_proposiciones_materiales(self):
        """H15-B son dos proposiciones: el factor existe, y es el único."""
        from core.verificacion_semantica import _INSTRUCCIONES
        assert "proposiciones materiales" in _INSTRUCCIONES
        assert "único" in _INSTRUCCIONES

    def test_no_impone_una_lista_de_palabras_prohibidas(self):
        """
        Su advertencia: la exclusividad puede estar expresada con otras
        palabras, y una exclusividad acreditada debe aceptarse.
        """
        from core.verificacion_semantica import _INSTRUCCIONES
        t = _INSTRUCCIONES.lower()
        assert "compara significados" in t
        assert "ninguna otra circunstancia incide" in t

    def test_conserva_que_suprimir_una_condicion_si_contradice(self):
        """H18: quitar "una vez que cause ejecutoria" sí cambia el efecto."""
        from core.verificacion_semantica import _INSTRUCCIONES
        assert "cause ejecutoria" in _INSTRUCCIONES
        assert "incondicional" in _INSTRUCCIONES


class TestNoSePuedeCalcularConUnaFechaTranscrita:
    """
    §6.3 de la revisión del 25-sep. El arreglo anterior cubría la mitad fácil:
    un campo presente en el registro con valor distinto se restauraba. Pero si
    el registro **no tenía** el campo, el valor que enviaba el modelo
    sobrevivía.

    Reproducido: origen sin `resolutionDate`, el modelo agrega 11-01-2024, y
    sale un promedio de 10.0 sin aviso. Una fecha que el registro no tiene no
    se puede completar con una transcripción.

    Y el segundo defecto: en H04 el modelo llamó la herramienta sin pedir
    estadísticas, así que el promedio nunca se calculó como operación. El 63.4
    lo enunció leyendo el desglose — correcto y no reconstruible.
    """

    def _ag(self):
        from agent.agent import NormaPlusAgent
        from temporal.analyzer import TemporalAnalyzer
        from temporal.holidays import HolidayCalendar
        ag = NormaPlusAgent.__new__(NormaPlusAgent)
        ag.temporal = TemporalAnalyzer(HolidayCalendar("data/dias_inhabiles.xlsx"))
        return ag

    def _calc(self, enviado, origen, **extra):
        import asyncio
        from agent.turn_state import TurnState
        st = TurnState()
        st.last_expedientes = [dict(r) for r in origen]
        args = {"expedientes": enviado, "campo_inicio": "startAgreementDate",
                "campo_fin": "resolutionDate", "unidad": "dias_naturales"}
        args.update(extra)
        r = asyncio.run(self._ag()._exec_calcular_plazos(args, None, st))
        return r, st

    # ── Campo ausente en el origen ───────────────────────────────────
    def test_un_campo_que_el_registro_no_tiene_no_se_acepta(self):
        origen = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024"}]
        enviado = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024",
                    "resolutionDate": "11-01-2024"}]
        r, _ = self._calc(enviado, origen, compute_stats=True)
        assert (r.get("stats") or {}).get("count") == 0, (
            "no se puede promediar con una fecha que el registro no tiene")

    def test_y_se_declara_con_su_motivo(self):
        origen = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024"}]
        enviado = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024",
                    "resolutionDate": "11-01-2024"}]
        r, _ = self._calc(enviado, origen, compute_stats=True)
        caso = r["FECHAS_CORREGIDAS_DESDE_EL_REGISTRO"]["casos"][0]
        assert caso["campo"] == "resolutionDate"
        assert caso["valor_del_registro"] is None
        assert "no tiene este campo" in caso["motivo"]

    def test_un_campo_presente_y_distinto_sigue_restaurandose(self):
        """El arreglo anterior no puede perderse."""
        origen = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024",
                   "resolutionDate": "11-01-2024"}]
        enviado = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024",
                    "resolutionDate": "31-01-2024"}]
        r, _ = self._calc(enviado, origen, compute_stats=True)
        assert r["stats"]["promedio"] == 10.0

    def test_un_campo_no_calculable_del_modelo_no_pasa_por_la_puerta_de_atras(self):
        """Ni siquiera con otro nombre de campo de fecha."""
        origen = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024"}]
        enviado = [{"caseLink": "A-1", "startAgreementDate": "01-01-2024",
                    "judgmentDate": "11-01-2024"}]
        r, _ = self._calc(enviado, origen, campo_fin="judgmentDate",
                          compute_stats=True)
        assert (r.get("stats") or {}).get("count") == 0

    # ── La operación se registra siempre ─────────────────────────────
    H04 = [("VCN-005-2024", "25-10-2024", "20-12-2024"),
           ("VCN-004-2024", "03-10-2024", "21-11-2024"),
           ("VCN-005-2023", "26-08-2024", "24-10-2024"),
           ("VCN-003-2024", "18-06-2024", "05-09-2024"),
           ("VCN-001-2024", "01-02-2024", "15-04-2024")]

    def test_el_promedio_queda_registrado_aunque_no_se_pida(self):
        """
        El fixture de H04 de COFECE: suma 317, n 5, media 63.4. Antes salía
        `stats=null` porque el modelo no pidió estadísticas.
        """
        regs = [{"caseLink": c, "startAgreementDate": i, "resolutionDate": f}
                for c, i, f in self.H04]
        _, st = self._calc([dict(x) for x in regs], regs)   # sin compute_stats
        op = st.computation_audit[0]["operacion_agregada"]
        assert op["suma"] == 317
        assert op["n"] == 5
        assert op["promedio"] == 63.4
        assert op["solicitada_por_el_modelo"] is False

    def test_la_operacion_solo_cuenta_los_elegibles(self):
        regs = [{"caseLink": c, "startAgreementDate": i, "resolutionDate": f}
                for c, i, f in self.H04]
        regs.append({"caseLink": "A-9", "startAgreementDate": None,
                     "resolutionDate": "11-01-2024"})
        _, st = self._calc([dict(x) for x in regs], regs)
        op = st.computation_audit[0]["operacion_agregada"]
        assert op["n"] == 5 and "A-9" not in op["ids"]

    def test_sin_valores_no_se_inventa_una_operacion(self):
        origen = [{"caseLink": "A-1", "startAgreementDate": None,
                   "resolutionDate": None}]
        _, st = self._calc([dict(x) for x in origen], origen)
        assert st.computation_audit[0]["operacion_agregada"] is None


class TestLaCorridaSeIdentificaYLaSuiteViajaCompleta:
    """
    Dos defectos de ENTREGA que señaló COFECE el 25-sep, y los dos hacían que
    una candidata congelada no fuera verificable.

    Las 60 trazas decían `agent_git_sha: unknown`, porque sólo se leía la
    variable que pone el despliegue. Y el paquete llevó un archivo de pruebas
    de cuatro, así que el "299 aprobadas" no se podía reconciliar: contaron 212
    métodos en lo entregado.

    Ninguno se arregla recordando hacerlo: van en el código que arma el ZIP.
    """

    def test_la_traza_identifica_el_commit(self):
        import shutil, subprocess
        from pathlib import Path
        from core.tracing.versioning import _git_sha
        if not shutil.which("git"):
            import pytest; pytest.skip("sin git")
        raiz = Path(__file__).resolve().parents[1]
        if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=raiz,
                          capture_output=True).returncode != 0:
            import pytest; pytest.skip("no es un repo git")
        sha = _git_sha()
        assert sha != "unknown"
        assert len(sha) >= 12

    def test_un_arbol_sucio_lo_dice(self):
        """
        Una corrida desde un árbol con cambios sin commitear no es
        reproducible. Decirlo vale más que un SHA que sugiere que sí lo es.
        """
        import inspect
        from core.tracing import versioning
        src = inspect.getsource(versioning._git_sha)
        assert "sucio" in src and "status" in src

    def test_el_zip_lleva_todos_los_archivos_de_prueba(self):
        import inspect
        from core.tracing import artifacts
        src = inspect.getsource(artifacts._codigo)
        assert 'pruebas.glob("*.py")' in src, (
            "el ZIP debe copiar la suite completa, no un archivo elegido a mano")
        assert "COMO_EJECUTAR" in src, "y el comando exacto para reejecutarla"


class TestAvisosDeSuspensionSinDecidirSuAplicacion:
    """
    §8 de la revisión del 25-sep, con el alcance que Imanol aprobó:

        "Contar días hábiles conforme al calendario general de la autoridad y
         mostrar por separado acuerdos de suspensión coincidentes, con fechas y
         enlaces, sin decidir ni descontar su aplicación al expediente."

    Eso desbloqueó lo que arrastrábamos desde el 22-sep. Habíamos pedido que
    alguien decidiera si las concentraciones estaban en la excepción de
    CFCE-084-2020, porque sin esa respuesta siete VCN no tenían número
    defendible. La decisión fue mejor que la pregunta: no hace falta decidirlo.
    """

    SIETE = [
        ("VCN-001-2020", "2020-03-02", "2020-07-13", ["S01","S02","S03","S04","S05","S06"]),
        ("VCN-002-2020", "2020-03-20", "2020-04-16", ["S01"]),
        ("VCN-003-2020", "2020-05-25", "2020-07-22", ["S03","S04","S05","S06"]),
        ("VCN-004-2020", "2020-06-18", "2020-07-22", ["S05","S06"]),
        ("VCN-005-2020", "2020-11-24", "2021-02-04", ["S07","S08","S09","S10"]),
        ("VCN-001-2025", "2025-08-05", "2025-08-28", ["S14"]),
        ("VCN-002-2024", "2025-06-20", "2025-09-25", ["S14"]),
    ]

    def _cat(self):
        from temporal.avisos import CatalogoAvisos
        return CatalogoAvisos.desde_directorio("data/calendario")

    def _d(self, s):
        import datetime as dt
        return dt.date.fromisoformat(s)

    def test_los_siete_intervalos_recuperan_sus_acuerdos(self):
        """Su criterio de aceptación, tal cual lo tabuló."""
        cat = self._cat()
        for exp, i, f, esperado in self.SIETE:
            avisos = cat.avisos_para(self._d(i), self._d(f), "COFECE") or \
                     cat.avisos_para(self._d(i), self._d(f))
            assert [a["id"] for a in avisos] == esperado, exp

    def test_ningun_aviso_decide_su_aplicacion(self):
        from temporal.avisos import NO_EVALUADA
        cat = self._cat()
        avisos = cat.avisos_para(self._d("2020-03-20"), self._d("2020-04-16"))
        assert avisos
        for a in avisos:
            assert a["aplicabilidad_al_expediente"] == NO_EVALUADA

    def test_el_aviso_lleva_periodo_y_enlace(self):
        """Sin fechas ni fuente, el usuario no puede revisar la aplicación."""
        cat = self._cat()
        a = cat.avisos_para(self._d("2020-03-20"), self._d("2020-04-16"))[0]
        assert a["periodo_inicio"] == "2020-03-23"
        assert a["periodo_fin"] == "2020-04-17"
        assert a["url"] and a["url"].startswith("http")
        assert a["acuerdo"] == "CFCE-084-2020"

    def test_un_periodo_ajeno_no_produce_avisos(self):
        cat = self._cat()
        assert cat.avisos_para(self._d("2016-01-01"), self._d("2016-03-01")) == []

    def test_no_se_duplica_el_mismo_acuerdo(self):
        cat = self._cat()
        avisos = cat.avisos_para(self._d("2020-01-01"), self._d("2021-12-31"))
        ids = [a["id"] for a in avisos]
        assert len(ids) == len(set(ids))

    # ── Las dos coberturas, separadas ────────────────────────────────
    def test_un_periodo_sin_calendario_confirmado_se_declara(self):
        """C01: el calendario de 2018 no se revalidó."""
        c = self._cat().cobertura_calendario_general(
            self._d("2018-05-01"), self._d("2018-06-01"))
        assert c["completa"] is False
        assert "C01" in [p["id"] for p in c["periodos_sin_confirmar"]]

    def test_un_periodo_con_calendario_confirmado_pasa(self):
        c = self._cat().cobertura_calendario_general(
            self._d("2020-03-20"), self._d("2020-04-16"))
        assert c["completa"] is True

    def test_una_limitacion_de_avisos_no_toca_la_cobertura_del_conteo(self):
        """
        Textual: "Un pendiente de otro periodo no bloquea la operación ajena a
        él." C04 es sobre excepciones COVID: limita los avisos, no el conteo.
        """
        cat = self._cat()
        ini, fin = self._d("2020-04-20"), self._d("2020-06-12")
        assert cat.cobertura_calendario_general(ini, fin)["completa"] is True
        av = cat.cobertura_avisos(ini, fin)
        assert av["completa"] is False
        assert "C04" in [x["id"] for x in av["limitaciones"]]

    def test_un_catalogo_ausente_no_dice_que_no_hubo_suspensiones(self):
        """
        Un error de lectura no puede convertirse en "no hubo acuerdos": son
        cosas distintas y COFECE lo señala expresamente.
        """
        from temporal.avisos import CatalogoAvisos
        vacio = CatalogoAvisos.desde_directorio("data/no_existe")
        assert vacio.cargado is False
        c = vacio.cobertura_avisos(self._d("2020-03-20"), self._d("2020-04-16"))
        assert c["completa"] is False and c["catalogo_cargado"] is False

    def test_un_acuerdo_sin_periodo_se_declara_y_no_se_inventa(self):
        """
        S11 y S12 no tienen inicio ni fin: su rango está pendiente de
        normalizar. No se les puede calcular coincidencia, y saltarlos en
        silencio afirmaría una lista completa que no lo es.

        Lo encontró esta prueba: la primera versión los descartaba sin decir
        nada. Control C03: "Fin vacío NO significa suspensión indefinida."
        """
        cat = self._cat()
        sin_periodo = cat.acuerdos_sin_periodo()
        assert {s["id"] for s in sin_periodo} == {"S11", "S12"}
        for s in sin_periodo:
            assert "no está normalizado" in s["motivo"]

    def test_esos_acuerdos_dejan_la_cobertura_de_avisos_incompleta(self):
        cat = self._cat()
        c = cat.cobertura_avisos(self._d("2016-01-01"), self._d("2016-03-01"))
        assert c["completa"] is False
        assert {s["id"] for s in c["acuerdos_sin_periodo"]} == {"S11", "S12"}

    def test_pero_no_se_presentan_como_coincidencias(self):
        """Declarar la limitación no es inventar una coincidencia."""
        cat = self._cat()
        avisos = cat.avisos_para(self._d("2013-01-01"), self._d("2030-01-01"))
        assert "S11" not in [a["id"] for a in avisos]

    def test_la_cifra_lleva_su_etiqueta_de_alcance(self):
        from temporal.avisos import ETIQUETA_ALCANCE
        assert "sin ajustar suspensiones" in ETIQUETA_ALCANCE


class TestLaCifraDeHabilesLlevaSuAlcanceYSusAvisos:
    """
    La prueba de aceptación que COFECE fijó para el calendario, textual:

        "VCN-002-2020 con las fuentes del ejemplo: 27 naturales y 14 hábiles de
         calendario general; inicio excluido, fin incluido; aviso CFCE-084-2020
         con periodo y enlace. No declara una decisión de excepción."

    La etiqueta no es decorativa: esta cuenta describe tiempo transcurrido según
    el calendario ordinario, no tiempo procesal efectivo, y sin decirlo se lee
    como si lo fuera.
    """

    REG = [{"caseLink": "VCN-002-2020", "authority": "COFECE",
            "startAgreementDate": "20-03-2020", "resolutionDate": "16-04-2020"}]

    def _calc(self, unidad="dias_habiles", con_catalogo=True):
        import asyncio
        from agent.agent import NormaPlusAgent
        from agent.turn_state import TurnState
        from temporal.analyzer import TemporalAnalyzer
        from temporal.holidays import HolidayCalendar
        from temporal.avisos import CatalogoAvisos
        ag = NormaPlusAgent.__new__(NormaPlusAgent)
        ag.temporal = TemporalAnalyzer(HolidayCalendar("data/dias_inhabiles.xlsx"))
        ag.avisos = CatalogoAvisos.desde_directorio(
            "data/calendario" if con_catalogo else "data/no_existe")
        st = TurnState()
        st.last_expedientes = [dict(r) for r in self.REG]
        return asyncio.run(ag._exec_calcular_plazos(
            {"expedientes": [dict(x) for x in self.REG],
             "campo_inicio": "startAgreementDate",
             "campo_fin": "resolutionDate", "unidad": unidad}, None, st))

    def test_las_dos_cifras_del_ejemplo(self):
        e = self._calc()["expedientes"][0]
        assert e["dias_naturales"] == 27
        assert e["dias_habiles"] == 14

    def test_la_cifra_declara_que_no_ajusta_suspensiones(self):
        r = self._calc()
        a = r["ALCANCE_DE_LA_CIFRA"]
        assert "sin ajustar suspensiones" in a["denominacion"]
        assert a["ajusta_suspensiones"] is False
        assert "excluye el día inicial" in a["convencion"]

    def test_la_regla_prohibe_presentarla_como_vencimiento(self):
        r = self._calc()
        assert "vencimiento" in r["ALCANCE_DE_LA_CIFRA"]["regla"]

    def test_el_acuerdo_coincidente_se_informa_con_periodo_y_enlace(self):
        r = self._calc()
        acuerdos = r["ACUERDOS_DE_SUSPENSION_COINCIDENTES"]["acuerdos"]
        s01 = next(a for a in acuerdos if a["id"] == "S01")
        assert s01["acuerdo"] == "CFCE-084-2020"
        assert s01["periodo_inicio"] == "2020-03-23"
        assert s01["periodo_fin"] == "2020-04-17"
        assert s01["url"].startswith("http")

    def test_no_decide_si_la_suspension_aplica(self):
        r = self._calc()
        for a in r["ACUERDOS_DE_SUSPENSION_COINCIDENTES"]["acuerdos"]:
            assert a["aplicabilidad_al_expediente"] == "no_evaluada"
        regla = r["ACUERDOS_DE_SUSPENSION_COINCIDENTES"]["regla"]
        assert "NO afirmes que aplican" in regla

    def test_no_se_descuenta_ni_un_dia_por_el_aviso(self):
        """
        S01 cubre 23-mar a 17-abr, casi toda la ventana. Si se descontara,
        los hábiles no serían 14.
        """
        assert self._calc()["expedientes"][0]["dias_habiles"] == 14

    def test_los_dias_naturales_no_llevan_esta_etiqueta(self):
        """La etiqueta es de la métrica de hábiles; los naturales no la usan."""
        assert "ALCANCE_DE_LA_CIFRA" not in self._calc(unidad="dias_naturales")

    def test_sin_catalogo_se_declara_incompleta_la_revision(self):
        """
        No se puede presentar una lista vacía como exhaustiva cuando el
        catálogo no se pudo leer.
        """
        r = self._calc(con_catalogo=False)
        cob = r.get("COBERTURA_DEL_CALENDARIO") or {}
        assert "revision_de_suspensiones_incompleta" in cob
        assert "no afirmes que no existen acuerdos" in cob["regla_avisos"].lower()
        assert "ACUERDOS_DE_SUSPENSION_COINCIDENTES" not in r


class TestUnEjemploTieneQueDemostrarSuPropiedad:
    """
    H16-A, el FAIL CRÍTICO. La pregunta pide "una resolución VCN en la que dos
    aumentos de capital se hayan tratado como operaciones independientes", y la
    respuesta presentó VCN-005-2018 apoyándose en el criterio 4035, que es de
    VCN-005-2024. El documento existía y el pasaje era auténtico; la atribución
    no. La traza tenía `requisitos=[]`: no había ninguna defensa.

    El patrón es estructural y cubre los tres frentes abiertos, que es la señal
    de que es el mecanismo y no un parche por pregunta:

        H16  "Busca una resolución VCN en la que…"        1 resolución
        H08  "Busca una resolución VCN que lo explique"   1 resolución
        H17  "Muéstrame dos sentencias… que lo expliquen" 2 sentencias

    COFECE es explícito en que no puede activarse por una palabra del dominio ni
    codificarse por número de pregunta. El código comprueba cantidad, tipo y
    procedencia; **que el pasaje demuestre la propiedad lo juzga el modelo**.
    """

    U = ["VCN-005-2018", "VCN-005-2024", "43_2021_3JD", "96_2023_2TCC"]
    Q16 = ("¿Qué significa que una concentración se realice mediante una "
           "sucesión de actos? Busca una resolución VCN en la que dos aumentos "
           "de capital se hayan tratado como operaciones independientes.")
    Q17 = ("¿El plazo puede empezar a correr si ya conoce el acto? Muéstrame "
           "dos sentencias relacionadas con VCN que lo expliquen y qué "
           "condiciones exigen.")

    def _req(self, q):
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        return construir_requisitos(q, ResolutorDeIdentidades(self.U).resolver(q))

    def _comp(self, q, docs):
        from core.requisitos import verificar
        v = verificar(self._req(q), docs)
        return next(c for c in v["componentes"] if c["tipo"] == "ejemplo")

    def _crit(self, cl, i="c1"):
        return {"id": i, "metadata": {"id_expediente": cl}, "content": "texto"}

    # ── La detección ─────────────────────────────────────────────────
    def test_reconoce_cantidad_y_tipo_documental(self):
        e16 = next(r for r in self._req(self.Q16) if r["tipo"] == "ejemplo")
        e17 = next(r for r in self._req(self.Q17) if r["tipo"] == "ejemplo")
        assert (e16["valor"], e16["tipo_documento"]) == (1, "resolucion")
        assert (e17["valor"], e17["tipo_documento"]) == (2, "sentencia")

    def test_conserva_la_propiedad_en_palabras_del_usuario(self):
        e = next(r for r in self._req(self.Q16) if r["tipo"] == "ejemplo")
        assert "independientes" in e["propiedad"]

    def test_no_se_activa_por_una_palabra_del_dominio(self):
        """
        Mencionar la propiedad sin pedir ejemplares no genera el requisito. Si
        se activara por "independientes", sería la regla que COFECE prohíbe.
        """
        req = self._req("¿Cuándo se consideran dos aumentos de capital "
                        "operaciones independientes?")
        assert not [r for r in req if r["tipo"] == "ejemplo"]

    def test_una_pregunta_sin_peticion_de_ejemplares_no_lo_genera(self):
        req = self._req("¿Qué multa se impuso en el VCN-005-2024?")
        assert not [r for r in req if r["tipo"] == "ejemplo"]

    # ── La verificación ──────────────────────────────────────────────
    def test_dos_fragmentos_del_mismo_documento_cuentan_como_uno(self):
        """Su criterio de aceptación para H17, textual."""
        c = self._comp(self.Q17, [self._crit("43_2021_3JD", "c1"),
                                  self._crit("43_2021_3JD", "c2")])
        assert not c["cumple"]
        assert "cuentan como uno" in c["detalle"]

    def test_dos_sentencias_distintas_cumplen(self):
        c = self._comp(self.Q17, [self._crit("43_2021_3JD"),
                                  self._crit("96_2023_2TCC")])
        assert c["cumple"]

    def test_una_resolucion_no_satisface_una_peticion_de_sentencias(self):
        c = self._comp(self.Q17, [self._crit("VCN-005-2018"),
                                  self._crit("VCN-005-2024")])
        assert not c["cumple"]
        assert "no es una resolución" in c["detalle"]

    def test_un_registro_sin_criterio_no_demuestra_nada(self):
        c = self._comp(self.Q16, [{"caseLink": "VCN-005-2024",
                                   "authority": "COFECE"}])
        assert not c["cumple"]
        assert "no demuestra" in c["detalle"]

    def test_el_codigo_no_pretende_juzgar_la_pertinencia(self):
        """
        Lo dice en el propio detalle, porque es el límite del mecanismo: con el
        documento correcto recuperado, sigue siendo el modelo el que tiene que
        sostener que el pasaje demuestra la propiedad.
        """
        c = self._comp(self.Q16, [self._crit("VCN-005-2024")])
        assert c["cumple"]
        assert "no que el pasaje demuestre" in c["detalle"]


class TestAmpliarDentroDelPrecedente:
    """
    §4.4 de la revisión del 25-sep. H08 pide "una resolución VCN que lo
    explique" y las tres corridas hicieron dos búsquedas ABIERTAS, omitiendo la
    evaluación conjunta de actos. El criterio que la sostiene —4212 de
    VCN-001-2025— nunca llegó al contexto.

    Verificado contra staging el 26-sep: no estaba fuera de alcance. Filtrando
    por ese documento sale en posición 2 o 3. Estaba fuera del top-k de una
    consulta que no acotaba documento.

    **El selector es el ranking, no el conteo.** La primera versión de esto
    eligió "el documento que más aportó" y amplió sobre dos documentos
    irrelevantes, llevando la evidencia de 20 a 66 entradas. El conteo amplifica
    lo que la búsqueda abierta devolvió más, que no es pertinencia.
    """

    class _Cli:
        """Devuelve por documento, en orden de ranking."""
        def __init__(self, abierta, por_doc):
            self.abierta, self.por_doc, self.llamadas = abierta, por_doc, []

        async def search(self, query, top_k=15, filters=None, collector=None):
            cl = (filters or {}).get("caseLink")
            self.llamadas.append(cl)
            return self.abierta if cl is None else self.por_doc.get(cl, [])

    class _Crit:
        def __init__(self, cid, cl): self.id, self.cl = cid, cl
        def model_dump(self):
            return {"id": self.id, "metadata": {"id_expediente": self.cl},
                    "content": f"texto {self.id}"}

    def _agente(self, cli, limite=12):
        from agent.agent import NormaPlusAgent
        ag = NormaPlusAgent.__new__(NormaPlusAgent)
        ag.criterios = cli
        ag.max_http_requests = limite
        return ag

    def _estado(self, con_ejemplo=True):
        from agent.turn_state import TurnState
        st = TurnState()
        if con_ejemplo:
            st.requisitos = [{"tipo": "ejemplo", "valor": 1,
                              "tipo_documento": "resolucion",
                              "propiedad": "x", "descripcion": "d",
                              "obligatorio": True}]
        return st

    def _correr(self, ag, st, abierta):
        import asyncio
        return asyncio.run(ag._ampliar_precedente("q", abierta, None, st))

    def test_amplia_sobre_el_mejor_rankeado_no_sobre_el_que_mas_aporta(self):
        """
        `B` aparece tres veces y `A` una, pero `A` va primero. El ranking del
        servicio es la señal de pertinencia; el conteo no.
        """
        C = self._Crit
        abierta = [C("1", "A"), C("2", "B"), C("3", "B"), C("4", "B")]
        cli = self._Cli(abierta, {"A": [C("9", "A")], "B": [C("8", "B")]})
        ag, st = self._agente(cli), self._estado()
        self._correr(ag, st, abierta)
        assert cli.llamadas[0] == "A"

    def test_solo_amplia_si_la_pregunta_pide_ejemplares(self):
        C = self._Crit
        abierta = [C("1", "A")]
        cli = self._Cli(abierta, {"A": [C("9", "A")]})
        ag, st = self._agente(cli), self._estado(con_ejemplo=False)
        r = self._correr(ag, st, abierta)
        assert cli.llamadas == [] and len(r) == 1

    def test_una_sola_vez_por_turno(self):
        C = self._Crit
        abierta = [C("1", "A")]
        cli = self._Cli(abierta, {"A": [C("9", "A")]})
        ag, st = self._agente(cli), self._estado()
        self._correr(ag, st, abierta)
        n = len(cli.llamadas)
        self._correr(ag, st, abierta)
        assert len(cli.llamadas) == n

    def test_no_repite_lo_que_ya_estaba(self):
        C = self._Crit
        abierta = [C("1", "A")]
        cli = self._Cli(abierta, {"A": [C("1", "A"), C("9", "A")]})
        ag, st = self._agente(cli), self._estado()
        r = self._correr(ag, st, abierta)
        assert [x.id for x in r] == ["1", "9"]

    def test_descarta_lo_que_no_es_del_documento(self):
        """El filtro de la API es substring, no igualdad."""
        C = self._Crit
        abierta = [C("1", "A")]
        cli = self._Cli(abierta, {"A": [C("9", "A_2025_10_09"), C("7", "A")]})
        ag, st = self._agente(cli), self._estado()
        r = self._correr(ag, st, abierta)
        assert [x.id for x in r] == ["1", "7"]

    def test_acota_cuantos_pasajes_trae(self):
        """
        El tope importa: una primera versión traía 23 por documento y llenó la
        evidencia de ruido.
        """
        from agent.agent import NormaPlusAgent
        C = self._Crit
        abierta = [C("1", "A")]
        muchos = [C(str(100 + i), "A") for i in range(30)]
        cli = self._Cli(abierta, {"A": muchos})
        ag, st = self._agente(cli), self._estado()
        r = self._correr(ag, st, abierta)
        assert len(r) == 1 + NormaPlusAgent._AMPLIAR_PASAJES

    def test_bajo_el_tope_la_cobertura_es_completa(self):
        """
        Con el filtro exacto del 27-sep, `devueltos < tope` sí acredita que se
        vio el documento entero: ya no hay resultados ajenos gastando el cupo.
        Antes no valía, porque el substring mezclaba actos.
        """
        C = self._Crit
        abierta = [C("1", "A")]
        cli = self._Cli(abierta, {"A": [C("9", "A"), C("8", "A")]})
        ag, st = self._agente(cli), self._estado()
        self._correr(ag, st, abierta)
        amp = st.ampliacion_precedente[0]
        assert amp["cobertura"] == "completa"
        assert amp["motivo_limite"] is None

    def test_al_alcanzar_el_tope_no_se_afirma_cobertura(self):
        """
        COFECE: no decir "no hay más condiciones" porque volvió un tope.
        """
        from agent.agent import NormaPlusAgent
        C = self._Crit
        abierta = [C("1", "A")]
        muchos = [C(str(100 + i), "A") for i in range(NormaPlusAgent._TOPE_AMPLIACION)]
        cli = self._Cli(abierta, {"A": muchos})
        ag, st = self._agente(cli), self._estado()
        self._correr(ag, st, abierta)
        amp = st.ampliacion_precedente[0]
        assert amp["cobertura"] == "parcial"
        assert "puede tener más criterios" in amp["motivo_limite"]

    def test_respeta_el_presupuesto(self):
        C = self._Crit
        abierta = [C("1", "A"), C("2", "B")]
        cli = self._Cli(abierta, {"A": [C("9", "A")], "B": [C("8", "B")]})
        ag, st = self._agente(cli, limite=1), self._estado()
        self._correr(ag, st, abierta)
        assert len(cli.llamadas) == 1
        assert any("presupuesto" in (x.get("motivo_limite") or "")
                   for x in st.ampliacion_precedente)

    def test_un_error_del_servicio_no_tumba_la_busqueda(self):
        C = self._Crit

        class _Roto(self._Cli):
            async def search(self, query, top_k=15, filters=None, collector=None):
                if (filters or {}).get("caseLink"):
                    raise RuntimeError("502")
                return self.abierta

        abierta = [C("1", "A")]
        cli = _Roto(abierta, {})
        ag, st = self._agente(cli), self._estado()
        r = self._correr(ag, st, abierta)
        assert [x.id for x in r] == ["1"]
        assert st.ampliacion_precedente[0]["motivo_limite"] == "RuntimeError"


class TestCoberturaSeparadaDelRespaldo:
    """
    §4.4/§5 de la revisión del 25-sep: "Separar respaldo de completitud."

    Una respuesta puede tener todas sus frases respaldadas y dejar fuera algo
    que la evidencia sostenía. Es el caso de H08, y no es una frase falsa:
    contarlo como contradicción sería el error opuesto.

    Lo que se prueba aquí es el contrato determinista. **Que el revisor detecte
    la omisión es otra cosa, y medida no la detecta** — ver el commit.
    """

    EV = {"C1": {"documento": "VCN-001-2025", "anchor": "",
                 "texto": "La adquisición del control no se produce "
                          "exclusivamente mediante acciones."},
          "C2": {"documento": "VCN-001-2025", "anchor": "",
                 "texto": "Los actos relacionados, considerados en conjunto, "
                          "producen la adquisición de control."}}

    class _Ad:
        def __init__(self, p): self.p = p
        async def quick_completion(self, messages, model, max_tokens=50):
            return self.p

    def _run(self, payload):
        import asyncio
        from core.verificacion_semantica import verificar
        return asyncio.run(verificar("pregunta", "borrador", self.EV,
                                     self._Ad(payload), "m"))

    def test_un_componente_incompleto_se_distingue_de_uno_omitido(self):
        import json
        r = self._run(json.dumps({"afirmaciones": [], "cobertura": [
            {"componente": "qué mecanismos", "estado": "cubierto_parcial",
             "evidencia_disponible": ["C1", "C2"],
             "evidencia_sin_usar": ["C2"], "motivo": "usó sólo C1"}]}))
        c = r["cobertura"][0]
        assert c["estado"] == "cubierto_parcial"
        assert c["evidencia_sin_usar"] == ["C2"]
        assert r["resumen"]["componentes_incompletos"] == 1
        assert r["resumen"]["componentes_omitidos"] == 0

    def test_no_se_declara_incompleto_sin_señalar_qué_quedó_sin_usar(self):
        """Sin el marcador concreto es una opinión sobre lo que 'debería' decir."""
        import json
        r = self._run(json.dumps({"afirmaciones": [], "cobertura": [
            {"componente": "x", "estado": "cubierto_parcial",
             "evidencia_disponible": ["C1"], "motivo": "falta algo"}]}))
        assert r["cobertura"][0]["estado"] == "cubierto"

    def test_no_se_declara_omitido_con_evidencia_inexistente(self):
        import json
        r = self._run(json.dumps({"afirmaciones": [], "cobertura": [
            {"componente": "x", "estado": "omitido",
             "evidencia_disponible": ["C99"], "motivo": "y"}]}))
        assert r["cobertura"][0]["estado"] == "no_resuelto"

    def test_la_cobertura_no_es_un_veredicto_de_falsedad(self):
        """
        Un componente incompleto no cuenta como contradicción: son dimensiones
        distintas y mezclarlas es el error que COFECE señaló en H08-C.
        """
        import json
        r = self._run(json.dumps({"afirmaciones": [], "cobertura": [
            {"componente": "x", "estado": "omitido",
             "evidencia_disponible": ["C2"], "motivo": "y"}]}))
        assert r["resumen"]["contradicted"] == 0
        assert r["resumen"]["componentes_omitidos"] == 1


class TestLosIndicadoresSeReportanDesdeLaHerramienta:
    """
    Dos cifras falsas llegaron a COFECE por no usar la herramienta que existe.

    El 23-sep les mandamos "tool esperada no llamada: 0/0/0" cuando eran 7/7/7:
    el campo es un *string* con el nombre de la herramienta, y un agregador
    propio hacía `len(v) if isinstance(v, list) else 0`. El 27-sep estuvo a
    punto de repetirse con `citations_unresolved`, que también es string.

    `compare.py` nunca tuvo ese defecto —cuenta por veracidad, que funciona
    igual para booleanos, strings y conteos—. El problema era que su lista de
    indicadores se quedó en agosto, así que lo que faltaba se reportaba desde un
    script suelto.
    """

    # Lo que se le reporta a COFECE en cada entrega.
    REPORTADOS = [
        "citations_unresolved", "scope_mismatch", "errors",
        "exhaustive_but_truncated", "ausencia_sin_complemento", "abstained",
        "coverage_truncated", "tools_expected_not_called",
    ]

    def test_todo_lo_que_reportamos_esta_en_la_lista(self):
        from core.tracing.compare import INDICADORES
        declarados = {c for c, _ in INDICADORES}
        faltan = [c for c in self.REPORTADOS if c not in declarados]
        assert not faltan, (
            f"estos indicadores se reportan y la herramienta no los compara: "
            f"{faltan}. Agrégalos a INDICADORES en core/tracing/compare.py")

    def test_se_cuentan_por_veracidad_no_por_tipo(self):
        """
        Es lo que hace correcto el conteo sin adivinar el tipo. Si alguien lo
        cambia por una suma, los campos string vuelven a contar cero.
        """
        import inspect
        from core.tracing import compare
        src = inspect.getsource(compare.comparar) \
            if hasattr(compare, "comparar") else inspect.getsource(compare)
        assert "sum(1 for q in comunes if r_a[q].get(campo))" in src, (
            "el conteo debe ser por veracidad: un campo string no se suma")

    def test_un_cero_confirmado_no_se_oculta(self):
        """
        Antes los indicadores en cero se saltaban, así que un cero confirmado y
        un campo que ni se midió se veían igual: como un renglón ausente.
        Cuando se reporta "0 en las tres", esa distinción es lo que hay que
        poder demostrar.
        """
        import inspect
        from core.tracing import compare
        src = inspect.getsource(compare)
        assert "if a == b == 0:\n            continue" not in src
        assert "el campo no está en estas corridas" in src

    def test_ningun_indicador_de_la_lista_esta_repetido(self):
        from core.tracing.compare import INDICADORES, NEUTROS
        campos = [c for c, _ in INDICADORES] + [c for c, _ in NEUTROS]
        assert len(campos) == len(set(campos))

    def test_los_contadores_de_conducta_no_llevan_juicio(self):
        """
        `second_retrieval` y `used_cached_evidence` describen lo que hizo el
        agente, no si lo hizo bien: una segunda búsqueda puede ser exactamente
        lo correcto. Marcarlos "empeora" hace leer una regresión donde no la
        hay, y esa lectura llega a COFECE en la tabla.
        """
        from core.tracing.compare import INDICADORES, NEUTROS
        calidad = {c for c, _ in INDICADORES}
        assert "second_retrieval" not in calidad
        assert "used_cached_evidence" not in calidad
        assert {"second_retrieval", "used_cached_evidence"} == {c for c, _ in NEUTROS}

    def test_los_neutros_se_imprimen_sin_flecha(self):
        import inspect
        from core.tracing import compare
        src = inspect.getsource(compare)
        bloque = src[src.index("Conducta (no son mejor ni peor)"):][:400]
        assert "mejora" not in bloque and "empeora" not in bloque


class TestLaRelacionConElPrincipalEsUnDatoDelRegistro:
    """
    H01 pide "a qué expediente corresponde" cada resolución de cumplimiento.
    Hasta el 27-sep sólo podía inferirse del sufijo del identificador, y COFECE
    lo prohibió expresamente: *"No eliminar sufijos para fabricar la relación."*

    Ese día José Miguel empezó a entregar `parent` con el `caseLink` del
    principal, a petición nuestra. Llega en los cuatro actos de cumplimiento del
    universo, que son justo los de H01.

    `ExpedienteRecord` no lo declaraba, así que Pydantic lo descartaba en
    silencio: la sexta aparición de esa familia de falla.
    """

    CRUDO = {"caseLink": "VCN-004-2022_2025_10_09",
             "parent": {"id": 25062, "caseLink": "VCN-004-2022"},
             "authority": "COFECE", "resolutionDate": "09-10-2025"}

    def test_el_registro_declara_el_campo(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(**self.CRUDO)
        assert r.parent_case_link == "VCN-004-2022"

    def test_sin_relacion_devuelve_none_y_no_la_inventa(self):
        from models.schemas import ExpedienteRecord
        r = ExpedienteRecord(caseLink="VCN-004-2022_2025_10_09")
        assert r.parent_case_link is None, (
            "el sufijo no es una relación acreditada")

    def test_al_modelo_llega_el_identificador_no_el_objeto(self):
        """El `id` interno del servicio es ruido; el caseLink sí se puede usar."""
        from models.schemas import ExpedienteRecord
        d = ExpedienteRecord(**self.CRUDO).para_prompt()
        assert d["expediente_principal"] == "VCN-004-2022"
        assert "parent" not in d

    def test_h01_exige_el_principal(self):
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        q = ("¿Qué resoluciones de VCN dictadas en cumplimiento de amparo "
             "tienes disponibles? Indica la fecha de cada una, a qué "
             "expediente corresponde y si el cumplimiento fue total o parcial.")
        req = construir_requisitos(
            q, ResolutorDeIdentidades(["VCN-004-2022_2025_10_09"]).resolver(q))
        papeles = {r.get("papel") for r in req if r["tipo"] == "campos_registro"}
        assert "expediente principal del que deriva" in papeles

    def test_y_NO_exige_ademas_la_relacion_judicial(self):
        """
        "a qué expediente corresponde" contiene literalmente "qué expediente",
        así que sin un desempate H01 exigía además el expediente del TCC.
        COFECE lo señaló como defecto nuestro.
        """
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        q = ("¿Qué resoluciones de VCN dictadas en cumplimiento de amparo "
             "tienes disponibles? Indica a qué expediente corresponde cada una.")
        req = construir_requisitos(
            q, ResolutorDeIdentidades(["VCN-004-2022_2025_10_09"]).resolver(q))
        papeles = {r.get("papel") for r in req if r["tipo"] == "campos_registro"}
        assert "expediente relacionado" not in papeles

    def test_h14_sigue_exigiendo_la_relacion_judicial(self):
        """El desempate no puede romper el caso contrario."""
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos
        q = ("En el amparo 677/2024, ¿qué tribunal colegiado y qué expediente "
             "dieron origen?")
        req = construir_requisitos(
            q, ResolutorDeIdentidades(["677_2024_1SCJN"]).resolver(q))
        papeles = {r.get("papel") for r in req if r["tipo"] == "campos_registro"}
        assert "expediente relacionado" in papeles
        assert "tribunal relacionado" in papeles

    def test_el_requisito_se_cumple_con_el_dato_del_registro(self):
        from core.identidades import ResolutorDeIdentidades
        from core.requisitos import construir_requisitos, verificar
        from models.schemas import ExpedienteRecord
        q = ("¿Qué resoluciones dictadas en cumplimiento de amparo hay? "
             "Indica a qué expediente corresponde cada una.")
        req = construir_requisitos(
            q, ResolutorDeIdentidades(["VCN-004-2022_2025_10_09"]).resolver(q))
        d = ExpedienteRecord(**self.CRUDO).para_prompt()
        comp = next(c for c in verificar(req, [d])["componentes"]
                    if "principal" in (c.get("detalle") or ""))
        assert comp["cumple"]
