"""
Clasificación de consulta y control de suficiencia de la evidencia.

COFECE, adjudicación de v1.3, punto 3: Norma recupera información
temáticamente cercana que **no responde realmente a la pregunta**, y luego
llena el hueco con inferencias o conocimiento general.

Los dos casos medidos:

- q14 "¿precedentes en el mercado de distribución de medicamentos?" — la
  búsqueda exacta dio cero, se amplió a "farmacéutico", y cinco expedientes
  del sector se presentaron como precedentes de distribución. `relevantMarkets`
  era nulo en los cinco. Además ese campo **no aplica a VCN** por diseño, así
  que inferirlo del nombre de los agentes no tenía respaldo posible.
- q16 "¿qué es el mercado relevante?" — los criterios recuperados hablaban de
  afectación a las atribuciones por omitir notificar. El agente contestó con
  la definición correcta, pero desde conocimiento general marcado como tal.

Lo que pidieron para v1, explícitamente sin reranker ni multiagente:

    routing → evidence check → un retry focalizado → abstención

Este módulo aporta las dos piezas deterministas —clasificar y evaluar
suficiencia—; el retry y la abstención los ejecuta el agente.
"""
import re
from typing import Any

# ── Tipos de consulta (routing) ─────────────────────────────

DETERMINISTIC_TOOL = "deterministic_tool"   # conteos, máximos, plazos
EXHAUSTIVE_QUERY = "exhaustive_query"       # requiere cubrir el universo
SEMANTIC_RETRIEVAL = "semantic_retrieval"   # criterio jurídico
MIXED = "mixed"

_CONTEO_RE = re.compile(r"\bcu[áa]nt[oa]s\b|\bn[úu]mero de\b|\btotal de\b", re.I)
_AGREGADO_RE = re.compile(
    r"\b(mayor|menor|m[áa]xim[oa]|m[íi]nim[oa]|promedio|mediana|suma)\b", re.I
)
_PLAZO_RE = re.compile(r"\b(d[íi]as?\s+h[áa]biles|plazo|tard[óo]|duraci[óo]n)\b", re.I)
_CONCEPTO_RE = re.compile(
    r"\b(qu[ée] es|c[óo]mo (?:se )?(?:define|ha definido)|criterio|criterios|concepto"
    r"|doctrina|precedente|precedentes|argument\w*|ha considerado|interpretaci[óo]n)\b",
    re.I,
)
_LISTA_RE = re.compile(
    r"\b(todos|todas|cu[áa]les|lista completa|listado|nunca|ning[úu]n)\b", re.I
)


def classify(query: str) -> dict[str, Any]:
    """
    Clasifica la consulta y propone la estrategia. Heurístico y así se marca:
    dice qué esperaríamos, no qué decidió el agente.
    """
    q = query or ""
    señales = {
        "conteo": bool(_CONTEO_RE.search(q)),
        "agregado": bool(_AGREGADO_RE.search(q)),
        "plazo": bool(_PLAZO_RE.search(q)),
        "concepto": bool(_CONCEPTO_RE.search(q)),
        "lista_universo": bool(_LISTA_RE.search(q)),
    }

    # Un expediente concreto acota el universo y quita el riesgo de cobertura.
    sobre_un_expediente = bool(re.search(r"\b[A-Z]{2,5}-\d{3}-\d{4}\b", q))
    semantica = señales["concepto"]

    # Un superlativo o un promedio sobre el acervo exige recorrerlo entero: el
    # riesgo no es el cálculo, es responderlo sobre una muestra. Un conteo, en
    # cambio, se resuelve con el total exacto sin traer documentos.
    if señales["agregado"] and not sobre_un_expediente:
        tipo = MIXED if semantica else EXHAUSTIVE_QUERY
    elif señales["lista_universo"]:
        tipo = EXHAUSTIVE_QUERY
    elif señales["conteo"] or (señales["plazo"] and sobre_un_expediente):
        tipo = MIXED if semantica else DETERMINISTIC_TOOL
    elif señales["plazo"]:
        tipo = EXHAUSTIVE_QUERY if not semantica else MIXED
    elif semantica:
        tipo = SEMANTIC_RETRIEVAL
    else:
        tipo = MIXED

    estrategias = {
        DETERMINISTIC_TOOL: "herramienta determinista (contar/agregar/plazos)",
        EXHAUSTIVE_QUERY: "recorrer el universo completo y agregar sin muestreo",
        SEMANTIC_RETRIEVAL: "retrieval semántico de criterios",
        MIXED: "combinar herramientas estructuradas y retrieval semántico",
    }
    return {
        "query_type": tipo,
        "strategy": estrategias[tipo],
        "signals": señales,
        "provenance": "heuristic",
    }


# ── Control de suficiencia ──────────────────────────────────

_VACIAS = {
    "que", "cual", "cuales", "como", "para", "por", "los", "las", "del", "una",
    "unos", "unas", "sobre", "entre", "ha", "han", "the", "cofece", "norma",
    "expediente", "expedientes", "resolucion", "resoluciones",
}


def check_sufficiency(
    query: str,
    docs: list[dict],
    query_type: str,
    min_solape: float = 0.34,
) -> dict[str, Any]:
    """
    ¿La evidencia recuperada permite contestar **exactamente** lo preguntado?

    Deliberadamente simple y explicable: mide qué proporción de los términos
    sustantivos de la pregunta aparece en la evidencia. No pretende entender;
    pretende detectar el caso en que se recuperó algo de otro tema y nadie se
    dio cuenta.

    Devuelve `sufficient` y, cuando no lo es, los términos que no aparecen —
    que son justo los que debería usar una segunda búsqueda focalizada.
    """
    terminos = _terminos(query)
    if not terminos:
        return {"sufficient": True, "reason": "sin términos evaluables",
                "provenance": "heuristic"}

    if not docs:
        return {
            "sufficient": False,
            "reason": "El retrieval no devolvió ningún documento.",
            "missing_terms": sorted(terminos),
            "coverage": 0.0,
            "provenance": "heuristic",
        }

    texto = " ".join(_texto_de(d) for d in docs).lower()
    presentes = {t for t in terminos if t[:6] in texto}
    faltantes = terminos - presentes
    cobertura = len(presentes) / len(terminos)

    suficiente = cobertura >= min_solape
    resultado = {
        "sufficient": suficiente,
        "coverage": round(cobertura, 2),
        "terms_total": len(terminos),
        "terms_found": len(presentes),
        "missing_terms": sorted(faltantes),
        "docs_evaluated": len(docs),
        "provenance": "heuristic",
    }
    if not suficiente:
        resultado["reason"] = (
            f"La evidencia recuperada solo cubre {int(cobertura * 100)}% de los "
            f"términos de la pregunta. Falta: {', '.join(sorted(faltantes)[:6])}."
        )
    return resultado


def _terminos(query: str) -> set[str]:
    import unicodedata
    s = (query or "").lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    palabras = re.findall(r"[a-z]{4,}", s)
    return {p for p in palabras if p not in _VACIAS}


def _texto_de(doc: dict) -> str:
    if not isinstance(doc, dict):
        return ""
    partes = [str(doc.get("text") or doc.get("content") or "")]
    for campo in ("name", "relevantMarkets", "economicAgents", "senseOfResolution"):
        valor = doc.get(campo)
        if valor:
            partes.append(str(valor))
    meta = doc.get("metadata")
    if isinstance(meta, dict):
        for campo in ("title", "anchor", "context", "titleNames"):
            if meta.get(campo):
                partes.append(str(meta[campo]))
    import unicodedata
    texto = " ".join(partes).lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
