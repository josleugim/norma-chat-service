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
    citation_builder,
    criterio_results: list[list],
    expediente_results: list[list],
    references: list,
    docs_in_context: list[dict[str, Any]],
    expected_prefixes: list[str],
) -> Answer:
    """
    Args:
        citation_builder: instancia de CitationBuilder, para resolver marcadores
            con exactamente la misma lógica que usa el agente.
        docs_in_context: [{doc_id, case_link, ...}] de lo que entró al prompt.
        expected_prefixes: prefijos que la pregunta pidió (VCN, IO, ...).
    """
    text = text or ""

    emitted: list[str] = []
    unresolved: list[str] = []
    cited_case_links: set[str] = set()

    for match in CITATION_RE.finditer(text):
        marker = f"{match.group(1)}{match.group(2)}"
        if marker not in emitted:
            emitted.append(marker)
        item = citation_builder.resolve_marker(
            match.group(1), int(match.group(2)), criterio_results, expediente_results
        )
        if item is None:
            if marker not in unresolved:
                unresolved.append(marker)
        else:
            link = _case_link_of(item)
            if link:
                cited_case_links.add(link)

    resolved = []
    for ref in references:
        data = ref.model_dump() if hasattr(ref, "model_dump") else dict(ref)
        resolved.append({
            "doc_id": data.get("id_expediente", ""),
            "case_link": data.get("id_expediente", ""),
            "source_type": data.get("source_type", ""),
            "title": data.get("title"),
        })
        if data.get("id_expediente"):
            cited_case_links.add(data["id_expediente"])

    uncited = sorted(
        d.get("doc_id", "")
        for d in docs_in_context
        if d.get("case_link") and d["case_link"] not in cited_case_links
    )

    mentioned = extract_case_links(text)
    scope_mismatch = False
    if expected_prefixes:
        mentioned_prefixes = extract_case_link_prefixes(text)
        # Desajuste = se mencionó al menos un expediente de un prefijo que no
        # se pidió. No basta con que falten: contestar de más también es error.
        scope_mismatch = bool(mentioned_prefixes - set(expected_prefixes))

    return Answer(
        text=text,
        length_chars=len(text),
        citations_emitted=emitted,
        citations_resolved=resolved,
        citations_unresolved=unresolved,
        docs_in_context_uncited=uncited,
        has_fuentes_section=bool(FUENTES_RE.search(text)) or "FUENTES" in text,
        format_markers=count_format_markers(text),
        case_links_mentioned=mentioned,
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
