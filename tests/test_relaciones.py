"""
Historia procesal (COFECE, 25-sep §4.3; en vivo el 6-oct).

Los registros imitan la forma real de `/cases/agent-search`: mismos campos,
mismos formatos de fecha (`DD-MM-YYYY`), `parent` como objeto.
"""
import asyncio

JD2 = "Juzgado Segundo de Distrito en Materia Administrativa Especializado"
JD1 = "Juzgado Primero de Distrito en Materia Administrativa Especializado"
JD3 = "Juzgado Tercero de Distrito en Materia Administrativa Especializado"
TCC2 = "Segundo Tribunal Colegiado de Circuito en Materia Administrativa"


def _p(cl):
    return {"id": 1, "caseLink": cl}


ACERVO = [
    {"caseLink": "VCN-001-2017", "typeOfProcedure": "Concentración no notificada",
     "resolutionDate": "18-05-2017", "senseOfResolution": ["Sanciona"]},
    {"caseLink": "VCN-001-2017_2019_03_14", "parent": _p("VCN-001-2017"),
     "natureOfResolution": "En cumplimiento de amparo",
     "amparoComplianceResolutionDate": "14-03-2019", "scopeOfCompliance": "Parcial",
     "judgmentImplementation": "Insubsistente respecto del notario."},
    {"caseLink": "1258_2017_2JD", "parent": _p("VCN-001-2017"), "authority": JD2,
     "typeOfProcedure": "Amparo indirecto", "judicialCaseFile": "1258/2017",
     "judgmentDate": "28-02-2018", "senseOfAmparo": ["sobresee", "niega", "concede"]},
    {"caseLink": "1259-1260_2017_2JD", "parent": _p("VCN-001-2017"), "authority": JD2,
     "typeOfProcedure": "Amparo indirecto", "judicialCaseFile": "1259/2017",
     "accumulatedCaseFiles": ["1260/2017"], "judgmentDate": "27-03-2018",
     "senseOfAmparo": ["niega"]},
    {"caseLink": "93_2018_2TCC", "authority": TCC2, "typeOfProcedure": "Amparo en revisión",
     "judicialCaseFile": "93/2018", "originAmparoCaseFiles": ["1259/2017", "1260/2017"],
     "appealedJudgmentBody": JD2, "appealedJudgmentDate": "27-03-2018",
     "reviewResolutionDate": "27-09-2018", "senseOfReview": ["confirma"]},
    # Dos 275/2023 de juzgados distintos; el del Tercero sin número.
    {"caseLink": "275_2023_1JD", "parent": _p("VCN-002-2023"), "authority": JD1,
     "typeOfProcedure": "Amparo indirecto", "judicialCaseFile": "275/2023",
     "judgmentDate": "15-07-2024"},
    {"caseLink": "275_2023_3JD", "parent": _p("VCN-004-2023"), "authority": JD3,
     "typeOfProcedure": "Amparo indirecto", "judgmentDate": "17-10-2023"},
    {"caseLink": "556_2023_1TCC", "typeOfProcedure": "Amparo en revisión",
     "originAmparoCaseFiles": ["275/2023"], "appealedJudgmentBody": JD3,
     "appealedJudgmentDate": "17-10-2023"},
    # Dos sentencias del mismo 278/2023: la fecha decide.
    {"caseLink": "278_2023_1JD_2024_07_15", "parent": _p("VCN-002-2023"), "authority": JD1,
     "typeOfProcedure": "Amparo indirecto", "judicialCaseFile": "278/2023",
     "judgmentDate": "15-07-2024"},
    {"caseLink": "278_2023_1JD_2025_11_19", "parent": _p("VCN-002-2023"), "authority": JD1,
     "typeOfProcedure": "Amparo indirecto", "judicialCaseFile": "278/2023",
     "judgmentDate": "19-11-2025"},
    {"caseLink": "322_2025_1TCC", "typeOfProcedure": "Amparo en revisión",
     "originAmparoCaseFiles": ["278/2023"], "appealedJudgmentBody": JD1,
     "appealedJudgmentDate": "19-11-2025"},
    {"caseLink": "VCN-002-2023", "resolutionDate": "08-06-2023"},
    {"caseLink": "VCN-004-2023", "resolutionDate": "01-01-2023"},
    # SCJN con origen fuera del acervo.
    {"caseLink": "480_2018_2SCJN", "relatedTccCaseFile": "82/2018",
     "relatedCollegiateCourt": TCC2, "relatedTccDecisionDate": "10-05-2018"},
    # Juzgado sin expediente administrativo informado.
    {"caseLink": "43_2021_3JD", "authority": JD3, "typeOfProcedure": "Amparo indirecto",
     "judicialCaseFile": "43/2021", "judgmentDate": "30-01-2023"},
    # Parece cumplimiento por el nombre; no tiene `parent`.
    {"caseLink": "VCN-009-2020_2021_01_01", "typeOfProcedure": "Concentración no notificada"},
]


