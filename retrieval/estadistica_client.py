"""
Cliente HTTP para búsqueda de expedientes/casos.
Consume el endpoint real de José Miguel:
GET https://norma-api-xxx.run.app/cases/search

Es un GET con query params, NO un POST.
Respuesta paginada: { "data": [...], "meta": { "total", "page", "limit", "totalPages" } }
"""
import logging
import httpx
from models.schemas import ExpedienteRecord

logger = logging.getLogger(__name__)


class EstadisticaSearchClient:

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 15.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # Total de la última búsqueda (meta.total). Permite que el agente
        # sepa cuánto universo quedó fuera en vez de responder a ciegas.
        self.last_total: int | None = None

    async def search(
        self,
        text_search: str | None = None,
        filters: dict | None = None,
        limit: int = 50,
        page: int = 1,
        search_field: str | None = None,
        collector=None,
        prefijo: str | None = None,
    ) -> list[ExpedienteRecord]:
        """
        Búsqueda de expedientes/casos vía GET /cases/search.

        Args:
            text_search: Texto libre para buscar. Se envía como `searchData`
                         que busca en caseLink, nombre del caso, agentes
                         económicos, y mercados relevantes simultáneamente.
                         Si search_field especifica un campo concreto, se usa
                         ese param en lugar de searchData.
            filters: Filtros con nombres internos (se mapean a query params)
            limit: Máximo de resultados por página
            page: Número de página
            search_field: Campo específico para text_search. Opciones:
                          'searchData' (default, búsqueda libre cross-field),
                          'economicAgents', 'relevantMarkets', 'caseLink',
                          'name'. Si es None o 'auto', usa 'searchData'.
        """
        # Construir query params
        params = {
            "page": page,
            "limit": limit,
        }

        if text_search:
            field = search_field or "searchData"
            if field == "auto":
                field = "searchData"

            if field == "searchData":
                # Búsqueda libre cross-field (caseLink, name,
                # economicAgents, relevantMarkets).
                # Usa ILIKE + unaccent: case-insensitive y sin acentos,
                # pero NO tolerante a typos (no es trigram/fuzzy).
                params["searchData"] = text_search
            elif field in ("caseLink", "economicAgents", "relevantMarkets", "name"):
                params[field] = text_search
            else:
                # Fallback a searchData
                params["searchData"] = text_search

        if filters:
            filter_map = {
                # nombre interno → nombre query param de la API
                "autoridad": "authority",
                "authority": "authority",
                "tipo_procedimiento": "typeOfProcedure",
                "typeOfProcedure": "typeOfProcedure",
                "sentido_resolucion": "senseOfResolution",
                "senseOfResolution": "senseOfResolution",
                "id_expediente": "caseLink",
                "caseLink": "caseLink",
                "agentes_economicos": "economicAgents",
                "economicAgents": "economicAgents",
                "mercados_relevantes": "relevantMarkets",
                "relevantMarkets": "relevantMarkets",
                # Rango de años de resolución
                "fecha_resolucion_desde": "senseOfResolutionFrom",
                "senseOfResolutionFrom": "senseOfResolutionFrom",
                "fecha_resolucion_hasta": "senseOfResolutionTo",
                "senseOfResolutionTo": "senseOfResolutionTo",
            }
            for key, value in filters.items():
                if value is not None:
                    api_key_name = filter_map.get(key, key)
                    params[api_key_name] = value

        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        logger.debug(f"Buscando expedientes: params={params}")

        url = f"{self.base_url}/cases/search"
        if collector is not None:
            collector.record_http_request("GET", url, params=dict(params))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    url,
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            logger.warning("Endpoint de casos no disponible.")
            if collector is not None:
                collector.add_error("estadistica_client", "ConnectError")
            return []
        except httpx.HTTPStatusError as e:
            logger.warning(f"Error en endpoint de casos: {e}")
            if collector is not None:
                collector.add_error("estadistica_client", str(e))
            return []

        # Respuesta paginada: { "data": [...], "meta": {...} }
        items = data.get("data", []) if isinstance(data, dict) else data
        meta = data.get("meta", {}) if isinstance(data, dict) else {}

        if meta:
            logger.debug(
                f"Casos: {meta.get('total', '?')} total, "
                f"página {meta.get('page', '?')}/{meta.get('totalPages', '?')}"
            )

        results = []
        for item in items:
            try:
                results.append(ExpedienteRecord(**item))
            except Exception as e:
                logger.warning(f"Error parseando caso: {e}")
                continue

        # Filtro por prefijo de expediente (VCN, IO, CNT...). searchData es
        # ILIKE cross-field, así que puede colar registros cuyo prefijo no
        # coincide; el filtro local garantiza el universo pedido.
        if prefijo:
            p = prefijo.strip().upper().rstrip("-") + "-"
            results = [r for r in results if (r.caseLink or "").upper().startswith(p)]

        # `meta.total` se guarda en el cliente para que el ejecutor pueda
        # decirle al modelo cuánto universo quedó fuera.
        self.last_total = (meta or {}).get("total")

        # Etapa única de retrieval: /cases/search filtra en SQL y no hay
        # reranking posterior. `meta.total` es lo que permite saber si la
        # respuesta se construyó sobre el universo completo o sobre una página.
        if collector is not None:
            collector.record_stage(
                stage="candidates",
                method="sql_filter:/cases/search",
                docs=list(items),
                notes=(
                    "searchData usa ILIKE + unaccent sobre caseLink, name, "
                    "economicAgents y relevantMarkets; no es fuzzy."
                ),
            )
            collector.record_coverage(
                total_available=meta.get("total") if meta else None,
                requested_limit=limit,
                returned=len(results),
                pages_fetched=1,
                truncation_reason="limit",
            )

        logger.debug(f"Expedientes parseados: {len(results)}")
        return results

    async def fetch_universe(
        self,
        text_search: str | None = None,
        max_results: int = 6000,
        collector=None,
    ) -> tuple[list[ExpedienteRecord], int, bool]:
        """
        Trae el universo COMPLETO, paginando en paralelo.

        Existe porque una agregación sobre una muestra no es una agregación.
        Y la muestra no es siquiera aleatoria: los primeros 1,000 registros del
        acervo son 977 de CFC y 23 de COFECE, así que cortar por arriba sesga
        el resultado de forma sistemática.

        Como la API une sus filtros con OR, cualquier acotación tiene que
        hacerse localmente sobre el universo completo.

        Retorna (registros, total_en_la_base, universo_completo). El tercer
        valor es el que decide si se puede afirmar un máximo o hay que avisar.
        """
        # La API acepta páginas grandes: `limit=5000` devuelve los 4,632
        # expedientes en una sola petición y en menos de 3 segundos. Paginar
        # de a 100 tardaba un minuto y provocaba 500 al pedir en paralelo.
        registros = await self.search(
            text_search=text_search, limit=max_results, page=1,
            collector=collector,
        )
        total = self.last_total or len(registros)

        # Si el tope pedido se quedó corto, se completa paginando en serie.
        if len(registros) < min(total, max_results):
            pagina = 2
            while len(registros) < min(total, max_results):
                lote = await self.search(
                    text_search=text_search, limit=max_results, page=pagina
                )
                if not lote:
                    break
                registros.extend(lote)
                pagina += 1
            self.last_total = total

        return registros, total, len(registros) >= total

    async def fetch_by_prefix(
        self,
        prefijo: str,
        max_results: int = 500,
        collector=None,
    ) -> list[ExpedienteRecord]:
        """
        Trae TODOS los expedientes de un prefijo, con `searchData` como ÚNICO
        filtro de la API.

        Existe por una razón concreta: **la API combina sus filtros con OR, no
        con AND.** Verificado el 14-ago-2026 — `authority=CFC` da 2,793,
        `searchData=VCN-` da 36, y ambos juntos dan 2,829, que es la suma
        exacta. Cualquier filtro adicional *amplía* el resultado.

        Por eso aquí se manda un solo filtro y el resto se aplica localmente.
        Es la única forma de obtener un universo correcto mientras la API no
        intersecte.
        """
        p = prefijo.strip().upper().rstrip("-")
        results = await self.search_all_pages(
            text_search=f"{p}-",
            filters=None,
            max_results=max_results,
            collector=collector,
        )
        return [r for r in results if (r.caseLink or "").upper().startswith(f"{p}-")]

    async def search_all_pages(
        self,
        text_search: str | None = None,
        filters: dict | None = None,
        max_results: int = 200,
        collector=None,
        prefijo: str | None = None,
    ) -> list[ExpedienteRecord]:
        """
        Busca iterando páginas hasta obtener max_results o agotar resultados.
        Es lo que hace falta para responder "todos", "cuántos", "el mayor":
        una sola página no permite sostener ninguna de esas afirmaciones.
        """
        all_results = []
        page = 1
        page_size = min(100, max_results)
        total = None

        while len(all_results) < max_results:
            # El filtro por prefijo NO se aplica por página: reduciría el lote
            # y la paginación lo leería como última página, cortando de más.
            # Se aplica al final, sobre el acumulado.
            batch = await self.search(
                text_search=text_search,
                filters=filters,
                limit=page_size,
                page=page,
                collector=collector if page == 1 else None,
            )
            if total is None:
                total = self.last_total
            if not batch:
                break
            all_results.extend(batch)
            if len(batch) < page_size:
                break  # última página
            page += 1

        if prefijo:
            p = prefijo.strip().upper().rstrip("-") + "-"
            all_results = [
                r for r in all_results if (r.caseLink or "").upper().startswith(p)
            ]

        self.last_total = total
        self.last_pages_fetched = page
        return all_results[:max_results]
