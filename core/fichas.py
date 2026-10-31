"""
Ficha mínima del expediente del que sale cada criterio.

Una búsqueda de criterios no trae fecha, autoridad ni sentido de la
resolución: viven en el registro del expediente. Desde H16 (sep-2026) el agente
le declaraba al modelo que esos campos "no están disponibles", lo cual era
cierto para la búsqueda y falso para el acervo. El costo apareció el 30-oct:
ante una cláusula de no competencia de cinco años, la respuesta dijo que cinco
años era "el tope habitual" sin poder ver que esos precedentes son de 1994-2007
y que los recientes (2012-2020) apuntan a tres. Imanol subrayó justo la frase
"los criterios no traen la autoridad emisora, el sentido de la resolución ni
la fecha".

El registro sí los tiene: medido el 31-oct sobre los expedientes de dos
consultas reales, 87 de 87 traen autoridad y tipo de procedimiento, y 86 de 87
fecha de resolución. Se unen aquí por `caseLink`, de forma determinista, desde
el mismo universo que ya se carga para la historia procesal.

La ficha es corta a propósito: el 22-sep la metadata llegó a sepultar el texto
del criterio (24% del documento serializado).
"""
from __future__ import annotations

from datetime import datetime

# Fecha que decide la época de una resolución, en orden de preferencia. Un acto
# de cumplimiento no trae `resolutionDate`; una sentencia trae la suya.
_CAMPOS_FECHA = (
    "resolutionDate",
    "amparoComplianceResolutionDate",
    "judgmentDate",
    "reviewResolutionDate",
)


def _iso(valor) -> str | None:
    """`DD-MM-YYYY` de la API → `YYYY-MM-DD`. Lo que no se entiende, se omite."""
    if not valor:
        return None
    texto = str(valor).strip()
    for formato in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            continue
    return None


def _valor(registro, campo):
    if isinstance(registro, dict):
        return registro.get(campo)
    return getattr(registro, campo, None)


def ficha_de(registro) -> dict:
    """Lo que de verdad se sabe del expediente; nada se rellena."""
    ficha: dict = {}
    for campo in _CAMPOS_FECHA:
        fecha = _iso(_valor(registro, campo))
        if fecha:
            ficha["fecha_resolucion"] = fecha
            break
    autoridad = _valor(registro, "authority")
    if autoridad:
        ficha["autoridad"] = str(autoridad).strip()
    tipo = _valor(registro, "typeOfProcedure")
    if tipo:
        ficha["tipo_procedimiento"] = str(tipo).strip()
    sentido = _valor(registro, "senseOfResolution")
    if isinstance(sentido, str):
        sentido = [sentido]
    if sentido:
        ficha["sentido_resolucion"] = [str(s).strip() for s in sentido if str(s or "").strip()]
        if not ficha["sentido_resolucion"]:
            del ficha["sentido_resolucion"]
    naturaleza = _valor(registro, "natureOfResolution")
    if naturaleza:
        ficha["naturaleza"] = str(naturaleza).strip()
    return ficha


# Lo que el modelo debe leer como ausente cuando la ficha no lo trae.
CAMPOS_DE_FICHA = {
    "autoridad": "autoridad",
    "sentido_resolucion": "sentido_resolucion",
    "fecha_resolucion": "fecha_resolucion",
}


def faltantes(ficha: dict | None) -> list[str]:
    ficha = ficha or {}
    return [c for c in CAMPOS_DE_FICHA if not ficha.get(c)]


class IndiceDeFichas:
    def __init__(self, fichas: dict[str, dict]):
        self._fichas = fichas

    @classmethod
    def desde_registros(cls, registros) -> "IndiceDeFichas":
        fichas = {}
        for r in registros:
            cl = str(_valor(r, "caseLink") or "").strip()
            if cl:
                fichas[cl] = ficha_de(r)
        return cls(fichas)

    def get(self, case_link: str | None) -> dict | None:
        if not case_link:
            return None
        return self._fichas.get(str(case_link).strip())

    def __len__(self) -> int:
        return len(self._fichas)