def _mapa():
    from core.relaciones import MapaDeRelaciones
    return MapaDeRelaciones.desde_registros(ACERVO)


def _enlace(m, cl):
    return m.hacia_arriba.get(cl)


class TestMapa:

    def test_cumplimiento_y_amparos_por_parent(self):
        m = _mapa()
        assert _enlace(m, "VCN-001-2017_2019_03_14").origen == "VCN-001-2017"
        assert _enlace(m, "1258_2017_2JD").origen == "VCN-001-2017"

    def test_no_se_deduce_parentesco_por_el_nombre(self):
        """COFECE: 'No deducir parentesco quitando el sufijo'."""
        assert _enlace(_mapa(), "VCN-009-2020_2021_01_01") is None

    def test_revision_por_numero_acumulado_organo_y_fecha(self):
        e = _enlace(_mapa(), "93_2018_2TCC")
        assert (e.origen, e.estado) == ("1259-1260_2017_2JD", "resuelto")

    def test_dos_sentencias_del_mismo_expediente_las_separa_la_fecha(self):
        e = _enlace(_mapa(), "322_2025_1TCC")
        assert e.origen == "278_2023_1JD_2025_11_19"

    def test_dos_275_las_separa_el_organo_y_se_declara_el_numero_faltante(self):
        """
        El 275/2023 del Tercero no trae número: coinciden órgano y fecha, se
        enlaza, y queda dicho que no se pudo confirmar con el número.
        """
        e = _enlace(_mapa(), "556_2023_1TCC")
        assert (e.origen, e.estado) == ("275_2023_3JD", "resuelto_sin_numero")
        assert "número" in e.motivo

    def test_origen_fuera_del_acervo_es_externo_no_inexistente(self):
        e = _enlace(_mapa(), "480_2018_2SCJN")
        assert e.estado == "externo" and e.origen is None
        assert "82/2018" in e.motivo

    def test_amparo_sin_origen_es_pendiente(self):
        e = _enlace(_mapa(), "43_2021_3JD")
        assert e.estado == "pendiente"

    def test_posteriores_en_cadena(self):
        exps = [p["expediente"] for p in _mapa().posteriores("VCN-001-2017")]
        assert set(exps) == {"VCN-001-2017_2019_03_14", "1258_2017_2JD",
                             "1259-1260_2017_2JD", "93_2018_2TCC"}

    def test_la_ficha_lleva_efecto_y_fecha_con_nombre(self):
        p = next(p for p in _mapa().posteriores("VCN-001-2017")
                 if p["expediente"] == "1258_2017_2JD")
        assert p["fecha_de_la_resolucion"] == "28-02-2018"
        assert p["senseOfAmparo"] == ["sobresee", "niega", "concede"]

    def test_cumplimiento_no_dice_que_amparo_cumple(self):
        """
        6-oct: el modelo escribió 'en cumplimiento del amparo 1258/2017'
        aunque ningún registro los enlaza.
        """
        h = _mapa().historia_de("VCN-001-2017")
        c = next(p for p in h["actuaciones_posteriores"]
                 if p["expediente"] == "VCN-001-2017_2019_03_14")
        assert "NO INFORMADA" in c["sentencia_que_cumple"]
        assert "1258_2017_2JD" in h["NO_ENLAZADO"]
        assert "VCN-001-2017_2019_03_14" in h["NO_ENLAZADO"]

    def test_el_limite_externo_se_dice(self):
        h = _mapa().historia_de("480_2018_2SCJN")
        assert "82/2018" in h["LIMITE"]

    def test_sin_relaciones_no_hay_historia(self):
        assert _mapa().historia_de("VCN-004-2023") is not None  # tiene 275_3JD
        from core.relaciones import MapaDeRelaciones
        assert MapaDeRelaciones.desde_registros(
            [{"caseLink": "X-1"}]).historia_de("X-1") is None


class TestResolutor:

    def test_fecha_del_principal_no_es_conflicto(self):
        """COFECE: pedir VCN-001-2017 del 18-may-2017 daba un conflicto falso."""
        from core.identidades import ResolutorDeIdentidades
        r = ResolutorDeIdentidades([x["caseLink"] for x in ACERVO])
        r.cargar_relaciones(_mapa())
        out = r.resolver("¿Qué resolvió VCN-001-2017 el 18 de mayo de 2017?")
        assert out[0]["candidatos"] == ["VCN-001-2017"]
        assert "conflicto" not in out[0]

    def test_los_actos_salen_del_parent(self):
        from core.identidades import ResolutorDeIdentidades
        r = ResolutorDeIdentidades([x["caseLink"] for x in ACERVO])
        r.cargar_relaciones(_mapa())
        assert "VCN-009-2020" not in r.actos_de
        assert r.actos_de["VCN-001-2017"] == [
            {"case_link": "VCN-001-2017_2019_03_14", "fecha": "2019-03-14"}]


