"""
Censo del acervo al inicio de cada corrida.

Por qué existe: el acervo se está cargando mientras probamos, así que el
universo es un blanco móvil. Si el corpus crece entre dos corridas, las
preguntas de exhaustividad dejan de ser comparables y no hay forma de saber si
una mejora vino de un fix nuestro o de documentos nuevos.

Eso lo resolvería `index_version` del lado de la API, que hoy no se expone
(ver docs/solicitud-jose-miguel.md). Mientras tanto, cada corrida se toma su
propia foto del universo con `meta.total` de /cases/search y la guarda en el
manifiesto. Con eso, comparar dos corridas es comparar también sus censos: si
difieren, lo sabemos antes de sacar conclusiones.

Es barato —una consulta por prefijo, `limit=1`— y se hace una sola vez por
corrida.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Prefijos que importan para las preguntas de exhaustividad de la batería.
PREFIXES = ("VCN", "IO", "CNT", "DE", "RA", "CON")


async def take_census(estadistica_client, prefixes: tuple[str, ...] = PREFIXES) -> dict:
    """
    Cuenta expedientes por prefijo vía meta.total. Nunca lanza: si algo falla,
    devuelve lo que alcanzó a medir y lo deja anotado.
    """
    census: dict = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "source": "meta.total de /cases/search",
        "by_prefix": {},
        "total": None,
        "errors": [],
    }

    # En serie y con reintento. La API devuelve 500 ante peticiones
    # concurrentes: el censo en paralelo perdía VCN y CON, y un censo
    # incompleto es peor que uno lento, porque se usa para decidir si dos
    # corridas son comparables.
    import asyncio

    async def contar(etiqueta: str, search: str | None) -> None:
        for intento in range(3):
            try:
                valor = await _count(estadistica_client, search)
                if etiqueta == "total":
                    census["total"] = valor
                else:
                    census["by_prefix"][etiqueta] = valor
                return
            except Exception as e:
                if intento == 2:
                    census["errors"].append(f"{etiqueta}: {e}")
                else:
                    await asyncio.sleep(0.5 * (intento + 1))

    await contar("total", None)
    for prefijo in prefixes:
        await contar(prefijo, f"{prefijo}-")

    census["completo"] = not census["errors"]
    return census


async def _count(client, search: str | None) -> int | None:
    """
    Pide una sola fila y lee meta.total. El cliente no expone `meta`, así que
    se replica la llamada mínima con su misma configuración.
    """
    import httpx

    params = {"page": 1, "limit": 1}
    if search:
        params["searchData"] = search

    headers = {"x-api-key": client.api_key} if client.api_key else {}
    async with httpx.AsyncClient(timeout=client.timeout) as http:
        resp = await http.get(
            f"{client.base_url}/cases/search", params=params, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, dict):
        return (data.get("meta") or {}).get("total")
    return None


def diff_census(before: dict, after: dict) -> list[dict]:
    """Diferencias entre el censo de dos corridas. Vacío = corpus estable."""
    if not before or not after:
        return []

    changes = []
    if before.get("total") != after.get("total"):
        changes.append({
            "field": "total",
            "before": before.get("total"),
            "after": after.get("total"),
        })

    keys = set(before.get("by_prefix", {})) | set(after.get("by_prefix", {}))
    for key in sorted(keys):
        b = before.get("by_prefix", {}).get(key)
        a = after.get("by_prefix", {}).get(key)
        if b != a:
            changes.append({"field": f"prefix:{key}", "before": b, "after": a})
    return changes
