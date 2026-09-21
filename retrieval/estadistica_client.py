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

3. **`meta.total` volvió el 9-sep-2026**, a petición nuestra. Entre el 7 y el 9
   el endpoint solo traía `returned` y `limit`, y el truncamiento había que
   inferirlo de `returned == limit` — una estimación, no un dato. Ahora se lee
   el total real. El camino de respaldo sigue ahí por si desaparece otra vez, y
   la traza registra en `truncation_reason` cuál de los dos se usó.

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


class BusquedaFallidaError(RuntimeError):
    """
    La búsqueda no se completó. Se levanta en vez de devolver una lista vacía
    porque "la API falló" y "no hay resultados" son cosas distintas, y
    confundirlas hace que el agente afirme que un expediente no existe.
    """


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

# A partir de este tope, la petición trae el acervo entero y necesita otro
# timeout. No es un detalle de afinación: cuando José Miguel repuso los seis
# campos que faltaban, la respuesta pasó de 2.1 MB a 7.6 MB y de ~3 s a 7-18 s,
# con el arranque en frío en el extremo alto. Con el timeout de 10 s que
# traíamos, el censo del acervo empezó a fallar por ReadTimeout.
LIMIT_PETICION_GRANDE = 1000
TIMEOUT_PETICION_GRANDE = 120.0


