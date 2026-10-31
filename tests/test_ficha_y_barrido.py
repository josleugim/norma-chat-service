"""
Ficha del expediente en cada criterio y barrido de criterios (31-oct).

Origen: dos conversaciones de Imanol del 30-oct. Ante una cláusula de no
competencia de cinco años, la respuesta dijo que cinco años era "el tope
habitual" porque no podía ver que esos precedentes son de 1994-2007; el agente
le decía que los criterios venían sin fecha, autoridad ni sentido. Y en la de
sustitución por la oferta (28-oct), el top-15 traía cero criterios del tema.
"""
import asyncio

from agent.turn_state import TurnState
from core import barrido
from core.fichas import IndiceDeFichas, faltantes, ficha_de
from models.schemas import CriterioResult, ExpedienteRecord


# ── Ficha ──────────────────────────────────────────────────────────────

class TestFicha:
    def test_trae_fecha_iso_autoridad_tipo_y_sentido(self):
        f = ficha_de({"caseLink": "CNT-030-1994", "resolutionDate": "12-05-1994",
                      "authority": "CFC", "typeOfProcedure": "Concentración",
                      "senseOfResolution": ["Autoriza"]})
        assert f == {"fecha_resolucion": "1994-05-12", "autoridad": "CFC",
                     "tipo_procedimiento": "Concentración", "sentido_resolucion": ["Autoriza"]}
        assert faltantes(f) == []

    def test_un_acto_de_cumplimiento_usa_su_propia_fecha(self):
        f = ficha_de({"amparoComplianceResolutionDate": "09-10-2025", "authority": "COFECE",
                      "natureOfResolution": "En cumplimiento de amparo"})
        assert f["fecha_resolucion"] == "2025-10-09"
        assert f["naturaleza"] == "En cumplimiento de amparo"

    def test_no_rellena_lo_que_no_viene(self):
        f = ficha_de({"caseLink": "CNT-001-2000", "authority": "CFC",
                      "resolutionDate": "fecha rara", "senseOfResolution": []})
        assert "fecha_resolucion" not in f and "sentido_resolucion" not in f
        assert faltantes(f) == ["sentido_resolucion", "fecha_resolucion"]
        assert faltantes(None) == ["autoridad", "sentido_resolucion", "fecha_resolucion"]

    def test_acepta_registros_del_modelo(self):
        r = ExpedienteRecord(caseLink="CNT-002-2020", resolutionDate="18-06-2020",
                             authority="COFECE", senseOfResolution=["Autoriza"])
        idx = IndiceDeFichas.desde_registros([r])
        assert idx.get("CNT-002-2020")["fecha_resolucion"] == "2020-06-18"
        assert idx.get("CNT-999-2020") is None and idx.get(None) is None


# ── Barrido ────────────────────────────────────────────────────────────

def _c(cid, cl, temas, texto="texto"):
    return CriterioResult(id=str(cid), text=texto, score=0.5,
                          metadata={"id_expediente": cl, "caseLink": cl, "titleNames": temas,
                                    "title": temas[0] if temas else ""})


FICHAS = IndiceDeFichas({
    "CNT-030-1994": {"fecha_resolucion": "1994-05-12", "autoridad": "CFC"},
    "CNT-002-2020": {"fecha_resolucion": "2020-06-18", "autoridad": "COFECE"},
    "CNT-055-2012": {"fecha_resolucion": "2012-03-01", "autoridad": "CFC"},
})


class TestBarrido:
    def test_un_pasaje_por_expediente_y_tema(self):
        cands = [_c(1, "CNT-002-2020", ["Dimensión temporal"]),
                 _c(2, "CNT-002-2020", ["Dimensión temporal"]),   # duplicado
                 _c(3, "CNT-002-2020", ["Finalidad"]),
                 _c(4, "CNT-030-1994", ["Dimensión temporal"])]
        pasajes, info = barrido.condensar(cands, fichas=FICHAS)
        assert [p.id for p in pasajes] == ["1", "3", "4"]
        assert info["agrupados_por_expediente_y_tema"] == 1
        assert info["revisados"] == 4 and info["expedientes_en_el_barrido"] == 2

    def test_el_indice_cuenta_temas_expedientes_y_anios(self):
        cands = [_c(1, "CNT-002-2020", ["Dimensión temporal"]),
                 _c(2, "CNT-030-1994", ["Dimensión temporal"]),
                 _c(3, "CNT-055-2012", ["Dimensión geográfica"])]
        _, info = barrido.condensar(cands, fichas=FICHAS)
        fila = info["indice_de_temas"][0]
        assert fila == {"tema": "Dimensión temporal", "criterios": 2, "expedientes": 2,
                        "anios": "1994-2020"}

    def test_por_tema_trae_todos_en_orden_cronologico(self):
        cands = [_c(1, "CNT-002-2020", ["Dimensión temporal"]),
                 _c(2, "CNT-SIN-FICHA", ["Dimensión temporal"]),
                 _c(3, "CNT-055-2012", ["Dimensión temporal"]),
                 _c(4, "CNT-030-1994", ["Dimensión temporal"]),
                 _c(5, "CNT-030-1994", ["Finalidad"])]
        pasajes, info = barrido.condensar(cands, tema="dimension TEMPORAL", fichas=FICHAS)
        # Sin acentos ni mayúsculas; sin fecha va al final.
        assert [p.id for p in pasajes] == ["4", "3", "1", "2"]
        assert info["criterios_del_tema"] == 4 and info["expedientes_del_tema"] == 4
        assert info["orden"] == "cronologico_por_fecha_de_resolucion"

    def test_un_tema_ausente_no_se_lee_como_inexistente(self):
        _, info = barrido.condensar([_c(1, "CNT-002-2020", ["Finalidad"])], tema="Ferry")
        assert info["criterios_del_tema"] == 0
        assert "no prueba que el tema no exista" in info["nota"]

    def test_siempre_advierte_que_no_es_la_lista_completa(self):
        for tema in (None, "Finalidad"):
            _, info = barrido.condensar([_c(1, "CNT-002-2020", ["Finalidad"])], tema=tema)
            assert "no afirmes que son todos" in info["advertencia"]

    def test_respeta_los_topes_de_contexto(self):
        cands = [_c(i, f"CNT-{i:03d}-2020", [f"Tema {i % 30}"]) for i in range(300)]
        pasajes, info = barrido.condensar(cands)
        assert len(pasajes) == barrido.MAX_PASAJES
        assert len(info["indice_de_temas"]) == barrido.MAX_TEMAS_INDICE
        assert info["temas_encontrados"] == 30


