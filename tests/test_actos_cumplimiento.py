"""
Un asunto se cuenta una vez en las agregaciones (5-oct-2026).

Hasta ese día, los cuatro actos de cumplimiento de amparo VCN entraban al
promedio de plazos junto a su principal: el mismo asunto dos veces, la segunda
con el litigio incluido. q01 decía 102.08 días hábiles; sobre los 32 asuntos
son 43.66.
"""
import asyncio


def _rec(case_link, inicio=None, fin=None, parent=None, naturaleza=None, multas=None):
    from models.schemas import ExpedienteRecord
    return ExpedienteRecord(**{
        "caseLink": case_link, "authority": "COFECE",
        "typeOfProcedure": "Concentración no notificada",
        "startAgreementDate": inicio, "resolutionDate": fin,
        "natureOfResolution": naturaleza, "agentFines": multas,
        "parent": {"id": 1, "caseLink": parent} if parent else None,
    })


# Forma real al 5-oct: el acto trae `parent` y su naturaleza, sin fechas de
# inicio ni resolución; la multa sí.
UNIVERSO = [
    _rec("VCN-001-2017", "10-02-2017", "15-05-2017", multas={"A": "$100000.00"}),
    _rec("VCN-002-2017", "08-03-2017", "26-04-2017", multas={"B": "$200000.00"}),
    _rec("VCN-001-2017_2019_03_14", parent="VCN-001-2017",
         naturaleza="En cumplimiento de amparo", multas={"A": "$900000.00"}),
]


class _Estadistica:
    """Mismo contrato que `fetch_universe`: (registros, total, completo)."""
    def __init__(self, registros): self.registros = registros

    async def fetch_universe(self, text_search=None, filters=None,
                             max_results=None, collector=None):
        return list(self.registros), len(self.registros), True


def _agente(registros=UNIVERSO):
    from agent.agent import NormaPlusAgent
    from temporal.analyzer import TemporalAnalyzer
    from temporal.holidays import HolidayCalendar
    ag = NormaPlusAgent.__new__(NormaPlusAgent)
    ag.temporal = TemporalAnalyzer(HolidayCalendar("data/dias_inhabiles.xlsx"))
    ag.estadistica = _Estadistica(registros)
    return ag


def _agregar(args, registros=UNIVERSO):
    from agent.turn_state import TurnState
    st = TurnState()
    r = asyncio.run(_agente(registros)._exec_agregar_expedientes(args, None, st))
    return r, st


PLAZO = {"operacion": "promedio", "metrica": "dias_habiles", "prefijo_expediente": "VCN",
         "campo_inicio": "startAgreementDate", "campo_fin": "resolutionDate"}


class TestActosDeCumplimiento:

    def test_se_reconoce_por_la_relacion_o_la_naturaleza(self):
        from core.aggregation import es_acto_de_cumplimiento
        assert es_acto_de_cumplimiento({"expediente_principal": "VCN-001-2017"})
        assert es_acto_de_cumplimiento({"natureOfResolution": "En cumplimiento de amparo"})
        assert not es_acto_de_cumplimiento({"caseLink": "VCN-001-2017"})

    def test_no_se_infiere_de_la_forma_del_identificador(self):
        """Un sufijo de fecha parece cumplimiento; sin relación ni naturaleza, no lo es."""
        from core.aggregation import es_acto_de_cumplimiento
        assert not es_acto_de_cumplimiento({"caseLink": "VCN-004-2022_2025_10_09"})

    def test_el_plazo_promedio_cuenta_cada_asunto_una_vez(self):
        r, _ = _agregar(PLAZO)
        assert r["procesados"] == 2
        assert r["ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS"]["count"] == 1
        assert r["ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS"]["actos"] == [
            {"expediente": "VCN-001-2017_2019_03_14", "asunto_principal": "VCN-001-2017"}]

    def test_no_se_describen_como_expedientes_sin_datos(self):
        """
        La respuesta del 5-oct decía "4 quedaron fuera por falta de datos".
        No les faltan datos: son actos de asuntos que ya cuentan.
        """
        r, _ = _agregar(PLAZO)
        texto = r["COMO_DEBES_DESCRIBIR_LA_COBERTURA"]
        assert "falta de datos" not in texto
        assert "No se incluyen 1 resoluciones en cumplimiento de amparo" in texto

    def test_tambien_en_multas(self):
        """
        Tres de los cuatro actos VCN traen multa: una suma los contaba además
        de su principal.
        """
        r, _ = _agregar({"operacion": "suma", "metrica": "multa", "prefijo_expediente": "VCN"})
        assert r["procesados"] == 2
        assert r["ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS"]["count"] == 1

    def test_la_exclusion_queda_en_la_traza(self):
        _, st = _agregar(PLAZO)
        linaje = [f for f in st.filtros_aplicados if f["filtro"] == "actos_de_cumplimiento"]
        assert linaje and linaje[0]["descartados"] == 1
        assert {"case_link": "VCN-001-2017_2019_03_14", "status": "excluded",
                "exclusion_reason": "acto_de_cumplimiento_de_amparo",
                "value_used": False} in st.computation_audit

    def test_se_pueden_pedir_explicitamente(self):
        """Si la pregunta es sobre las resoluciones en cumplimiento, entran."""
        r, _ = _agregar({**PLAZO, "incluir_actos_de_cumplimiento": True})
        assert r["procesados"] == 3
        assert "ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS" not in r

    def test_sin_actos_no_hay_aviso(self):
        r, _ = _agregar(PLAZO, registros=UNIVERSO[:2])
        assert "ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS" not in r
        assert "cumplimiento" not in r["COMO_DEBES_DESCRIBIR_LA_COBERTURA"]


