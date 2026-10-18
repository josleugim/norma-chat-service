"""Prototipo de lectura de la resolución completa: cliente y verificación de citas."""
import asyncio

import pytest

from core.lector_resolucion import leer, retirar_sin_respaldo, verificar_cita
from models.schemas import LLMToolResponse
from retrieval.estadistica_client import BusquedaFallidaError
from retrieval.resolucion_client import (
    FIRMA, LIMITE, TESTADO, armar_resolucion, limpiar_fragmento)


def _fila(i, pagina, texto, case="VCN-005-2024"):
    return {"id": i, "caseLink": case, "page": pagina, "text": texto, "distance": 0.5}


class TestCliente:
    def test_ordena_por_pagina_e_id_aunque_lleguen_por_distancia(self):
        filas = [_fila(30, 2, "tercero"), _fila(10, 1, "primero"), _fila(20, 1, "segundo")]
        r = armar_resolucion("VCN-005-2024", filas)
        assert r.paginas == {1: "primero\nsegundo", 2: "tercero"}

    def test_testado_queda_como_marca_y_se_cuenta(self):
        r = armar_resolucion("X", [_fila(1, 1, 'fue <p class="crossed-out"></p> y', "X")])
        assert TESTADO in r.paginas[1]
        assert r.testados == 1

    def test_limpia_tablas_saltos_y_firmas(self):
        t = limpiar_fragmento("a<br/>b<table><tr><td>x</td><td>y</td></tr></table> "
                              + "A" * 80)
        assert "<" not in t and "a\nb" in t and "x | y" in t and FIRMA in t

    def test_vacia_no_afirma_inexistencia(self):
        r = armar_resolucion("CNT-999-2020", [])
        assert not r.disponible
        assert "no distingue" in r.motivo

    def test_tope_alcanzado_se_declara(self):
        filas = [_fila(i, 1, "t", "X") for i in range(LIMITE)]
        assert armar_resolucion("X", filas).posiblemente_incompleta

    def test_fragmento_de_otro_expediente_revienta(self):
        with pytest.raises(BusquedaFallidaError):
            armar_resolucion("X", [_fila(1, 1, "t", "X"), _fila(2, 1, "t", "Y")])


@pytest.fixture
def resolucion():
    return armar_resolucion("VCN-005-2024", [
        _fila(1, 13, "Los propósitos para realizar los incrementos de capital son distintos."),
        _fila(2, 14, "El AUMENTO DE CAPITAL 2020 no era una aportación vinculada a la"),
        _fila(3, 15, "INVERSIÓN 2018, sino una operación independiente."),
        _fila(4, 21, "**VOTO CONCURRENTE*<br/>Comisionado: José Eduardo Mendoza Contreras"),
    ])


class TestVerificacion:
    def test_cita_textual_en_su_pagina(self, resolucion):
        v = verificar_cita(resolucion, 13, "los incrementos de capital son distintos")
        assert v["verificada"] and v["pagina_encontrada"] == 13

    def test_tolera_mayusculas_espacios_y_marcado(self, resolucion):
        assert verificar_cita(resolucion, 21, "VOTO CONCURRENTE Comisionado: José Eduardo Mendoza")["verificada"]

    def test_pasaje_que_cruza_pagina(self, resolucion):
        v = verificar_cita(resolucion, 14, "vinculada a la INVERSIÓN 2018, sino una operación")
        assert v["verificada"]

    def test_pagina_equivocada_se_corrige_no_se_descarta(self, resolucion):
        v = verificar_cita(resolucion, 3, "los incrementos de capital son distintos")
        assert v["verificada"] and v["pagina_encontrada"] == 13

    def test_encabezado_de_pagina_intercalado_y_coma(self):
        r = armar_resolucion("X", [
            _fila(1, 183, "deje insubsistente la resolución por la que se impuso una", "X"),
            _fila(2, 184, "FORMA A-55 JUICIO DE AMPARO multa de $ <p class=\"crossed-out\"></p> , por intervenir", "X")])
        v = verificar_cita(r, 183, "deje insubsistente la resolución por la que se impuso una "
                                   "multa de $ [TESTADO], por intervenir")
        assert v["verificada"]

    def test_puntos_suspensivos_entre_tramos_textuales(self, resolucion):
        v = verificar_cita(resolucion, 14, "El AUMENTO DE CAPITAL 2020 no era ... "
                                           "INVERSIÓN 2018, sino una operación independiente")
        assert v["verificada"]

    def test_negacion_insertada_no_pasa(self, resolucion):
        v = verificar_cita(resolucion, 13, "los propósitos para realizar los incrementos "
                                           "de capital no son distintos")
        assert not v["verificada"]

    def test_cifra_cambiada_no_pasa(self):
        r = armar_resolucion("X", [_fila(1, 9, "se impone una multa de $40,838,762.32 MXN al fiduciario", "X")])
        assert verificar_cita(r, 9, "se impone una multa de $40,838,762.32 MXN")["verificada"]
        assert not verificar_cita(r, 9, "se impone una multa de $40,838,762.33 MXN")["verificada"]

    def test_paráfrasis_no_pasa(self, resolucion):
        v = verificar_cita(resolucion, 13, "los aumentos de capital tenían fines diferentes")
        assert not v["verificada"]


