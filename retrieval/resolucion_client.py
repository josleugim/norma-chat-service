"""
Cliente de la resolución digital completa.

`GET /digital-resolution/vector-search` es una búsqueda semántica, pero con
`caseLink` exacto y un `limit` mayor que el número de fragmentos del caso
devuelve **todos** los fragmentos elegibles de esa resolución. Ordenados por
página e `id` quedan en orden de lectura, de la portada a la hoja de firmas
(verificado el 17-oct-2026 sobre CNT-018-2023 y VCN-004-2024: 0 inversiones de
página). El orden *dentro* de una página por `id` está preguntado a José Miguel.

Tres cosas que este cliente no deja pasar en silencio:

- **Respuesta vacía.** La API la regresa igual para un caso sin texto y para uno
  que todavía no tiene embeddings. No se afirma que la resolución no exista:
  `disponible=False` y el motivo.
- **Tope alcanzado.** Si llegan exactamente `LIMITE` fragmentos, el documento
  pudo cortarse. Se marca `posiblemente_incompleta` en vez de presentarlo como
  completo.
- **Fallo HTTP.** Se levanta `BusquedaFallidaError`, como en los demás clientes:
  "la API falló" no es "no hay resolución".
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field

import httpx

from retrieval.estadistica_client import BusquedaFallidaError

logger = logging.getLogger(__name__)

# Muy por encima del caso más largo medido (1,216 fragmentos, VCN-001-2017).
LIMITE = 5000

# `search` es obligatorio en el endpoint y se vectoriza aunque aquí no importe
# el orden por distancia: el orden lo pone el cliente.
_CONSULTA_NEUTRA = "resolución"

TESTADO = "[TESTADO]"
FIRMA = "[firma electrónica]"

_RE_TESTADO = re.compile(r'<p\s+class="crossed-out"\s*>\s*</p>', re.I)
_RE_BR = re.compile(r"<br\s*/?>", re.I)
_RE_FIN_CELDA = re.compile(r"</t[dh]>", re.I)
_RE_FIN_FILA = re.compile(r"</tr>", re.I)
_RE_ETIQUETA = re.compile(r"<[^>]+>")
# Cadenas de firma electrónica / sellos: base64 largo sin espacios.
_RE_BASE64 = re.compile(r"[A-Za-z0-9+/=]{60,}")
_RE_ESPACIOS = re.compile(r"[ \t]+")


def limpiar_fragmento(texto: str) -> str:
    """Texto legible de un fragmento, conservando lo testado como marca."""
    t = texto or ""
    t = _RE_TESTADO.sub(f" {TESTADO} ", t)
    t = _RE_BR.sub("\n", t)
    t = _RE_FIN_CELDA.sub(" | ", t)
    t = _RE_FIN_FILA.sub("\n", t)
    t = _RE_ETIQUETA.sub("", t)
    t = html.unescape(t)
    t = _RE_BASE64.sub(FIRMA, t)
    t = _RE_ESPACIOS.sub(" ", t)
    t = "\n".join(linea.strip() for linea in t.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


@dataclass
class ResolucionDigital:
    case_link: str
    disponible: bool
    paginas: dict[int, str] = field(default_factory=dict)
    fragmentos: int = 0
    posiblemente_incompleta: bool = False
    motivo: str | None = None
    testados: int = 0

    @property
    def numero_paginas(self) -> int:
        return len(self.paginas)

    @property
    def caracteres(self) -> int:
        return sum(len(t) for t in self.paginas.values())

    def texto_con_paginas(self) -> str:
        """El documento con una marca por página, que es lo que se cita."""
        return "\n\n".join(
            f"<<<PÁGINA {p}>>>\n{t}" for p, t in sorted(self.paginas.items())
        )

    def resumen(self) -> dict:
        return {
            "case_link": self.case_link,
            "disponible": self.disponible,
            "fragmentos": self.fragmentos,
            "paginas": self.numero_paginas,
            "caracteres": self.caracteres,
            "testados": self.testados,
            "posiblemente_incompleta": self.posiblemente_incompleta,
            "motivo": self.motivo,
        }


def armar_resolucion(case_link: str, filas: list[dict]) -> ResolucionDigital:
    """Ordena, limpia y agrupa por página. Separado del HTTP para probarlo."""
    if not filas:
        return ResolucionDigital(
            case_link=case_link, disponible=False,
            motivo=("La API no devolvió fragmentos. Puede que la resolución no "
                    "esté digitalizada o que aún no tenga embeddings; la API no "
                    "distingue los dos casos."),
        )
    ajenas = [f for f in filas if f.get("caseLink") not in (None, case_link)]
    if ajenas:
        # El filtro es exacto según la documentación; si deja de serlo, mezclar
        # dos resoluciones sería peor que fallar.
        raise BusquedaFallidaError(
            f"La API devolvió {len(ajenas)} fragmentos de otros expedientes "
            f"al pedir {case_link}.")
    sin_pagina = 10**9
    ordenadas = sorted(filas, key=lambda f: (f.get("page") or sin_pagina, f.get("id") or 0))
    paginas: dict[int, list[str]] = {}
    testados = 0
    for f in ordenadas:
        crudo = f.get("text") or ""
        testados += len(_RE_TESTADO.findall(crudo))
        limpio = limpiar_fragmento(crudo)
        if not limpio:
            continue
        paginas.setdefault(f.get("page") or 0, []).append(limpio)
    return ResolucionDigital(
        case_link=case_link,
        disponible=True,
        paginas={p: "\n".join(ts) for p, ts in paginas.items()},
        fragmentos=len(filas),
        posiblemente_incompleta=len(filas) >= LIMITE,
        motivo=(f"Llegaron {len(filas)} fragmentos, el tope de la consulta: "
                "la resolución pudo quedar cortada.") if len(filas) >= LIMITE else None,
        testados=testados,
    )


class ResolucionDigitalClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def obtener(self, case_link: str) -> ResolucionDigital:
        params = {"search": _CONSULTA_NEUTRA, "caseLink": case_link, "limit": LIMITE}
        url = f"{self.base_url}/digital-resolution/vector-search"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params,
                                        headers={"x-api-key": self.api_key})
                resp.raise_for_status()
                datos = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise BusquedaFallidaError(
                f"No se pudo leer la resolución {case_link}: {e}") from e
        filas = datos.get("data", []) if isinstance(datos, dict) else []
        res = armar_resolucion(case_link, filas)
        logger.info("Resolución %s: %s", case_link, res.resumen())
        return res
