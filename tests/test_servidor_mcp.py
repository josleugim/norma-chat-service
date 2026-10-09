"""
Conector MCP ("@norma" en Claude y ChatGPT), fase 1.

Fija las reglas de los directorios de Claude y ChatGPT (leídas el 9-oct-2026):
herramientas de sólo lectura que lo declaran, descripciones que no le dicen al
modelo cómo comportarse, y resultados que son datos. Y las nuestras: nada de
nombres de campo de la API ni de instrucciones pensadas para nuestro agente.
"""
import asyncio
import json
import re

from mcp_server import limpieza

# Palabras que delatan una instrucción al modelo en vez de una descripción.
IMPERATIVOS = re.compile(
    r"\b(SIEMPRE|IMPORTANTE|NUNCA|debes|úsalo|usa exactamente|no lo afirmes|"
    r"dilo|no digas|no afirmes|insinú|OJO)\b", re.IGNORECASE)
CAMPO_DE_API = re.compile(r"\b[a-z]+[A-Z][A-Za-z]+\b")  # camelCase


def _herramientas():
    from mcp_server.servidor import servidor
    return asyncio.run(servidor.list_tools())


class TestHerramientas:

    def test_son_las_esperadas(self):
        assert {t.name for t in _herramientas()} == {
            "buscar_criterios", "buscar_expedientes", "ver_expediente",
            "historia_procesal", "calcular_estadistica", "dias_entre_fechas"}

    def test_todas_son_de_solo_lectura_y_lo_declaran(self):
        for t in _herramientas():
            a = t.annotations
            assert a is not None and a.read_only_hint is True, t.name
            assert a.destructive_hint is False, t.name
            assert a.title, t.name

    def test_nombres_cortos(self):
        assert all(len(t.name) <= 64 for t in _herramientas())

    def test_las_descripciones_no_dan_instrucciones(self):
        for t in _herramientas():
            assert not IMPERATIVOS.search(t.description), (t.name, t.description)

    def test_parametros_sin_nombres_de_la_api(self):
        for t in _herramientas():
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", {})
            for nombre in (schema.get("properties") or {}):
                assert not CAMPO_DE_API.fullmatch(nombre), (t.name, nombre)


REGISTRO = {
    "id": 25066, "caseLink": "VCN-001-2017", "name": "Notarios", "authority": "COFECE",
    "typeOfProcedure": "Concentración no notificada", "startAgreementDate": "10-02-2017",
    "resolutionDate": "18-05-2017", "senseOfResolution": ["Sanciona"],
    "agentFines": {"Cecilio González Márquez": "$8545680.00"},
    "dissentingOpinions": "Voto particular de X", "resolutionFileUrl": "https://s3/firmada",
    "campoNuevoDeLaApi": "algo",
}


class TestLimpieza:

    def test_la_ficha_usa_nombres_en_espanol_y_url_absoluta(self):
        f = limpieza.expediente(REGISTRO, "https://normaplus.ai")
        assert f["votos_particulares"] == "Voto particular de X"
        assert f["url"] == "https://normaplus.ai/case-search?caseLink=VCN-001-2017&details=true"
        assert not any(CAMPO_DE_API.fullmatch(k) for k in f)

    def test_un_campo_nuevo_de_la_api_no_se_expone_sin_revisarlo(self):
        f = limpieza.expediente(REGISTRO, "https://normaplus.ai")
        assert "campoNuevoDeLaApi" not in f and "algo" not in f.values()
        assert "resolutionFileUrl" not in json.dumps(f)

    def test_la_agregacion_no_trae_instrucciones_ni_campos_de_api(self):
        r = {
            "operacion": "promedio", "metrica": "dias_habiles", "resultado": 43.66,
            "procesados": 32, "con_valor": 32, "sin_valor": 0, "cobertura_completa": True,
            "campo_inicio": "default por tipo", "campo_fin": "resolutionDate",
            "COMO_DEBES_DESCRIBIR_LA_COBERTURA": (
                "Se analizaron 34 expedientes. OJO: el cálculo mezcla autoridades "
                "(32 de COFECE, 2 de CNA). No lo atribuyas a una sola; vuelve a "
                "calcular. USA EXACTAMENTE ESTAS CIFRAS: no digas…"),
            "ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS": {"count": 4, "actos": [], "nota": "no los describas así"},
        }
        a = limpieza.agregacion(r, "https://normaplus.ai")
        texto = json.dumps(a, ensure_ascii=False)
        assert not IMPERATIVOS.search(texto), texto
        assert "resolutionDate" not in texto
        assert a["descripcion_del_calculo"].startswith("Se analizaron 34 expedientes.")
        assert "El cálculo mezcla autoridades (32 de COFECE, 2 de CNA)" in a["descripcion_del_calculo"]
        assert not any(k.isupper() for k in a)

    def test_la_historia_dice_hechos_no_ordenes(self):
        h = {
            "deriva_de": [{"posterior": "480_2018_2SCJN", "origen": None, "estado": "externo",
                           "referencia_informada": "82/2018 · Segundo TCC · 10-05-2018",
                           "motivo": "no está en el acervo: no se puede consultar ni citar. Dilo."}],
            "actuaciones_posteriores": [
                {"expediente": "1258_2017_2JD", "deriva_de": "VCN-001-2017", "tipo": "Amparo indirecto",
                 "fecha_de_la_resolucion": "28-02-2018", "enlace": "resuelto",
                 "senseOfAmparo": ["concede"]},
                {"expediente": "VCN-001-2017_2019_03_14", "deriva_de": "VCN-001-2017",
                 "tipo": "En cumplimiento de amparo", "enlace": "resuelto",
                 "sentencia_que_cumple": "NO INFORMADA. No digas cuál amparo cumple."}],
            "NO_ENLAZADO": "Aunque sólo uno haya concedido, no lo afirmes.",
            "LIMITE": "480_2018_2SCJN: … Dilo.",
        }
        out = limpieza.historia(h, "https://normaplus.ai")
        texto = json.dumps(out, ensure_ascii=False)
        assert not IMPERATIVOS.search(texto), texto
        assert "no está en el acervo de Norma+" in texto
        assert "no indica cuál de los amparos" in out["nota"]
        assert out["actuaciones_posteriores"][0]["sentido_del_amparo"] == ["concede"]
        assert out["actuaciones_posteriores"][0]["tipo_de_fuente"] == "sentencia"

    def test_el_plazo_no_trae_la_regla_para_nuestro_agente(self):
        r = {"fecha_inicio": "2018-12-21", "fecha_fin": "2019-01-24", "dias_habiles": 14,
             "dias_naturales": 34, "institucion": "COFECE",
             "ALCANCE_DE_LA_CIFRA": {"denominacion": "días hábiles según calendario general",
                                     "ajusta_suspensiones": False, "regla": "Conserva esta denominación…"},
             "ACUERDOS_DE_SUSPENSION_COINCIDENTES": {"acuerdos": ["CFCE-084-2020"],
                                                     "regla": "NO afirmes que aplican"}}
        p = limpieza.plazo(r)
        texto = json.dumps(p, ensure_ascii=False)
        assert "regla" not in texto and not IMPERATIVOS.search(texto)
        assert p["dias_habiles"] == 14
        assert p["acuerdos_de_suspension_que_coinciden"]["acuerdos"] == ["CFCE-084-2020"]


def test_el_conector_esta_apagado_por_omision():
    """Sin login (fase 1), no puede quedar expuesto al desplegar."""
    from config import Settings
    assert Settings.model_fields["mcp_enabled"].default is False
    import main
    assert not any(getattr(r, "path", "") == "/mcp" for r in main.app.router.routes)
