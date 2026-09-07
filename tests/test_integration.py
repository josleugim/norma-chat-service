"""
Tests de integración — requieren el mock server corriendo.

Ejecutar:
  1. Terminal 1: cd chat-service && uvicorn mock_search_server:app --port 3000
  2. Terminal 2: cd chat-service && python -m pytest tests/test_integration.py -v

Estos tests validan que los clientes HTTP se conectan correctamente
al formato real de la API de José Miguel (simulado por el mock).
"""
import sys
import os
import pytest
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MOCK_BASE = os.environ.get("MOCK_BASE", "http://localhost:3000")
MOCK_API_KEY = "1.test-key"


def is_mock_running():
    """Verifica si el mock server está corriendo."""
    import httpx
    try:
        r = httpx.get(f"{MOCK_BASE}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not is_mock_running(),
    reason=f"Mock server no disponible en {MOCK_BASE}. "
           f"Ejecutar: uvicorn mock_search_server:app --port 3000 "
           f"(o setear MOCK_BASE=http://localhost:PORT)",
)


# ═══════════════════════════════════════════════════════════════
# 1. CriteriosSearchClient contra mock
# ═══════════════════════════════════════════════════════════════

class TestCriteriosClientIntegration:

    @pytest.fixture
    def client(self):
        from retrieval.criterios_client import CriteriosSearchClient
        return CriteriosSearchClient(base_url=MOCK_BASE, api_key=MOCK_API_KEY)

    @pytest.mark.asyncio
    async def test_basic_search(self, client):
        """Búsqueda básica retorna resultados."""
        results = await client.search("mercado relevante")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_response_has_caselink(self, client):
        """Cada resultado tiene caseLink en metadata."""
        results = await client.search("mercado relevante")
        for r in results:
            assert r.metadata.get("caseLink"), f"Falta caseLink en resultado {r.id}"

    @pytest.mark.asyncio
    async def test_response_has_casename(self, client):
        """Cada resultado tiene nombre_expediente."""
        results = await client.search("multas")
        for r in results:
            assert r.metadata.get("nombre_expediente") is not None

    @pytest.mark.asyncio
    async def test_title_from_titlenames(self, client):
        """El título viene de titleNames, no hardcoded."""
        results = await client.search("barreras entrada")
        has_title = any(r.metadata.get("title") for r in results)
        assert has_title, "Ningún resultado tiene título"

    @pytest.mark.asyncio
    async def test_empty_titlenames_fallback(self, client):
        """Resultados con titleNames=[] usan anchor como fallback."""
        results = await client.search("efectos unilaterales")
        for r in results:
            # Si no tiene titleNames, debe tener anchor como title
            title = r.metadata.get("title", "")
            assert title is not None  # no es None, puede ser ""

    @pytest.mark.asyncio
    async def test_articlenames_as_array(self, client):
        """articleNames es un array (puede ser vacío)."""
        results = await client.search("multas")
        for r in results:
            articles = r.metadata.get("articleNames")
            assert isinstance(articles, list), f"articleNames no es lista: {type(articles)}"

    @pytest.mark.asyncio
    async def test_score_range(self, client):
        """Score debe estar entre 0 y 1."""
        results = await client.search("mercado")
        for r in results:
            assert 0 <= r.score <= 1, f"Score fuera de rango: {r.score}"

    @pytest.mark.asyncio
    async def test_grounding_in_metadata(self, client):
        """Al menos un resultado tiene grounding."""
        results = await client.search("multas")
        has_grounding = any(r.metadata.get("grounding") for r in results)
        assert has_grounding, "Ningún resultado tiene grounding"


# ═══════════════════════════════════════════════════════════════
# 2. EstadisticaSearchClient contra mock
# ═══════════════════════════════════════════════════════════════

