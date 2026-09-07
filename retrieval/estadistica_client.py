"""
Cliente HTTP para búsqueda de expedientes/casos.

    GET {base}/cases/agent-search

Sustituye a `/cases/search`, que desde el 7-sep-2026 responde 401 con nuestra
llave. Tres diferencias del endpoint nuevo cambian el diseño del cliente:

1. **Los filtros se combinan con AND.** Verificado el 7-sep-2026:
   `authority=COFECE` → 1,842, `caseLink=VCN` → 46, ambos juntos → 39, no
   1,888. Se acabó el parche de mandar un solo filtro y acotar localmente.

2. **No existe `page`.** La paginación desapareció. En cambio `limit` no tiene
   tope: `limit=5000` devuelve los 4,662 expedientes en ~3 s y 2.1 MB.

3. **No existe `meta.total`.** `meta` trae solo `returned` y `limit`, así que
   el truncamiento se infiere de `returned == limit`. Es una estimación, no un
   dato: si el universo mide exactamente `limit`, se ve truncado sin estarlo.
   Por eso `last_total` solo se llena cuando la respuesta es demostrablemente
   completa, y queda en None cuando pudo haberse cortado.

Y una trampa que obliga a validar de este lado: **los nombres de parámetro mal
escritos se ignoran en silencio.** Verificado: `?aplicableLaw=...` (con una
sola `p`) devuelve `200 OK` con los 4,662 registros, indistinguible de no
filtrar. El código anterior hacía `filter_map.get(key, key)`, así que cualquier
nombre que inventara el modelo se reenviaba tal cual y la API lo descartaba sin
avisar: el agente creía haber filtrado y respondía sobre el corpus completo.
Ahora un filtro desconocido levanta `FiltroDesconocidoError`.
"""
import logging
import httpx
from models.schemas import ExpedienteRecord

logger = logging.getLogger(__name__)


class FiltroDesconocidoError(ValueError):
    """
    Un filtro que la API no conoce. Se levanta en vez de reenviarlo porque la
    API ignora los parámetros desconocidos con 200 OK, y una respuesta sin
    filtrar es indistinguible de una filtrada. El despachador de herramientas
    convierte esto en un error visible para el modelo y para la traza.
    """


# Nombres de query param que el endpoint reconoce de verdad. Todo lo que no
# esté aquí se descarta en silencio del lado del servidor.
PARAMS_API = frozenset({
    "name", "caseLink", "applicableLaw", "economicAgents", "relevantMarkets",
    "searchData", "authority", "typeOfProcedure", "senseOfResolution",
    "senseOfResolutionFrom", "senseOfResolutionTo", "agentFines", "limit",
})

# Nombre interno del agente → query param de la API.
FILTER_MAP = {
    "autoridad": "authority",
    "authority": "authority",
    "tipo_procedimiento": "typeOfProcedure",
    "typeOfProcedure": "typeOfProcedure",
    "id_expediente": "caseLink",
    "caseLink": "caseLink",
    "agentes_economicos": "economicAgents",
    "economicAgents": "economicAgents",
    "mercados_relevantes": "relevantMarkets",
    "relevantMarkets": "relevantMarkets",
    "name": "name",
    "searchData": "searchData",
    "applicableLaw": "applicableLaw",
    # Años de resolución. Ojo con el nombre: filtran sobre `resolutionDate`,
    # no sobre el sentido. La API exige que vayan los dos o ninguno.
    "fecha_resolucion_desde": "senseOfResolutionFrom",
    "senseOfResolutionFrom": "senseOfResolutionFrom",
    "fecha_resolucion_hasta": "senseOfResolutionTo",
    "senseOfResolutionTo": "senseOfResolutionTo",
    # Triestado, ahora resuelto por el servidor: true → 191, false → 4,471,
    # y suman el universo exacto.
    "has_multas": "agentFines",
    "agentFines": "agentFines",
}

# Filtros que NO se mandan a la API aunque ella los acepte.
#
# `senseOfResolution` hace match de valor completo y su alias está roto: pedir
# SANCION expande a `SANCION`, `SANCION/ACREDITACION DEL INCUMPLIMIENTO` y
# `ACREDITACION DEL INCUMPLIMIENTO`, pero el valor dominante en los datos es
# `Sanciona`, con 35 de los 37 sancionados. La consulta devuelve 2 de 37 con
# 200 OK. Mientras JM no normalice el vocabulario, el sentido se filtra
# localmente con el matcher tolerante a negaciones, que sí distingue
# "NO SE ACREDITÓ INCUMPLIMIENTO" de "SANCIÓN/ACREDITACIÓN".
FILTROS_SOLO_LOCALES = frozenset({"sentido_resolucion", "senseOfResolution"})

