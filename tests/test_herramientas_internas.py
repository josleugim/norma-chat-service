"""
Herramientas internas para el servidor MCP del repo aparte.

La lógica se queda en el chat; el otro repo sólo reenvía. Estas pruebas fijan
que el catálogo trae lo necesario para registrar las herramientas sin copiar
descripciones, que el resultado viene en formato MCP, y que nada queda
expuesto sin llave.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_server import servidor as modulo_servidor
from routers.herramientas_internas import router

LLAVE = "llave-de-prueba"


class _Agente:
    async def _exec_calcular_plazos(self, args, _tool_id, _state):
        return {"fecha_inicio": "2018-12-21", "fecha_fin": "2019-01-24",
                "dias_habiles": 14, "dias_naturales": 34, "institucion": args["institucion"]}

    async def _exec_buscar_expedientes(self, args, _tool_id, _state):
        return []


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(modulo_servidor, "_agente", _Agente())
    app = FastAPI()
    app.state.settings = SimpleNamespace(internal_tools_key=LLAVE)
    app.include_router(router)
    return TestClient(app)


def _h(llave=LLAVE):
    return {"X-Internal-Key": llave}


def test_sin_llave_o_con_llave_equivocada_es_401(cli):
    assert cli.get("/internal/tools").status_code == 401
    assert cli.get("/internal/tools", headers=_h("otra")).status_code == 401
    assert cli.post("/internal/tools/dias_entre_fechas", json={}, headers=_h("otra")).status_code == 401


def test_llave_vacia_en_la_configuracion_no_abre_nada(cli):
    cli.app.state.settings.internal_tools_key = ""
    assert cli.get("/internal/tools", headers=_h("")).status_code == 401


def test_el_catalogo_trae_lo_necesario_para_registrar_las_herramientas(cli):
    r = cli.get("/internal/tools", headers=_h()).json()
    assert r["servidor"]["name"] == "Norma+" and "cadenas procesales" in r["servidor"]["instructions"]
    nombres = {t["name"] for t in r["tools"]}
    assert nombres == {"buscar_criterios", "buscar_expedientes", "ver_expediente",
                       "historia_procesal", "calcular_estadistica", "dias_entre_fechas"}
    for t in r["tools"]:
        assert t["description"] and t["inputSchema"]["type"] == "object", t["name"]
        assert t["annotations"]["readOnlyHint"] is True, t["name"]


def test_ejecuta_y_devuelve_el_resultado_en_formato_mcp(cli):
    r = cli.post("/internal/tools/dias_entre_fechas", headers=_h(),
                 json={"fecha_inicio": "21-12-2018", "fecha_fin": "24-01-2019"})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo.get("isError") is not True
    # Igual que /mcp hoy: el resultado va como texto JSON en `content`.
    assert cuerpo["content"][0]["type"] == "text"
    assert json.loads(cuerpo["content"][0]["text"])["dias_habiles"] == 14


def test_un_error_de_la_herramienta_va_como_resultado_no_como_http(cli):
    r = cli.post("/internal/tools/ver_expediente", headers=_h(), json={"expediente": "VCN-999-2099"})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["isError"] is True
    assert "VCN-999-2099" in cuerpo["content"][0]["text"]


def test_argumentos_invalidos_van_como_error_de_herramienta(cli):
    r = cli.post("/internal/tools/dias_entre_fechas", headers=_h(), json={"fecha_inicio": "2018-12-21"})
    assert r.status_code == 200 and r.json()["isError"] is True


def test_herramienta_desconocida_es_404(cli):
    assert cli.post("/internal/tools/borrar_todo", headers=_h(), json={}).status_code == 404


def test_sin_llave_configurada_las_rutas_no_se_montan():
    from config import Settings
    assert Settings.model_fields["internal_tools_key"].default == ""
    import main
    assert not any(getattr(r, "path", "").startswith("/internal") for r in main.app.router.routes)
