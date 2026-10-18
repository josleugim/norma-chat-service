"""
Buscar expedientes por la persona que resolvió, votó o recurrió (18-oct-2026).

Imanol pidió "todas las sentencias en que participó el Magistrado Gómez
Fierro" y el chat contestó que el nombre del juez no era un dato disponible.
Sí lo es (`decisionOfficials`), pero la búsqueda de la API no lo mira.
"""
import asyncio

# Formas reales del 18-oct.
UNIVERSO = [
    {"caseLink": "257_2019_2JD", "typeOfProcedure": "Amparo indirecto",
     "decisionOfficials": [
         {"rol": "juez_distrito", "nombre": "Juan Pablo Gómez Fierro",
          "rol_literal": "Juez Segundo de Distrito"},
         {"rol": "secretario_juzgado", "nombre": "Arely Muñoz López",
          "rol_literal": "secretaria de juzgado"}]},
    {"caseLink": "353_2024_1TCC", "typeOfProcedure": "Amparo en revisión",
     "decisionOfficials": [
         {"rol": "magistrado_presidente", "nombre": "Irma Leticia Flores Díaz",
          "rol_literal": "Presidencia"}],
     "dissentingAndConcurringOpinions": [
         {"nombre": "Irma Leticia Flores Díaz", "posicion": "en_contra"}],
     "principalAppellants": ["Comisión Federal de Competencia Económica"]},
    {"caseLink": "VCN-003-2025", "typeOfProcedure": "Concentración no notificada",
     "dissentingOpinions": ["Oscar Alejandro Gómez Romero (concurrente)",
                            "Ana María Reséndiz Mora (en contra)"]},
    {"caseLink": "CNT-084-2016", "typeOfProcedure": "Concentración",
     "dissentingOpinions": [
         "Brenda Gisela Hernández Ramírez (en contra); Alejandra Palacios Prieto (en contra)"]},
    {"caseLink": "CNT-001-2020", "typeOfProcedure": "Concentración",
     "economicAgents": ["Gómez Fierro Hermanos S.A."]},
]


class TestCoincidencias:

    def test_el_titulo_no_impide_encontrar_al_juez(self):
        from core.personas import filtrar_por_persona
        r = filtrar_por_persona(UNIVERSO, "Magistrado Gómez Fierro")
        assert [x["caseLink"] for x in r] == ["257_2019_2JD"]
        c = r[0]["coincidencia_persona"][0]
        assert c["nombre"] == "Juan Pablo Gómez Fierro"
        assert "juez distrito" in c["papel"]

    def test_sin_acentos_ni_mayusculas(self):
        from core.personas import filtrar_por_persona
        assert len(filtrar_por_persona(UNIVERSO, "gomez fierro")) == 1

    def test_un_apellido_suelto_trae_a_todos_los_que_lo_llevan(self):
        """La ambigüedad queda a la vista con los nombres completos."""
        from core.personas import filtrar_por_persona
        r = filtrar_por_persona(UNIVERSO, "Gómez")
        nombres = {c["nombre"] for x in r for c in x["coincidencia_persona"]}
        assert nombres == {"Juan Pablo Gómez Fierro", "Oscar Alejandro Gómez Romero"}

    def test_palabra_completa_no_subcadena(self):
        from core.personas import filtrar_por_persona
        assert filtrar_por_persona(UNIVERSO, "Góm") == []

    def test_los_agentes_economicos_no_son_quienes_resuelven(self):
        from core.personas import filtrar_por_persona
        assert "CNT-001-2020" not in [x["caseLink"] for x in
                                      filtrar_por_persona(UNIVERSO, "Gómez Fierro")]

    def test_votos_con_su_sentido(self):
        from core.personas import filtrar_por_persona
        r = filtrar_por_persona(UNIVERSO, "Reséndiz")
        assert r[0]["coincidencia_persona"] == [
            {"nombre": "Ana María Reséndiz Mora", "papel": "voto en contra"}]

    def test_varios_votos_en_una_cadena_se_separan(self):
        from core.personas import filtrar_por_persona
        r = filtrar_por_persona(UNIVERSO, "Alejandra Palacios")
        assert r[0]["coincidencia_persona"] == [
            {"nombre": "Alejandra Palacios Prieto", "papel": "voto en contra"}]

    def test_magistrada_con_cargo_y_voto(self):
        from core.personas import filtrar_por_persona
        papeles = [c["papel"] for c in
                   filtrar_por_persona(UNIVERSO, "Flores Díaz")[0]["coincidencia_persona"]]
        assert papeles == ["magistrado presidente (Presidencia)", "voto en contra"]

    def test_recurrentes(self):
        from core.personas import filtrar_por_persona
        r = filtrar_por_persona(UNIVERSO, "Comisión Federal de Competencia Económica")
        assert r[0]["coincidencia_persona"][0]["papel"] == "recurrente principal"