# ── En el agente ───────────────────────────────────────────────────────

UNIVERSO = [
    ExpedienteRecord(caseLink="CNT-030-1994", resolutionDate="12-05-1994", authority="CFC",
                     typeOfProcedure="Concentración", senseOfResolution=["Autoriza"]),
    ExpedienteRecord(caseLink="CNT-002-2020", resolutionDate="18-06-2020", authority="COFECE",
                     typeOfProcedure="Concentración", senseOfResolution=["Autoriza"]),
]


def _agente(candidatos, universo=UNIVERSO):
    from agent.agent import NormaPlusAgent

    class _Criterios:
        def __init__(self): self.top_ks = []

        async def search(self, query, top_k=15, filters=None, collector=None, max_distance=0.7):
            self.top_ks.append(top_k)
            return list(candidatos)

    class _Estadistica:
        universo = None

        async def fetch_universe(self, **kw):
            if universo is None:
                raise RuntimeError("API caída")
            return list(universo), len(universo), True

    ag = NormaPlusAgent.__new__(NormaPlusAgent)
    ag.criterios = _Criterios()
    ag.estadistica = _Estadistica()

    async def _sin_ampliar(query, results, collector, state):
        return results
    ag._ampliar_precedente = _sin_ampliar
    return ag


CANDS = [_c(1, "CNT-002-2020", ["Dimensión temporal"]),
         _c(2, "CNT-030-1994", ["Dimensión temporal"])]


class TestEnElAgente:
    def test_cada_criterio_trae_la_ficha_de_su_expediente(self):
        ag, st = _agente(CANDS), TurnState(query="no competencia")
        docs = asyncio.run(ag._exec_buscar_criterios({"query": "q"}, None, st))
        por_cl = {d["metadata"]["id_expediente"]: d for d in docs}
        assert por_cl["CNT-030-1994"]["ficha_fuente"]["fecha_resolucion"] == "1994-05-12"
        assert por_cl["CNT-030-1994"]["ficha_fuente"]["autoridad"] == "CFC"
        # Ya no se declara ausente lo que sí está: era el defecto que subrayó Imanol.
        assert por_cl["CNT-030-1994"]["campos_no_disponibles"] == []
        assert st.fichas_criterios == {"con_ficha": 2, "sin_ficha": 0, "indice_cargado": True}

    def test_si_el_universo_no_carga_se_declara_ausente_como_antes(self):
        ag, st = _agente(CANDS, universo=None), TurnState(query="q")
        docs = asyncio.run(ag._exec_buscar_criterios({"query": "q"}, None, st))
        assert all("ficha_fuente" not in d for d in docs)
        assert docs[0]["campos_no_disponibles"] == [
            "autoridad", "sentido_resolucion", "fecha_resolucion"]
        assert st.fichas_criterios["indice_cargado"] is False

    def test_sin_barrido_se_piden_los_15_de_siempre(self):
        ag = _agente(CANDS)
        asyncio.run(ag._exec_buscar_criterios({"query": "q"}, None, TurnState(query="q")))
        assert ag.criterios.top_ks == [15]

    def test_el_barrido_pide_300_y_el_resultado_lleva_su_advertencia(self):
        ag, st = _agente(CANDS), TurnState(query="q")
        docs = asyncio.run(ag._exec_buscar_criterios({"query": "q", "barrido": True}, None, st))
        assert ag.criterios.top_ks == [barrido.PROFUNDIDAD]
        payload = ag._serialize_tool_result("buscar_criterios", docs, st)
        assert payload["BARRIDO"]["modo"] == "barrido"
        assert "no afirmes que son todos" in payload["BARRIDO"]["advertencia"]
        assert st.barrido_pendiente is None  # se pega una sola vez
        assert st.barridos[0]["query"] == "q" and "advertencia" not in st.barridos[0]

    def test_por_tema_el_orden_es_cronologico_en_lo_que_ve_el_modelo(self):
        ag, st = _agente(CANDS), TurnState(query="q")
        docs = asyncio.run(ag._exec_buscar_criterios(
            {"query": "q", "tema": "Dimensión temporal"}, None, st))
        assert [d["ficha_fuente"]["fecha_resolucion"][:4] for d in docs] == ["1994", "2020"]

    def test_con_expedientes_nombrados_el_barrido_no_aplica(self):
        ag, st = _agente(CANDS), TurnState(query="q")

        async def _por_doc(query, exps, top_k, collector, state):
            return list(CANDS)
        ag._buscar_criterios_por_documento = _por_doc
        asyncio.run(ag._exec_buscar_criterios(
            {"query": "q", "barrido": True, "en_expedientes": ["CNT-002-2020"]}, None, st))
        assert st.barridos == [] and ag.criterios.top_ks == []