# Campos válidos para dirigir `text_search` a una columna concreta.
CAMPOS_TEXTO = frozenset({
    "searchData", "caseLink", "economicAgents", "relevantMarkets", "name",
})

# El universo completo cabe en una petición; este es el techo por defecto.
LIMIT_UNIVERSO = 5000


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

        # Total EXACTO del universo que cumple los filtros, o None si la
        # respuesta pudo quedar truncada. Sin `meta.total` no hay forma de
        # saberlo cuando `returned == limit`, y afirmar cobertura completa a
        # partir de una estimación es justo el error que perseguimos.
        self.last_total: int | None = None
        self.last_returned: int = 0
        self.last_limit: int | None = None
        self.last_truncado: bool = False
        self.last_pages_fetched: int = 1

    # ── Construcción de la petición ──────────────────────────────────

    def _build_params(
        self,
        text_search: str | None,
        filters: dict | None,
        limit: int,
        search_field: str | None,
    ) -> dict:
        params: dict = {"limit": limit}

        if text_search:
            campo = search_field or "searchData"
            if campo == "auto" or campo not in CAMPOS_TEXTO:
                campo = "searchData"
            params[campo] = text_search

        for key, value in (filters or {}).items():
            if value is None:
                continue
            if key in FILTROS_SOLO_LOCALES:
                continue
            nombre = FILTER_MAP.get(key)
            if nombre is None:
                raise FiltroDesconocidoError(
                    f"Filtro '{key}' no existe en la API. La API ignora los "
                    f"parámetros desconocidos y devuelve resultados SIN "
                    f"filtrar, así que se rechaza aquí. Filtros válidos: "
                    f"{', '.join(sorted(FILTER_MAP))}."
                )
            if nombre == "agentFines":
                # La API exige exactamente 'true' o 'false'; cualquier otra
                # cosa es 400. El triestado se respeta: None ya se saltó.
                params[nombre] = "true" if bool(value) else "false"
            else:
                params[nombre] = value

        # Los años de resolución van juntos o no van: uno solo es 400.
        desde = params.get("senseOfResolutionFrom")
        hasta = params.get("senseOfResolutionTo")
        if (desde is None) != (hasta is None):
            faltante = (
                "fecha_resolucion_hasta" if desde is not None
                else "fecha_resolucion_desde"
            )
            raise FiltroDesconocidoError(
                f"El rango de años de resolución necesita los dos extremos; "
                f"falta '{faltante}'. La API responde 400 si va uno solo."
            )

        desconocidos = set(params) - PARAMS_API
        if desconocidos:
            raise FiltroDesconocidoError(
                f"Parámetros que la API no reconoce y descartaría en "
                f"silencio: {sorted(desconocidos)}."
            )
        return params

    # ── Búsqueda ─────────────────────────────────────────────────────

    async def search(
        self,
        text_search: str | None = None,
        filters: dict | None = None,
        limit: int = 50,
        search_field: str | None = None,
        collector=None,
        prefijo: str | None = None,
    ) -> list[ExpedienteRecord]:
        """
        Búsqueda de expedientes vía `GET /cases/agent-search`.

        Args:
            text_search: Texto libre. Se manda como `searchData`, que busca a
                         la vez en caseLink, name, economicAgents y
                         relevantMarkets. Es ILIKE + unaccent: insensible a
                         mayúsculas y acentos, pero NO tolerante a typos.
            filters:     Filtros con nombres internos. Un nombre no
                         reconocido levanta FiltroDesconocidoError.
            limit:       Tope de filas. No hay paginación: para el universo
                         completo se pide LIMIT_UNIVERSO de una vez.
            search_field: Columna a la que dirigir `text_search`.
            prefijo:     Guarda local de prefijo de expediente (VCN, IO...).
        """
        params = self._build_params(text_search, filters, limit, search_field)

        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        logger.debug(f"Buscando expedientes: params={params}")

        url = f"{self.base_url}/cases/agent-search"
        if collector is not None:
            collector.record_http_request("GET", url, params=dict(params))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            logger.warning("Endpoint de casos no disponible.")
            if collector is not None:
                collector.add_error("estadistica_client", "ConnectError")
            self._reset_cobertura()
            return []
        except httpx.HTTPStatusError as e:
            logger.warning(f"Error en endpoint de casos: {e}")
            if collector is not None:
                collector.add_error("estadistica_client",
                                    f"{type(e).__name__}: {e}")
            self._reset_cobertura()
            return []

        items = data.get("data", []) if isinstance(data, dict) else data
        meta = data.get("meta", {}) if isinstance(data, dict) else {}

        results = []
        for item in items:
            try:
                results.append(ExpedienteRecord(**item))
            except Exception as e:
                logger.warning(f"Error parseando caso: {e}")
                continue

        # Guarda de prefijo. `searchData` es cross-field, así que puede colar
        # registros cuyo caseLink no empieza con el prefijo pedido.
        if prefijo:
            p = prefijo.strip().upper().rstrip("-") + "-"
            results = [r for r in results if (r.caseLink or "").upper().startswith(p)]

        devueltos = int(meta.get("returned", len(items)) or len(items))
        tope = int(meta.get("limit", limit) or limit)
        self.last_returned = devueltos
        self.last_limit = tope
        # Sin `meta.total`, la igualdad con el tope es la única señal de corte.
        self.last_truncado = devueltos >= tope
        # Solo se afirma un total cuando la API demostró haber devuelto todo
        # lo que cumple los filtros: `returned < limit`.
        self.last_total = None if self.last_truncado else devueltos
        self.last_pages_fetched = 1

        if collector is not None:
            collector.record_stage(
                stage="candidates",
                method="sql_filter:/cases/agent-search",
                docs=list(items),
                notes=(
                    "Filtros combinados con AND por el servidor. searchData "
                    "usa ILIKE + unaccent sobre caseLink, name, "
                    "economicAgents y relevantMarkets; no es fuzzy. La API no "
                    "expone meta.total, así que el truncamiento se infiere de "
                    "returned==limit y es una estimación: un universo que mida "
                    "exactamente el tope se marca truncado sin estarlo."
                ),
            )
            collector.record_coverage(
                total_available=self.last_total,
                requested_limit=limit,
                returned=len(results),
                pages_fetched=1,
                truncated=self.last_truncado,
                truncation_reason="returned==limit",
            )

        logger.debug(
            f"Expedientes: {len(results)} parseados, returned={devueltos}, "
            f"limit={tope}, truncado={self.last_truncado}"
        )
        return results

    def _reset_cobertura(self) -> None:
        self.last_total = None
        self.last_returned = 0
        self.last_limit = None
        self.last_truncado = False
        self.last_pages_fetched = 0

    # ── Universo completo ────────────────────────────────────────────

    async def fetch_universe(
        self,
        text_search: str | None = None,
        filters: dict | None = None,
        max_results: int = LIMIT_UNIVERSO,
        collector=None,
    ) -> tuple[list[ExpedienteRecord], int, bool]:
        """
        Trae el universo COMPLETO en una sola petición.

        Existe porque una agregación sobre una muestra no es una agregación, y
        la muestra ni siquiera es aleatoria: los primeros 1,000 registros del
        acervo son 977 de CFC y 23 de COFECE, así que cortar por arriba sesga
        el resultado de forma sistemática.

        Retorna (registros, total, universo_completo). El tercer valor es el
        que decide si se puede afirmar un máximo o hay que advertir: con la
        API nueva se sostiene en `returned < limit`, no en un `meta.total`.
        """
        registros = await self.search(
            text_search=text_search,
            filters=filters,
            limit=max_results,
            collector=collector,
        )
        completo = not self.last_truncado
        total = self.last_total if completo else len(registros)
        return registros, total, completo

    async def fetch_by_prefix(
        self,
        prefijo: str,
        filters: dict | None = None,
        max_results: int = LIMIT_UNIVERSO,
        collector=None,
    ) -> list[ExpedienteRecord]:
        """
        Trae TODOS los expedientes de un prefijo (VCN, IO, CNT...).

        Antes esto mandaba un solo filtro y acotaba localmente, porque la API
        vieja unía sus filtros con OR y cualquier filtro extra *ampliaba* el
        resultado. Con `agent-search` eso ya no aplica: `caseLink` hace match
        parcial y se combina con AND, así que el prefijo viaja como filtro y
        el resto se puede acompañar sin inflar nada.
        """
        p = prefijo.strip().upper().rstrip("-")
        combinados = dict(filters or {})
        combinados["caseLink"] = f"{p}-"
        return await self.search(
            filters=combinados,
            limit=max_results,
            collector=collector,
            prefijo=p,
        )

    async def search_all_pages(
        self,
        text_search: str | None = None,
        filters: dict | None = None,
        max_results: int = LIMIT_UNIVERSO,
        collector=None,
        prefijo: str | None = None,
    ) -> list[ExpedienteRecord]:
        """
        Búsqueda exhaustiva. Conserva el nombre por los llamadores, pero ya no
        pagina: `agent-search` no tiene parámetro `page`.

        Ojo con por qué importa. Como la API **ignora en silencio** los
        parámetros que no conoce, el bucle anterior mandaba `page=2`, recibía
        otra vez la primera página y la concatenaba: mismos registros
        duplicados hasta llenar `max_results`, sin un solo error. Una sola
        petición con `limit` alto es además más rápida: 4,662 registros en
        ~3 s contra el minuto que tardaba paginando de a 100.
        """
        results = await self.search(
            text_search=text_search,
            filters=filters,
            limit=max_results,
            collector=collector,
            prefijo=prefijo,
        )
        return results[:max_results]
