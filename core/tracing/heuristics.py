"""
Interpretación heurística de la consulta.

IMPORTANTE: esto NO es lo que el agente decidió. El agente hoy no tiene un paso
de interpretación: recibe la pregunta y llama herramientas directamente. Todo lo
que sale de aquí va marcado con provenance="heuristic" para que nadie lo confunda
con una decisión del agente.

Cuando se implemente el mecanismo de revisión de la pregunta (comentario #7 de
Imanol, v1.1), esos campos pasarán a provenance="agent" y este módulo quedará
solo como respaldo comparativo.
"""
import re

from core.tracing.schema import Constraints, Interpretation, Scope

# Prefijos de expediente del acervo de competencia económica.
# VCN y CNT son los del reporte de Imanol; IO son los que él señaló faltantes.
#
# Ojo con los prefijos que colisionan con palabras del español (DE, CON, LI,
# AD, RA): buscarlos sueltos daría falsos positivos — "los VCN DE Cofece"
# marcaría scope=DE. Solo cuentan cuando aparecen dentro de un número de
# expediente completo, vía CASE_LINK_RE. Los inequívocos sí se buscan sueltos,
# y en mayúsculas, que es como se escriben.
PREFIX_BARE_RE = re.compile(r"\b(VCN|CNT|IO|UO|IEBC|IEED)\b")
CASE_LINK_RE = re.compile(r"\b[A-Z]{2,5}-\d{3}-\d{4}\b")

# Disparadores de consulta exhaustiva.
#
# La lista de COFECE es explícita: "todos, cuántos, mayor, menor, promedio,
# nunca, cuáles deberían permitir identificar que no basta necesariamente un
# top-k semántico normal". Los superlativos y los agregados entran aquí aunque
# también se clasifiquen aparte: preguntar por el MAYOR de algo exige haber
# recorrido el universo completo, igual que preguntar por TODOS.
EXHAUSTIVE_RE = re.compile(
    r"\b(todos|todas|cu[áa]nt[oa]s|cu[áa]les|listado|lista completa|completo|completa"
    r"|exhaustiv[oa]|la totalidad|en total|cada uno|universo"
    r"|mayor|menor|m[áa]xim[oa]|m[íi]nim[oa]|promedio|media|mediana"
    r"|nunca|jam[áa]s|ning[úu]n|siempre)\b",
    re.IGNORECASE,
)
SUPERLATIVE_PATTERNS = [
    ("maximo", r"\b(m[áa]ximo|mayor|m[áa]s (?:largo|alto|tardado|grande)|el que m[áa]s)\b"),
    ("minimo", r"\b(m[íi]nimo|menor|m[áa]s (?:corto|bajo|r[áa]pido|peque[ñn]o)|el que menos)\b"),
    ("ultimo", r"\b([úu]ltimo|m[áa]s reciente|reciente)\b"),
    ("primero", r"\b(primer[oa]?|m[áa]s antigu[oa])\b"),
]
COMPUTATION_RE = re.compile(
    r"\b(plazo|plazos|d[íi]as?\s+h[áa]biles|d[íi]as?|tard[óo]|tardaron|duraci[óo]n"
    r"|c[óo]mputo|promedio|mediana|cu[áa]nto tiempo|plazo legal)\b",
    re.IGNORECASE,
)
COMPARATIVE_RE = re.compile(
    r"\b(compar[ae]|comparaci[óo]n|versus|vs\.?|diferencia entre|frente a)\b",
    re.IGNORECASE,
)
AUTHORITY_PATTERNS = [
    ("COFECE", r"\bcofece\b"),
    ("CNA", r"\b(cna|comisi[óo]n nacional antimonopolio)\b"),
    ("CFC", r"\bcfc\b"),
]
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def interpret(query: str) -> Interpretation:
    """Deriva scope y constraints de la pregunta, con reglas explícitas."""
    q = query or ""

    case_links = sorted(set(CASE_LINK_RE.findall(q)))
    # Prefijos: los inequívocos escritos sueltos, más los que se deducen de
    # los expedientes citados (ahí sí valen todos, incluidos los ambiguos).
    prefixes = set(PREFIX_BARE_RE.findall(q))
    prefixes.update(cl.split("-")[0] for cl in case_links)

    authority = None
    for name, pattern in AUTHORITY_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            authority = name
            break

    date_from = date_to = None
    raw_years = sorted({m.group(0) for m in YEAR_RE.finditer(q)})
    if raw_years:
        date_from = raw_years[0]
        date_to = raw_years[-1] if len(raw_years) > 1 else None

    superlative = None
    for name, pattern in SUPERLATIVE_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            superlative = name
            break

    scope = Scope(
        procedure_prefix=sorted(prefixes),
        authority=authority,
        case_links=case_links,
        date_from=date_from,
        date_to=date_to,
    )
    constraints = Constraints(
        exhaustive=bool(EXHAUSTIVE_RE.search(q)),
        superlative=superlative,
        requires_computation=bool(COMPUTATION_RE.search(q)),
        comparative=bool(COMPARATIVE_RE.search(q)),
    )

    provenance = {
        "scope.procedure_prefix": "heuristic",
        "scope.authority": "heuristic",
        "scope.case_links": "heuristic",
        "scope.date_from": "heuristic",
        "scope.date_to": "heuristic",
        "constraints.exhaustive": "heuristic",
        "constraints.superlative": "heuristic",
        "constraints.requires_computation": "heuristic",
        "constraints.comparative": "heuristic",
    }

    return Interpretation(
        intent=None,  # se llena offline (annotated); hoy el agente no lo decide
        scope=scope,
        constraints=constraints,
        provenance=provenance,
    )


def expected_tools(query: str) -> list[str]:
    """
    Herramientas que la consulta *debería* haber disparado.

    Existe para cubrir el punto 4 de COFECE: "si una herramienta que debía
    utilizarse no fue llamada, que podamos detectarlo". Comparando esto contra
    las que realmente se llamaron, el trace responde solo esa pregunta.

    Es heurístico y así queda marcado: dice qué esperaríamos, no qué decidió
    el agente.
    """
    q = query or ""
    esperadas = []
    if COMPUTATION_RE.search(q):
        esperadas.append("calcular_plazos")
    if CASE_LINK_RE.search(q) or re.search(
        r"\b(expediente|expedientes|multa|multas|resoluci[óo]n|agente econ[óo]mico"
        r"|sentido|fecha)\b", q, re.IGNORECASE
    ):
        esperadas.append("buscar_expedientes")
    if re.search(
        r"\b(criterio|criterios|concepto|defini[óc]|mercado relevante|barreras"
        r"|poder de mercado|eficiencias|precedente|precedentes|doctrina"
        r"|ha considerado|argument)\w*\b", q, re.IGNORECASE
    ):
        esperadas.append("buscar_criterios")
    return esperadas


def extract_case_link_prefixes(text: str) -> set[str]:
    """Prefijos de los expedientes que aparecen en un texto."""
    return {cl.split("-")[0] for cl in CASE_LINK_RE.findall(text or "")}


def extract_case_links(text: str) -> list[str]:
    return sorted(set(CASE_LINK_RE.findall(text or "")))
