"""
Pruebas del cliente contra `GET /cases/agent-search`.

Lo que se garantiza aquí es exactamente lo que la API nueva NO garantiza por
su cuenta, y que ya nos costó caro antes:

1. Un filtro con nombre desconocido revienta en vez de viajar. La API ignora
   los parámetros que no conoce y responde 200 OK con el universo entero, así
   que reenviarlos produce respuestas sin filtrar que parecen correctas.
2. `senseOfResolution` nunca se manda: su alias devuelve 2 de 37 sancionados.
3. `agentFines=false` se manda como filtro, no se confunde con "sin filtro".
4. El truncamiento no se afirma como total. Sin `meta.total`, `returned ==
   limit` es lo único que hay, y es una estimación.
5. La búsqueda exhaustiva hace UNA petición. La API no tiene `page`, así que
   el bucle anterior habría pedido la misma página una y otra vez,
   duplicando registros sin un solo error.
"""
import httpx
import pytest

from retrieval.estadistica_client import (
    EstadisticaSearchClient, FiltroDesconocidoError,
)


@pytest.fixture
def peticiones(monkeypatch):
    """Captura las peticiones salientes y devuelve una respuesta vacía."""
    registro: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        registro.append(request)
        return httpx.Response(200, json={"data": [], "meta": {"returned": 0, "limit": 50}})

    _instalar(monkeypatch, handler)
    return registro, None


def _params(request: httpx.Request) -> dict:
    return dict(request.url.params)


# ── 1. Filtros desconocidos ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_filtro_desconocido_revienta(peticiones):
    """
    El modelo escribe `aplicableLaw` con una sola p; la API lo ignoraría y
    devolvería los 4,662 registros sin filtrar, con 200 OK.
    """
    registro, _ = peticiones
    client = EstadisticaSearchClient("https://api.test", "1.k")

    with pytest.raises(FiltroDesconocidoError) as exc:
        await client.search(filters={"aplicableLaw": "LFCE 2014"})

    assert "aplicableLaw" in str(exc.value)
    assert not registro, "no debe salir ninguna petición con un filtro inválido"


@pytest.mark.asyncio
async def test_rango_de_anios_incompleto_revienta(peticiones):
    """La API responde 400 si va un solo extremo; se detecta antes de llamar."""
    registro, _ = peticiones
    client = EstadisticaSearchClient("https://api.test", "1.k")

    with pytest.raises(FiltroDesconocidoError):
        await client.search(filters={"fecha_resolucion_desde": 2018})
    assert not registro


# ── 2. senseOfResolution no viaja ────────────────────────────────────

@pytest.mark.asyncio
async def test_sentido_de_resolucion_no_se_manda(peticiones):
    """
    `?senseOfResolution=SANCION` devuelve 2 de 37 casos sancionados porque el
    valor dominante es `Sanciona` y el alias no lo cubre. Se filtra localmente.
    """
    registro, _ = peticiones
    client = EstadisticaSearchClient("https://api.test", "1.k")

    await client.search(filters={
        "senseOfResolution": "SANCION",
        "authority": "COFECE",
    })

    params = _params(registro[0])
    assert "senseOfResolution" not in params
    assert params["authority"] == "COFECE"


# ── 3. agentFines triestado ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_fines_false_es_un_filtro(peticiones):
    """False significa 'sin multa', no 'no filtres'."""
    registro, _ = peticiones
    client = EstadisticaSearchClient("https://api.test", "1.k")

    await client.search(filters={"has_multas": False})
    assert _params(registro[0])["agentFines"] == "false"

    await client.search(filters={"has_multas": True})
    assert _params(registro[1])["agentFines"] == "true"


@pytest.mark.asyncio
async def test_agent_fines_none_no_filtra(peticiones):
    registro, _ = peticiones
    client = EstadisticaSearchClient("https://api.test", "1.k")

    await client.search(filters={"has_multas": None})
    assert "agentFines" not in _params(registro[0])


# ── 4. Truncamiento sin meta.total ───────────────────────────────────