class _Estadistica:
    def __init__(self, registros):
        from models.schemas import ExpedienteRecord
        self.registros = [ExpedienteRecord(**r) for r in registros]
        self.last_truncado = False
        self.llamadas = []

    async def fetch_universe(self, text_search=None, filters=None,
                             max_results=None, collector=None):
        self.llamadas.append(("universo", text_search, filters))
        return list(self.registros), len(self.registros), True

    async def fetch_by_prefix(self, prefijo, filters=None, max_results=None, collector=None):
        self.llamadas.append(("prefijo", prefijo, filters))
        return [r for r in self.registros if r.caseLink.startswith(prefijo + "-")]

    async def search(self, **kw):
        raise AssertionError("con persona no se usa la búsqueda de una página")


def _agente():
    from agent.agent import NormaPlusAgent
    ag = NormaPlusAgent.__new__(NormaPlusAgent)
    ag.estadistica = _Estadistica(UNIVERSO)
    return ag


def _buscar(args):
    from agent.turn_state import TurnState
    st = TurnState()
    ag = _agente()
    return asyncio.run(ag._exec_buscar_expedientes(args, None, st)), st, ag


class TestEnElAgente:

    def test_recorre_el_universo_y_devuelve_la_ficha_con_el_papel(self):
        r, st, ag = _buscar({"persona": "Gómez Fierro"})
        assert [x["caseLink"] for x in r] == ["257_2019_2JD"]
        assert r[0]["decisionOfficials"]
        assert r[0]["coincidencia_persona"][0]["nombre"] == "Juan Pablo Gómez Fierro"
        assert ag.estadistica.llamadas[0][0] == "universo"
        assert st.universo_completo is True
        filtro = st.filtros_aplicados[-1]
        assert filtro["filtro"] == "persona" and filtro["universo_despues"] == 1

    def test_se_combina_con_el_prefijo(self):
        r, _, ag = _buscar({"persona": "Gómez", "prefijo_expediente": "VCN"})
        assert [x["caseLink"] for x in r] == ["VCN-003-2025"]
        assert ag.estadistica.llamadas[0][0] == "prefijo"

    def test_sin_coincidencias_queda_marcado_para_el_aviso(self):
        r, st, _ = _buscar({"persona": "Fulano Inexistente"})
        assert r == []
        assert st.persona_sin_coincidencias == "Fulano Inexistente"
        assert st.universo_completo is True

    def test_si_hay_mas_que_el_limite_no_se_declara_completo(self):
        r, st, _ = _buscar({"persona": "Gómez", "limit": 1})
        assert len(r) == 1
        assert st.universo_completo is False
        assert st.universo_tamano == 2

    def test_contar_por_persona(self):
        from agent.turn_state import TurnState
        r = asyncio.run(_agente()._exec_contar_expedientes(
            {"persona": "Gómez"}, None, TurnState()))
        assert r["total"] == 2


def test_la_herramienta_y_el_conector_exponen_el_parametro():
    import inspect
    from agent.tools import TOOLS as AGENT_TOOLS
    por_nombre = {t["name"]: t for t in AGENT_TOOLS}
    assert "persona" in por_nombre["buscar_expedientes"]["parameters"]["properties"]
    assert "persona" in por_nombre["contar_expedientes"]["parameters"]["properties"]
    from mcp_server import servidor
    fuente = inspect.getsource(servidor)
    assert '"persona": persona' in fuente