class EstadisticaSearchClient:

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 15.0,
        timeout_grande: float = TIMEOUT_PETICION_GRANDE,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # Traer el universo completo es otra clase de petición que buscar diez
        # expedientes; medirlas con el mismo reloj hacía fallar la primera.
        self.timeout_grande = timeout_grande

        # Total de coincidencias que cumplen los filtros, leído de
        # `meta.total`. Queda en None solo si la API dejara de mandarlo y la
        # respuesta pudo cortarse: afirmar cobertura completa a partir de una
        # estimación es justo el error que perseguimos.
        self.last_total: int | None = None
        self.last_returned: int = 0
        self.last_limit: int | None = None
        self.last_truncado: bool = False
        self.last_pages_fetched: int = 1

        # Universo restringido (paso 01 del holdout). Cuando está puesto, todo
        # lo que sale de este cliente queda acotado a esa lista cerrada, y la
        # restricción se registra como un paso más del linaje de filtros.
        # Va aquí y no en el agente a propósito: es el único punto por el que
        # pasan search, fetch_universe y fetch_by_prefix, así que ninguna ruta
        # puede saltárselo por olvido.
        self.universo = None
        self.ultimo_descartados_universo: int = 0

        # Último payload tal como llegó, antes de parsear. Existe para que el
        # censo pueda comparar los campos que manda la API contra los que el
        # modelo declara: un campo no declarado se descarta en silencio y el
        # agente lo reporta como inexistente.
        self.ultimo_payload_crudo: list = []

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
        # Con universo restringido el tope deja de tener sentido como tope.
        #
        # Pedir 50 y quedarnos con los que caen en el universo mezcla dos
        # cortes distintos: el de la API sobre el acervo y el nuestro sobre la
        # lista. El resultado no permite afirmar ni cobertura ni truncamiento
        # del universo, porque el corte ocurrió sobre otra población.
        #
        # Se pide el conjunto completo que cumple los filtros —la API los
        # aplica igual, sólo sube el tope— y el recorte al universo pasa a ser
        # el único corte. Así la cobertura vuelve a ser un hecho. Cuesta una
        # respuesta más grande; cuesta menos que una exhaustividad falsa.
        if self.universo is not None:
            limit = max(limit, LIMIT_UNIVERSO)

        params = self._build_params(text_search, filters, limit, search_field)

        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        logger.debug(f"Buscando expedientes: params={params}")

        url = f"{self.base_url}/cases/agent-search"
        if collector is not None:
            collector.record_http_request("GET", url, params=dict(params))

        espera = (
            self.timeout_grande if limit >= LIMIT_PETICION_GRANDE
            else self.timeout
        )
        try:
            async with httpx.AsyncClient(timeout=espera) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            # Un fallo de la búsqueda NO es "no hay resultados".
            #
            # Antes esto devolvía `[]` y el agente concluía que el expediente
            # no existía. Visto en producción el 9-sep-2026: `/cases/search`
            # empezó a responder 401, el cliente lo tragó, y el chat contestó
            # *"es posible que el expediente no exista"* sobre IO-001-2019, un
            # caso real con multas por más de 15 millones que el propio
            # explorador de normaplus.ai muestra sin problema.
            #
            # Afirmar inexistencia a partir de una falla de infraestructura es
            # el peor error que puede cometer este agente, y es el que COFECE
            # nos marcó desde la primera ronda. Ahora se levanta la excepción:
            # el despachador de herramientas la convierte en un error visible
            # para el modelo y para la traza, y el agente dice que la búsqueda
            # falló en vez de inventar un vacío.
            detalle = f"{type(e).__name__}: {e}".rstrip(": ")
            logger.error(f"Búsqueda de expedientes fallida: {detalle}")
            if collector is not None:
                collector.add_error("estadistica_client", detalle)
            self._reset_cobertura()
            raise BusquedaFallidaError(
                f"La búsqueda de expedientes falló ({detalle}). NO se puede "
                f"concluir que no existan resultados: la consulta nunca se "
                f"completó."
            ) from e

        items = data.get("data", []) if isinstance(data, dict) else data
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        self.ultimo_payload_crudo = items if isinstance(items, list) else []

        results = []
        for item in items:
            try:
                results.append(ExpedienteRecord(**item))
            except Exception as e:
                logger.warning(f"Error parseando caso: {e}")
                continue

        # Universo restringido. Va ANTES de la guarda de prefijo y antes de
        # leer la cobertura: `meta.total` describe el acervo entero de la API,
        # no nuestro universo, así que usarlo como denominador después de
        # recortar produciría exactamente la falsa exhaustividad que este
        # proyecto persigue.
        self.ultimo_descartados_universo = 0
        if self.universo is not None:
            antes = len(results)
            results = self.universo.filtrar(results)
            self.ultimo_descartados_universo = antes - len(results)
            if self.ultimo_descartados_universo:
                logger.debug(
                    f"Universo restringido: {antes} → {len(results)} "
                    f"(descartados {self.ultimo_descartados_universo})"
                )

        # Guarda de prefijo. `searchData` es cross-field, así que puede colar
        # registros cuyo caseLink no empieza con el prefijo pedido.
        if prefijo:
            p = prefijo.strip().upper().rstrip("-") + "-"
            results = [r for r in results if (r.caseLink or "").upper().startswith(p)]

        devueltos = int(meta.get("returned", len(items)) or len(items))
        tope = int(meta.get("limit", limit) or limit)
        self.last_returned = devueltos
        self.last_limit = tope
        self.last_pages_fetched = 1

        # `meta.total` volvió el 9-sep-2026, a petición nuestra. Es el número
        # de coincidencias reales, independiente del tope, así que el
        # truncamiento deja de ser una inferencia y pasa a ser un hecho: se
        # sabe cuánto quedó fuera, no solo que *pudo* quedar algo.
        total = meta.get("total")
        if total is not None:
            self.last_total = int(total)
            self.last_truncado = devueltos < self.last_total
        else:
            # Respaldo por si la API vuelve a dejar de mandarlo: `returned ==
            # limit` es lo único que queda, y es una estimación. No se afirma
            # un total que no se puede sostener.
            self.last_truncado = devueltos >= tope
            self.last_total = None if self.last_truncado else devueltos
            logger.warning(
                "La API no devolvió meta.total; el truncamiento vuelve a ser "
                "una estimación basada en returned==limit."
            )

        # Con universo restringido, el total de la API mide otra cosa.
        #
        # `meta.total` cuenta las coincidencias en el acervo completo (4,697).
        # Si pedimos 50, la API devuelve 50 de 1,800 y nosotros nos quedamos
        # con los 3 que están en el universo, el truncamiento REAL de nuestro
        # universo no se puede deducir de esos números: el corte ocurrió sobre
        # una población que no es la nuestra.
        #
        # Afirmar cobertura completa aquí sería la falsa exhaustividad de
        # siempre, y afirmar truncamiento sería una alarma falsa. Así que el
        # total queda en None —desconocido— salvo que la respuesta venga
        # demostrablemente completa desde el servidor, único caso en que sí
        # vimos todo el acervo y por tanto todo nuestro universo.
        if self.universo is not None:
            acervo_completo = (
                total is not None and devueltos >= int(total)
            )
            if acervo_completo:
                self.last_total = len(results)
                self.last_truncado = False
            else:
                self.last_total = None
                self.last_truncado = True

        if collector is not None:
            collector.record_stage(
                stage="candidates",
                method="sql_filter:/cases/agent-search",
                docs=list(items),
                notes=(
                    "Filtros combinados con AND por el servidor. searchData "
                    "usa ILIKE + unaccent sobre caseLink, name, "
                    "economicAgents y relevantMarkets; no es fuzzy. El "
                    "truncamiento sale de meta.total, así que es un hecho y no "
                    "una inferencia; si la API dejara de mandarlo se degrada a "
                    "returned==limit y la traza lo dice en truncation_reason."
                ),
            )
            collector.record_coverage(
                total_available=self.last_total,
                requested_limit=limit,
                returned=len(results),
                pages_fetched=1,
                truncated=self.last_truncado,
                truncation_reason=(
                    "meta.total" if meta.get("total") is not None
                    else "returned==limit"
                ),
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
        que decide si se puede afirmar un máximo o hay que advertir, y desde
        que volvió `meta.total` se sostiene en el total real.
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
