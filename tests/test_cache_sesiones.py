"""
Desalojo del caché de evidencia por sesión (21-oct-2026, reporte de JM).

`EvidenceCache` guardaba cada session_id hasta reiniciar el proceso: la
memoria crecía con cada conversación hasta que ECS mataba la tarea.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.evidence_cache import EvidenceCache


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


CRIT = [{"id": "1", "text": "t", "metadata": {"id_expediente": "VCN-001-2017"}}]


def _cache(ttl=100, maximo=3):
    reloj = Reloj()
    return EvidenceCache(ttl_segundos=ttl, max_sesiones=maximo, reloj=reloj), reloj


def _escribir(c, s):
    c.update(session_id=s, query="q", criterios=CRIT, expedientes=[])
    c.recordar_anclas(s, {"1": "ancla"})


def test_inactiva_mas_del_ttl_se_desaloja():
    c, reloj = _cache(ttl=100)
    _escribir(c, "a")
    reloj.t = 101
    assert c.session_count() == 0
    assert c.anclas("a") == {}
    assert c.select("a", "y ese caso?") == ([], [], False)


def test_pasado_el_maximo_sale_la_menos_usada():
    c, reloj = _cache(maximo=2)
    _escribir(c, "a"); reloj.t = 1
    _escribir(c, "b"); reloj.t = 2
    c.select("a", "lo anterior")   # "a" se usa: ahora la menos usada es "b"
    reloj.t = 3
    _escribir(c, "c")
    assert set(c._sessions) == {"a", "c"}
    assert "b" not in c._anclas


def test_una_sesion_en_uso_sobrevive_al_ttl_contado_desde_su_creacion():
    c, reloj = _cache(ttl=100)
    _escribir(c, "a")
    for t in (60, 120, 180):
        reloj.t = t
        assert c.anclas("a") == {"1": "ancla"}
    assert c.session_count() == 1


def test_leer_una_sesion_inexistente_no_la_crea():
    c, _ = _cache()
    c.select("fantasma", "x"); c.anclas("fantasma")
    assert "fantasma" not in c._acceso


def test_clear_session_borra_evidencia_y_anclas():
    c, _ = _cache()
    _escribir(c, "a")
    c.clear_session("a")
    assert "a" not in c._sessions and "a" not in c._anclas and "a" not in c._acceso
    c.clear_session("a")  # idempotente


def _app(cache):
    from routers import chat
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")

    class _Ag:
        evidence_cache = cache
    app.dependency_overrides[chat.get_agent] = lambda: _Ag()
    return TestClient(app)


def test_endpoint_borra_la_sesion_y_es_idempotente():
    c, _ = _cache()
    _escribir(c, "a")
    cli = _app(c)
    r = cli.delete("/api/chat/sessions/a/cache")
    assert r.status_code == 204 and r.content == b""
    assert "a" not in c._sessions and "a" not in c._anclas
    assert cli.delete("/api/chat/sessions/a/cache").status_code == 204
    assert cli.delete("/api/chat/sessions/nunca-existio/cache").status_code == 204


def test_endpoint_fuera_del_esquema_publico():
    cli = _app(_cache()[0])
    assert "/api/chat/sessions/{session_id}/cache" not in cli.get("/openapi.json").json()["paths"]