@pytest.mark.asyncio
async def test_returned_igual_a_limit_no_afirma_total(monkeypatch):
    """
    Si la API devolvió justo el tope, el universo pudo quedar cortado. No hay
    `meta.total` para desempatar, así que no se afirma ningún total.
    """
    def handler(request):
        return httpx.Response(200, json={
            "data": [{"caseLink": f"CNT-{i:03d}-2024"} for i in range(50)],
            "meta": {"returned": 50, "limit": 50},
        })

    _instalar(monkeypatch, handler)
    client = EstadisticaSearchClient("https://api.test", "1.k")
    await client.search(limit=50)

    assert client.last_truncado is True
    assert client.last_total is None


@pytest.mark.asyncio
async def test_returned_menor_a_limit_si_afirma_total(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={
            "data": [{"caseLink": f"VCN-{i:03d}-2024"} for i in range(39)],
            "meta": {"returned": 39, "limit": 5000},
        })

    _instalar(monkeypatch, handler)
    client = EstadisticaSearchClient("https://api.test", "1.k")
    registros, total, completo = await client.fetch_universe()

    assert completo is True
    assert total == 39
    assert client.last_total == 39
    assert len(registros) == 39


# ── 5. La exhaustiva hace UNA petición ───────────────────────────────

@pytest.mark.asyncio
async def test_busqueda_exhaustiva_no_pagina(monkeypatch):
    """
    `agent-search` no tiene `page`. Si se paginara, la API ignoraría el
    parámetro y devolvería la misma primera página una y otra vez: registros
    duplicados hasta llenar el tope, sin un solo error visible.
    """
    llamadas = []

    def handler(request):
        llamadas.append(request)
        return httpx.Response(200, json={
            "data": [{"caseLink": f"CNT-{i:03d}-2024"} for i in range(100)],
            "meta": {"returned": 100, "limit": 100},
        })

    _instalar(monkeypatch, handler)
    client = EstadisticaSearchClient("https://api.test", "1.k")
    resultados = await client.search_all_pages(max_results=100)

    assert len(llamadas) == 1, "debe ser una sola petición, no un bucle"
    assert "page" not in _params(llamadas[0])
    links = [r.caseLink for r in resultados]
    assert len(links) == len(set(links)), "no debe haber duplicados"


# ── 6. El prefijo viaja como filtro ──────────────────────────────────

@pytest.mark.asyncio
async def test_prefijo_va_como_case_link_con_los_demas_filtros(monkeypatch):
    """
    Con AND del servidor ya no hay que mandar un solo filtro: el prefijo se
    combina con la autoridad en la misma petición.
    """
    llamadas = []

    def handler(request):
        llamadas.append(request)
        return httpx.Response(200, json={
            "data": [{"caseLink": "VCN-004-2024", "authority": "COFECE"}],
            "meta": {"returned": 1, "limit": 5000},
        })

    _instalar(monkeypatch, handler)
    client = EstadisticaSearchClient("https://api.test", "1.k")
    resultados = await client.fetch_by_prefix("VCN", filters={"authority": "COFECE"})

    params = _params(llamadas[0])
    assert params["caseLink"] == "VCN-"
    assert params["authority"] == "COFECE"
    assert [r.caseLink for r in resultados] == ["VCN-004-2024"]


@pytest.mark.asyncio
async def test_guarda_de_prefijo_descarta_lo_que_no_coincide(monkeypatch):
    """`caseLink` es match parcial: puede colar algo que no empieza igual."""
    def handler(request):
        return httpx.Response(200, json={
            "data": [
                {"caseLink": "VCN-004-2024"},
                {"caseLink": "CNT-VCN-99-2020"},
            ],
            "meta": {"returned": 2, "limit": 5000},
        })

    _instalar(monkeypatch, handler)
    client = EstadisticaSearchClient("https://api.test", "1.k")
    resultados = await client.fetch_by_prefix("VCN")

    assert [r.caseLink for r in resultados] == ["VCN-004-2024"]


# ── Utilidad ─────────────────────────────────────────────────────────

def _instalar(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def init(self, *args, **kwargs):
        kwargs.setdefault("transport", transport)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
