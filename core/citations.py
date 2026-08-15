"""
Registro de citas por turno.

Reemplaza la resolución posicional de citas por una asociación explícita.

El problema que resuelve, reportado por COFECE el 14-ago-2026: cuando un turno
hacía varias búsquedas, `[E1]` se resolvía contra el ÚLTIMO bloque donde el
índice fuera válido. Una respuesta que hablaba de VCN-004-2024 podía mostrar
una fuente que apuntaba a VCN-004-2022. En un producto jurídico eso es lo más
grave que puede pasar: el texto es correcto y la fuente manda al abogado al
expediente equivocado.

La corrección es estructural. En vez de adivinar después a qué documento se
refería el modelo, **se le entrega el identificador junto con el documento** y
él lo cita textualmente. Resolver una cita pasa a ser una búsqueda en un
diccionario.

Reglas:

- Los marcadores son únicos y estables durante todo el turno. Si hay dos
  búsquedas, la segunda continúa la numeración; nunca reinicia.
- Un mismo expediente recibe siempre el mismo marcador, aunque aparezca en
  varias búsquedas.
- Un marcador que no está en el registro **no se muestra**. COFECE fue
  explícito: es preferible no mostrar cita a mostrar una incorrecta.
"""
from typing import Any, Optional


class CitationRegistry:
    """Asigna y resuelve marcadores de cita dentro de un turno."""

    def __init__(self):
        # marcador → documento
        self._docs: dict[str, dict] = {}
        # clave de identidad → marcador, para no duplicar el mismo expediente
        self._by_key: dict[str, str] = {}
        self._counters = {"C": 0, "E": 0}

    def assign(self, doc: dict, kind: str) -> str:
        """
        Registra un documento y devuelve su marcador ('C3', 'E7').
        Si ya estaba registrado, devuelve el mismo marcador.
        """
        key = f"{kind}:{self._identity(doc, kind)}"
        if key in self._by_key:
            return self._by_key[key]

        self._counters[kind] += 1
        marker = f"{kind}{self._counters[kind]}"
        self._by_key[key] = marker
        self._docs[marker] = doc
        return marker

    def resolve(self, marker: str) -> Optional[dict]:
        """Devuelve el documento del marcador, o None si no existe."""
        return self._docs.get(marker.upper())

    def markers(self) -> list[str]:
        return list(self._docs)

    def case_link_of(self, marker: str) -> str:
        doc = self.resolve(marker)
        return _case_link(doc) if doc else ""

    def to_trace(self) -> list[dict]:
        """
        Cadena completa afirmación → evidencia → registro → expediente, para
        que el trace permita auditar cualquier cita, como pidió COFECE.
        """
        salida = []
        for marker, doc in self._docs.items():
            meta = doc.get("metadata") or {}
            salida.append({
                "marker": marker,
                "case_link": _case_link(doc),
                "doc_id": str(doc.get("id", "")),
                "source_type": "criterio" if marker.startswith("C") else "estadistica",
                "title": meta.get("title") if isinstance(meta, dict) else None,
                "pages": meta.get("paginas_parrafos") if isinstance(meta, dict) else None,
            })
        return salida

    # ── Internos ────────────────────────────────────────────

    def _identity(self, doc: dict, kind: str) -> str:
        """
        Identidad estable de un documento. Para criterios importa el chunk
        concreto —un mismo expediente aporta varios—; para expedientes basta
        el número de expediente.
        """
        if kind == "C":
            doc_id = str(doc.get("id", ""))
            if doc_id:
                return doc_id
            meta = doc.get("metadata") or {}
            anchor = meta.get("anchor", "") if isinstance(meta, dict) else ""
            return f"{_case_link(doc)}|{anchor[:80]}"
        return _case_link(doc) or str(doc.get("id", ""))


def _case_link(doc: Any) -> str:
    if not isinstance(doc, dict):
        return ""
    if doc.get("caseLink"):
        return doc["caseLink"]
    if doc.get("id_expediente"):
        return doc["id_expediente"]
    meta = doc.get("metadata") or {}
    if isinstance(meta, dict):
        return meta.get("id_expediente") or meta.get("caseLink") or ""
    return ""
