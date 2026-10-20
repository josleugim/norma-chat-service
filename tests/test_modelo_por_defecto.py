"""
Modelo por defecto y modelo fijo por configuración (19-oct-2026).

COFECE pidió subir el sitio a Claude Sonnet 5.5. El modelo venía fijo en el
esquema de la petición (`gpt-4.1`), así que cambiarlo exigía tocar el proxy o
el front. Ahora se elige con variables de entorno.
"""
from models.schemas import ChatRequest
from config import Settings
from routers.chat import resolver_modelo

SONNET = dict(agent_default_provider="anthropic", agent_default_model="claude-sonnet-5-5")


def _req(**kw):
    return ChatRequest(session_id="s", query="q", **kw)


def test_sin_configurar_sigue_siendo_gpt41():
    assert resolver_modelo(_req(), Settings(_env_file=None)) == ("openai", "gpt-4.1")


def test_la_peticion_sin_modelo_usa_el_de_la_configuracion():
    assert resolver_modelo(_req(), Settings(_env_file=None, **SONNET)) == (
        "anthropic", "claude-sonnet-5-5")


def test_sin_modelo_fijo_manda_el_cliente():
    r = _req(provider="openai", model="gpt-4.1")
    assert resolver_modelo(r, Settings(_env_file=None, **SONNET)) == ("openai", "gpt-4.1")


def test_con_modelo_fijo_se_ignora_lo_que_mande_el_cliente():
    r = _req(provider="openai", model="gpt-4.1")
    s = Settings(_env_file=None, agent_modelo_fijo=True, **SONNET)
    assert resolver_modelo(r, s) == ("anthropic", "claude-sonnet-5-5")


def test_provider_y_modelo_van_juntos():
    """Un modelo sin proveedor no se mezcla con el proveedor de la configuración."""
    r = _req(model="gpt-4.1")
    assert resolver_modelo(r, Settings(_env_file=None, **SONNET)) == (
        "anthropic", "claude-sonnet-5-5")


def test_no_arranca_si_el_proveedor_por_defecto_no_tiene_llave():
    import inspect
    import main
    fuente = inspect.getsource(main.lifespan)
    assert "agent_default_provider not in registry.providers" in fuente