class _Adaptador:
    def __init__(self, contenido):
        self.contenido = contenido
        self.mensajes = None

    async def completion_with_tools(self, messages, **kw):
        self.mensajes = messages
        return LLMToolResponse(content=self.contenido, input_tokens=100_000, output_tokens=500)


class TestLeer:
    def test_cita_inventada_se_retira_del_texto(self, resolucion):
        salida = ("RESPUESTA:\nSon operaciones distintas [1]. Hubo multa [2].\n"
                  "CITAS:\n[1] p. 13 | «los incrementos de capital son distintos»\n"
                  "[2] p. 14 | se impuso una multa de diez millones\n"
                  "NO_CONSTA: no")
        lec = asyncio.run(leer(_Adaptador(salida), "gpt-4.1", resolucion, "¿?"))
        assert lec.citas_verificadas == 1
        assert lec.respuesta == "Son operaciones distintas [1]."
        assert lec.retiradas == ["Hubo multa [2]."]
        assert lec.costo_usd == pytest.approx(0.204)

    def test_el_documento_entra_completo_con_paginas(self, resolucion):
        ad = _Adaptador("RESPUESTA:\nx\nCITAS:\nNO_CONSTA: no")
        asyncio.run(leer(ad, "gpt-4.1", resolucion, "¿?"))
        usuario = ad.mensajes[-1]["content"]
        assert "<<<PÁGINA 13>>>" in usuario and "<<<PÁGINA 21>>>" in usuario

    def test_no_disponible_no_llama_al_modelo(self):
        ad = _Adaptador("nunca")
        lec = asyncio.run(leer(ad, "gpt-4.1", armar_resolucion("X", []), "¿?"))
        assert ad.mensajes is None and lec.no_consta and "no significa" in lec.respuesta


class TestFormato:
    def test_no_consta_y_salida_fuera_de_formato(self, resolucion):
        lec = asyncio.run(leer(_Adaptador("RESPUESTA:\nNo consta.\nCITAS:\nNO_CONSTA: sí"),
                               "gpt-4.1", resolucion, "¿?"))
        assert lec.no_consta and lec.error is None
        lec = asyncio.run(leer(_Adaptador("texto libre sin formato"), "gpt-4.1", resolucion, "¿?"))
        assert lec.error and lec.respuesta == "texto libre sin formato" and lec.citas == []


class TestRetiro:
    def test_marca_sin_entrada_en_citas_se_retira(self):
        texto, ret = retirar_sin_respaldo("A [1]. B [3].", {1})
        assert texto == "A [1]." and ret == ["B [3]."]

    def test_oracion_con_una_cita_buena_se_queda_sin_la_mala(self):
        texto, ret = retirar_sin_respaldo("A y B [1][2].", {1})
        assert texto == "A y B [1]." and ret == []

    def test_no_consta_sin_texto_tiene_respuesta(self, resolucion):
        lec = asyncio.run(leer(_Adaptador("RESPUESTA:\nCITAS:\nNO_CONSTA: sí"),
                               "gpt-4.1", resolucion, "¿?"))
        assert "no contiene" in lec.respuesta

    def test_tramos_textuales_separados_por_puntos_suspensivos(self):
        r = armar_resolucion("X", [
            _fila(1, 128, "se impone a CI NEXXUS una multa como sanción de $20,419,381.16 MXN. "
                          "Texto intermedio largo. se impone a GI NEXXUS una multa como sanción", "X")])
        assert verificar_cita(r, 128, "se impone a CI NEXXUS una multa ... "
                                      "se impone a GI NEXXUS una multa")["verificada"]
        assert not verificar_cita(r, 128, "se impone a CI NEXXUS una multa ... "
                                          "se impone a BANCO INVEX una multa")["verificada"]


class TestParser:
    def test_rango_de_paginas_y_no_consta_omitido(self, resolucion):
        salida = ("RESPUESTA:\nSon distintos [1].\nCITAS:\n"
                  "[1] p. 13-14 | \"los incrementos de capital son distintos\"")
        lec = asyncio.run(leer(_Adaptador(salida), "gpt-4.1", resolucion, "¿?"))
        assert lec.error is None and lec.citas_verificadas == 1
        assert lec.respuesta == "Son distintos [1]." and not lec.no_consta