# ── Hasta la resolución final, sólo si se pide (Imanol, 6-oct) ─────────────

def _rec_acto(case_link, parent, fecha_cumplimiento):
    from models.schemas import ExpedienteRecord
    return ExpedienteRecord(**{
        "caseLink": case_link, "authority": "COFECE",
        "typeOfProcedure": "Concentración no notificada",
        "natureOfResolution": "En cumplimiento de amparo",
        "amparoComplianceResolutionDate": fecha_cumplimiento,
        "parent": {"id": 1, "caseLink": parent},
    })


UNIVERSO_FINAL = [
    _rec("VCN-001-2017", "10-02-2017", "15-05-2017"),
    _rec("VCN-002-2017", "08-03-2017", "26-04-2017"),
    _rec_acto("VCN-001-2017_2018_01_01", "VCN-001-2017", "01-01-2018"),
    _rec_acto("VCN-001-2017_2019_03_14", "VCN-001-2017", "14-03-2019"),
]


class TestHastaResolucionFinal:

    def test_por_defecto_hasta_la_resolucion_inicial(self):
        """La definición de Imanol: sin cumplimientos, salvo que se pida."""
        r, _ = _agregar(PLAZO, registros=UNIVERSO_FINAL)
        assert "PLAZO_HASTA_RESOLUCION_FINAL" not in r
        assert r["ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS"]["count"] == 2

    def test_si_se_pide_corre_hasta_el_cumplimiento_mas_reciente(self):
        r, st = _agregar({**PLAZO, "hasta_resolucion_final": True},
                         registros=UNIVERSO_FINAL)
        assert r["procesados"] == 2  # el asunto se sigue contando una vez
        cambios = r["PLAZO_HASTA_RESOLUCION_FINAL"]["asuntos"]
        assert cambios == [{"asunto": "VCN-001-2017",
                            "cumplimiento": "VCN-001-2017_2019_03_14",
                            "fecha_inicial": "15-05-2017",
                            "fecha_final_usada": "14-03-2019"}]
        audit = {a["case_link"]: a for a in st.computation_audit if a.get("end_date")}
        # El cálculo y su audit usan la fecha del cumplimiento; el otro asunto,
        # sin cumplimiento, conserva la suya.
        assert audit["VCN-001-2017"]["end_date"] == "2019-03-14"
        assert audit["VCN-002-2017"]["end_date"] == "2017-04-26"

    def test_lo_dice_en_la_descripcion_y_no_los_llama_excluidos(self):
        r, _ = _agregar({**PLAZO, "hasta_resolucion_final": True},
                        registros=UNIVERSO_FINAL)
        texto = r["COMO_DEBES_DESCRIBIR_LA_COBERTURA"]
        assert "hasta su resolución en cumplimiento" in texto
        assert "No se incluyen" not in texto
        assert "ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS" not in r

    def test_no_aplica_a_multas(self):
        r, _ = _agregar({"operacion": "suma", "metrica": "multa", "prefijo_expediente": "VCN",
                         "hasta_resolucion_final": True}, registros=UNIVERSO_FINAL)
        assert "PLAZO_HASTA_RESOLUCION_FINAL" not in r