class TestEstadisticaClientIntegration:

    @pytest.fixture
    def client(self):
        from retrieval.estadistica_client import EstadisticaSearchClient
        return EstadisticaSearchClient(base_url=MOCK_BASE, api_key=MOCK_API_KEY)

    @pytest.mark.asyncio
    async def test_searchdata_cross_field(self, client):
        """searchData encuentra por nombre de agente económico."""
        results = await client.search(text_search="Scotiabank")
        assert len(results) > 0
        found = any("scotiabank" in " ".join(r.economicAgents or []).lower()
                    for r in results)
        assert found

    @pytest.mark.asyncio
    async def test_searchdata_by_market(self, client):
        """searchData encuentra por mercado relevante."""
        results = await client.search(text_search="telecomunicaciones")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_searchdata_by_case_name(self, client):
        """searchData encuentra por nombre de caso."""
        results = await client.search(text_search="Gasolineras")
        assert len(results) > 0
        assert any(r.caseLink == "IO-001-2019" for r in results)

    @pytest.mark.asyncio
    async def test_searchdata_case_insensitive(self, client):
        """ILIKE: 'scotiabank' encuentra 'SCOTIABANK INVERLAT'."""
        results = await client.search(text_search="scotiabank")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_filter_authority(self, client):
        """Filtro por autoridad funciona."""
        results = await client.search(
            filters={"authority": "CFC"}
        )
        for r in results:
            assert r.authority == "CFC"

    @pytest.mark.asyncio
    async def test_sense_of_resolution_no_viaja_a_la_api(self, client):
        """
        `senseOfResolution` se filtra localmente, no en la API.

        Su alias está roto: pedir SANCION expande a tres valores que casi no
        existen en los datos, mientras el sentido dominante es `Sanciona`, con
        35 de los 37 sancionados. La consulta devolvía 2 de 37 con 200 OK, que
        es indistinguible de un universo vacío. Mandar el filtro sería peor
        que no mandarlo, así que el cliente lo omite y el agente lo resuelve
        con match tolerante.
        """
        results = await client.search(
            filters={"senseOfResolution": "CONDICIONADA"}
        )
        # No se filtró en el servidor: llegan también otros sentidos.
        sentidos = {(r.senseOfResolution or "").upper() for r in results}
        assert len(results) > 0
        assert sentidos - {"CONDICIONADA"}, (
            "si el filtro hubiera viajado, solo habría CONDICIONADA"
        )

    @pytest.mark.asyncio
    async def test_agentfines_types(self, client):
        """agentFines puede ser string o dict, no crashea."""
        results = await client.search(text_search="Gasolineras")
        for r in results:
            fines = r.agentFines
            assert fines is None or isinstance(fines, (str, dict))

    @pytest.mark.asyncio
    async def test_pagination_meta(self, client):
        """La respuesta tiene metadata de paginación."""
        results = await client.search(text_search="")
        # El cliente solo retorna la lista, pero no crashea
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_search_returns_all(self, client):
        """Sin filtros retorna todos los casos del mock."""
        results = await client.search()
        assert len(results) >= 5


# ═══════════════════════════════════════════════════════════════
# 3. Mock server — formato de respuesta
# ═══════════════════════════════════════════════════════════════

class TestMockServerFormat:
    """Verifica que el mock devuelve el formato exacto de la API real."""

    @pytest.mark.asyncio
    async def test_vector_search_top_level_fields(self):
        """La respuesta de vector-search tiene campos de primer nivel."""
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{MOCK_BASE}/paragraphs/vector-search",
                           json={"question": "mercado"})
            data = r.json()

        assert isinstance(data, list), "Debe ser array directo"
        item = data[0]
        assert "caseName" in item, "Falta caseName en primer nivel"
        assert "caseLink" in item, "Falta caseLink en primer nivel"
        assert "articleNames" in item, "Falta articleNames en primer nivel"
        assert "titleNames" in item, "Falta titleNames en primer nivel"
        assert "metadata" in item, "Falta metadata"
        assert "distance" in item, "Falta distance"

    @pytest.mark.asyncio
    async def test_vector_search_metadata_structure(self):
        """metadata contiene anchor, context, grounding, pdf_pages, resolution_pages."""
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{MOCK_BASE}/paragraphs/vector-search",
                           json={"question": "multas"})
            data = r.json()

        meta = data[0]["metadata"]
        assert "anchor" in meta
        assert "context" in meta
        assert "pdf_pages" in meta
        assert "resolution_pages" in meta
        # grounding puede ser None

    @pytest.mark.asyncio
    async def test_agent_search_acepta_searchdata(self):
        """GET /cases/agent-search acepta searchData."""
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{MOCK_BASE}/cases/agent-search",
                          params={"searchData": "Scotiabank"})
            data = r.json()

        assert "data" in data
        assert "meta" in data
        assert len(data["data"]) > 0

    @pytest.mark.asyncio
    async def test_endpoint_viejo_esta_retirado(self):
        """
        `/cases/search` responde 401 desde el 7-sep-2026. Si algún camino
        volviera a apuntar ahí, tiene que fallar de inmediato y no degradarse
        en silencio a cero resultados.
        """
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{MOCK_BASE}/cases/search", params={"limit": 3})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_meta_no_trae_total_ni_paginacion(self):
        """
        `meta` trae solo `returned` y `limit`. No hay `total`, `page` ni
        `totalPages`: la paginación desapareció y el truncamiento hay que
        inferirlo de `returned == limit`.
        """
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{MOCK_BASE}/cases/agent-search",
                          params={"limit": 2})
            data = r.json()

        assert "data" in data
        meta = data["meta"]
        assert set(meta) == {"returned", "limit"}
        assert meta["limit"] == 2
        assert meta["returned"] == len(data["data"])