class TestEnElAgente:

    def _agente_y_estado(self, mapa):
        from agent.agent import NormaPlusAgent
        from agent.turn_state import TurnState
        ag = NormaPlusAgent.__new__(NormaPlusAgent)
        st = TurnState()
        st.mapa_relaciones = mapa
        return ag, st

    def test_las_actuaciones_entran_citables(self):
        ag, st = self._agente_y_estado(_mapa())
        h = ag._historia_procesal([{"caseLink": "VCN-001-2017"}], st)
        posts = h["documentos"]["VCN-001-2017"]["actuaciones_posteriores"]
        assert all(p.get("ref", "").startswith("E") for p in posts)
        assert st.registry.case_link_of(posts[0]["ref"]) == posts[0]["expediente"]

    def test_sin_mapa_se_declara_no_consultada(self):
        """Un fallo al cargar el mapa no se lee como 'no hay impugnaciones'."""
        ag, st = self._agente_y_estado(None)
        h = ag._historia_procesal([{"caseLink": "VCN-001-2017"}], st)
        assert h["estado"] == "NO_CONSULTADA"

    def test_el_resumen_marca_el_documento_usado_sin_aviso(self):
        from core.relaciones import resumen_historia_procesal
        ag, st = self._agente_y_estado(_mapa())
        ag._historia_procesal([{"caseLink": "VCN-001-2017"}], st)
        r = resumen_historia_procesal(
            st.historia_procesal, "La multa fue X en VCN-001-2017.", st.registry)
        assert r["documentos_sin_aviso"] == ["VCN-001-2017"]
        ref = next(p["ref"] for p in st.historia_procesal[0]["posteriores"]
                   if p["expediente"] == "VCN-001-2017_2019_03_14")
        r = resumen_historia_procesal(
            st.historia_procesal, f"La multa fue X en VCN-001-2017, luego anulada [{ref}].",
            st.registry)
        assert r["documentos_sin_aviso"] == []

    def test_es_indicador_comparable(self):
        from core.tracing.compare import INDICADORES
        assert "historia_no_advertida" in dict(INDICADORES)

    def test_el_prompt_lo_pide(self):
        from prompts.system import AGENT_SYSTEM_PROMPT as P
        assert "HISTORIA PROCESAL" in P
        assert "No inventes la causa de una falla propia" in P


class TestOrganoFlexible:
    """
    7-oct: el juzgado se llama "…Especializada…" en su registro y el tribunal
    lo cita como "…Especializado…". Por esa letra, seis sentencias que sí
    están en el acervo quedaban como externas.
    """

    def test_misma_clave_aunque_cambie_la_redaccion(self):
        from core.relaciones import _organo_clave
        a = "Juzgado Segundo de Distrito en Materia Administrativa Especializada en Competencia"
        b = "Juzgado Segundo de Distrito en Materia Administrativa Especializado en Competencia"
        assert _organo_clave(a) == _organo_clave(b) == ("juzgado", "2")
        assert _organo_clave("Primer Tribunal Colegiado de Circuito") == ("tribunal", "1")

    def test_ordinal_distinto_no_empata(self):
        from core.relaciones import _organo_clave
        assert _organo_clave("Juzgado Primero de Distrito") != _organo_clave("Juzgado Tercero de Distrito")

    def test_la_revision_se_enlaza_pese_a_la_redaccion(self):
        from core.relaciones import MapaDeRelaciones
        m = MapaDeRelaciones.desde_registros([
            {"caseLink": "1587_2015_2JD", "typeOfProcedure": "Amparo indirecto",
             "authority": "Juzgado Segundo de Distrito en Materia Administrativa Especializada",
             "judgmentDate": "04-10-2016"},
            {"caseLink": "153_2016_2TCC", "originAmparoCaseFiles": ["1587/2015"],
             "appealedJudgmentBody": "Juzgado Segundo de Distrito en Materia Administrativa Especializado",
             "appealedJudgmentDate": "04-10-2016"},
        ])
        e = m.hacia_arriba["153_2016_2TCC"]
        assert (e.origen, e.estado) == ("1587_2015_2JD", "resuelto_sin_numero")


def test_las_actuaciones_llevan_su_tipo_de_fuente():
    """7-oct: sin tipo, el modelo etiquetaba [RESOLUCIÓN] las sentencias."""
    from agent.agent import NormaPlusAgent
    from agent.turn_state import TurnState
    ag = NormaPlusAgent.__new__(NormaPlusAgent)
    st = TurnState()
    st.mapa_relaciones = _mapa()
    h = ag._historia_procesal([{"caseLink": "VCN-001-2017"}], st)
    tipos = {p["expediente"]: p["tipo_fuente"]
             for p in h["documentos"]["VCN-001-2017"]["actuaciones_posteriores"]}
    assert tipos["1258_2017_2JD"] == "sentencia"
    assert tipos["93_2018_2TCC"] == "sentencia"
    assert tipos["VCN-001-2017_2019_03_14"] == "resolucion"
