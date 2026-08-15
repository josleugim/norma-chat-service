"""
Análisis determinista de la respuesta final.

Convierte en números lo que hoy solo se puede detectar leyendo la respuesta:
citas alucinadas, markdown crudo, y desajuste de scope (el error VCN/CNT del
comentario #7 de Imanol).

Todo lo de aquí es provenance="derived": se calcula de lo que realmente pasó.
"""
import re
from typing import Any

from core.tracing.heuristics import extract_case_link_prefixes, extract_case_links
from core.tracing.schema import Answer

CITATION_RE = re.compile(r"\[([CE])(\d+)\]")
FUENTES_RE = re.compile(r"^\s*(\*\*)?\s*FUENTES\s*(\*\*)?\s*:?\s*$", re.IGNORECASE | re.MULTILINE)


def count_format_markers(text: str) -> dict[str, int]:
    """
    Cuenta marcadores de markdown crudo. Vuelve medible el comentario #2
    (se ven ###, ***, | sin renderizar) y ayuda a decidir si se corrige en el
    prompt o en el frontend.
    """
    return {
        "h3": len(re.findall(r"^\s*#{1,6}\s", text, re.MULTILINE)),
        "bold": len(re.findall(r"\*\*[^*]+\*\*", text)),
        "asterisks": text.count("***"),
        "pipes": text.count("|"),
        "brackets": len(CITATION_RE.findall(text)),
    }


def analyze_answer(
    text: str,
    registry,
    references: list,
    unresolved: list[str],
    docs_in_context: list[dict[str, Any]],
    expected_prefixes: list[str],
) -> Answer:
    """
    Args:
        registry: CitationRegistry del turno. La resolución ya ocurrió por
            diccionario; aquí solo se registra la cadena para el trace.
        unresolved: marcadores que el modelo citó y no existen en el registro.
        docs_in_context: [{doc_id, case_link, ...}] de lo que entró al prompt.
        expected_prefixes: prefijos que la pregunta pidió (VCN, IO, ...).
    """
    text = text or ""

    emitted: list[str] = []
    cited_case_links: set[str] = set()

    for match in CITATION_RE.finditer(text):
        marker = f"{match.group(1)}{match.group(2)}"
        if marker not in emitted:
            emitted.append(marker)

    # Cadena completa por cita: marcador → registro → expediente → fuente.
    # Es lo que permite auditar cualquier cita desde el trace.
    resolved = []
    for ref in references:
        data = ref.model_dump() if hasattr(ref, "model_dump") else dict(ref)
        marker = data.get("marker")
        resolved.append({
            "marker": marker,
            "doc_id": data.get("id_expediente", ""),
            "case_link": data.get("id_expediente", ""),
            "source_type": data.get("source_type", ""),
            "title": data.get("title"),
            # Verificación cruzada: lo que dice el registro para ese marcador.
            "registry_case_link": (
                registry.case_link_of(marker) if marker and registry else None
            ),
        })
        if data.get("id_expediente"):
            cited_case_links.add(data["id_expediente"])

    uncited = sorted(
        d.get("doc_id", "")
        for d in docs_in_context
        if d.get("case_link") and d["case_link"] not in cited_case_links
    )

    mentioned = extract_case_links(text)

    # Desajuste de scope. Se mira el retrieval, no solo la respuesta: si el
    # agente contesta con un promedio agregado sin citar expedientes, el texto
    # no delata nada, pero los documentos que entraron al contexto sí. Ese es
    # justo el caso del comentario #7 —preguntar por VCN y que el agente busque
    # concentraciones notificadas— y mirando solo el texto se escapa.
    observed = extract_case_link_prefixes(text)
    for d in docs_in_context:
        link = d.get("case_link") or ""
        if "-" in link:
            observed.add(link.split("-")[0])

    scope_mismatch = False
    if expected_prefixes:
        # Contestar de más también es error, no solo que falten.
        scope_mismatch = bool(observed - set(expected_prefixes))

    return Answer(
        text=text,
        length_chars=len(text),
        citations_emitted=emitted,
        citations_resolved=resolved,
        citations_unresolved=list(unresolved or []),
        citation_registry=registry.to_trace() if registry else [],
        docs_in_context_uncited=uncited,
        has_fuentes_section=bool(FUENTES_RE.search(text)) or "FUENTES" in text,
        format_markers=count_format_markers(text),
        case_links_mentioned=mentioned,
        scope_observed=sorted(observed),
        scope_mismatch=scope_mismatch,
    )


def _case_link_of(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    if item.get("caseLink"):
        return item["caseLink"]
    if item.get("id_expediente"):
        return item["id_expediente"]
    meta = item.get("metadata") or {}
    if isinstance(meta, dict):
        return meta.get("id_expediente") or meta.get("caseLink") or ""
    return ""