# ── La herramienta en el agente ─────────────────────────────────────────────

class _Clientes:
    """Doble del cliente de resolución: mismo contrato que el real."""

    def __init__(self, resolucion):
        self.resolucion, self.pedidas = resolucion, []

    async def obtener(self, case_link):
        self.pedidas.append(case_link)
        return self.resolucion


class _Universo:
    case_links = {"VCN-005-2024"}


class _Estadistica:
    universo = None


def _agente(cliente, universo=None):
    from agent.agent import NormaPlusAgent
    ag = NormaPlusAgent.__new__(NormaPlusAgent)
    ag.resoluciones = cliente
    ag.max_lecturas_resolucion = 3
    ag.estadistica = _Estadistica()
    ag.estadistica.universo = universo
    return ag


def _estado(salida):
    from agent.turn_state import TurnState
    st = TurnState()
    st.llm = (_Adaptador(salida), "gpt-4.1")
    return st


_SALIDA_BUENA = ("RESPUESTA:\nSon distintos [1]. Hubo multa [2].\nCITAS:\n"
                 "[1] p. 13 | los incrementos de capital son distintos\n"
                 "[2] p. 14 | se impuso una multa de diez millones\nNO_CONSTA: no")


class TestHerramienta:
    def test_lectura_con_marcador_paginas_y_costo(self, resolucion):
        ag, st = _agente(_Clientes(resolucion)), _estado(_SALIDA_BUENA)
        r = asyncio.run(ag._exec_leer_resolucion(
            {"expediente": "VCN-005-2024", "pregunta": "¿?"}, None, st))
        assert r["ref"] == "E1" and st.registry.case_link_of("E1") == "VCN-005-2024"
        # Las marcas del lector se vuelven páginas y la oración sin respaldo se va.
        assert r["lectura"] == "Son distintos (p. 13)."
        assert r["pasajes_verificados"] == [
            {"pagina": 13, "texto": "los incrementos de capital son distintos"}]
        # El costo del lector cuenta en el turno, y la evidencia queda acumulada.
        assert st.tokens_lector_entrada == 100_000
        assert any(d.get("ref") == "E1" for d in st.evidencia_acumulada)
        lec = st.lecturas_resolucion[0]
        assert lec["citas_verificadas"] == 1 and lec["oraciones_retiradas"] == ["Hubo multa [2]."]

    def test_fuera_del_universo_no_se_lee(self, resolucion):
        cli = _Clientes(resolucion)
        r = asyncio.run(_agente(cli, _Universo())._exec_leer_resolucion(
            {"expediente": "CNT-018-2023", "pregunta": "¿?"}, None, _estado("")))
        assert not r["leida"] and cli.pedidas == []

    def test_tope_por_turno(self, resolucion):
        ag, st = _agente(_Clientes(resolucion)), _estado(_SALIDA_BUENA)
        for _ in range(3):
            asyncio.run(ag._exec_leer_resolucion({"expediente": "VCN-005-2024", "pregunta": "¿?"}, None, st))
        r = asyncio.run(ag._exec_leer_resolucion({"expediente": "VCN-005-2024", "pregunta": "¿?"}, None, st))
        assert not r["leida"] and st.lecturas_resolucion[-1]["motivo"] == "tope"

    def test_no_disponible_no_afirma_inexistencia(self):
        ag, st = _agente(_Clientes(armar_resolucion("X", []))), _estado("nunca")
        r = asyncio.run(ag._exec_leer_resolucion({"expediente": "X", "pregunta": "¿?"}, None, st))
        assert not r["leida"] and "no existe" in r["instruccion"]
        assert st.llm[0].mensajes is None and st.registry.markers() == []

    def test_sin_cliente_configurado(self):
        r = asyncio.run(_agente(None)._exec_leer_resolucion(
            {"expediente": "X", "pregunta": "¿?"}, None, _estado("")))
        assert not r["leida"]

    def test_esta_registrada_en_el_agente_el_prompt_y_las_herramientas(self):
        from agent.tools import TOOLS
        from prompts.system import AGENT_SYSTEM_PROMPT
        assert any(t["name"] == "leer_resolucion" for t in TOOLS)
        assert "leer_resolucion" in AGENT_SYSTEM_PROMPT

    def test_main_importa_el_cliente(self):
        # El arranque falló una vez con NameError: el servicio no levantaba.
        import main
        assert main.ResolucionDigitalClient is not None


class TestMarcadorConPagina:
    def test_pagina_dentro_del_corchete_se_saca(self):
        from core.validacion_salida import normalizar_marcadores
        texto, n = normalizar_marcadores("dijo X[E1, p. 15]. Y [ E2, pp. 183-184].")
        assert texto == "dijo X[E1] (p. 15). Y [E2] (pp. 183-184)." and n == 2
