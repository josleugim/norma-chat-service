"""
Construye referencias estructuradas a partir de la respuesta del LLM.
Parsea tags [C1], [E1] y los mapea a objetos ChatReference.

NOTA sobre indexación: el LLM recibe resultados de cada tool call como un
bloque independiente con su propio conteo (1..N). Si hay dos buscar_criterios,
el LLM cita [C1] refiriéndose al primer resultado de *cada* búsqueda, no a
un índice global acumulado. Por eso build_references usa offsets por tool call
y resuelve con fallback a la lista aplanada.
"""
import re
import logging
from models.schemas import ReferenceItem

logger = logging.getLogger(__name__)


class CitationBuilder:

    def build_references(
        self,
        llm_response: str,
        criterio_results: list[list],  # lista de listas (una por tool call)
        expediente_results: list[list],
    ) -> tuple[str, list[ReferenceItem]]:
        """
        Parsea [C1], [C2], [E1], [E2], etc. del texto del LLM.
        Retorna (texto original, lista de referencias únicas).

        Estrategia de resolución de índices:
        - Si solo hubo 1 tool call de ese tipo, indexar directo.
        - Si hubo N tool calls, intentar resolver contra cada bloque
          (el LLM tiende a reiniciar la numeración por bloque) y
          como fallback usar la lista aplanada.
        """
        # Aplanar para fallback
        all_criterios = self._flatten(criterio_results)
        all_expedientes = self._flatten(expediente_results)

        # Calcular offsets por tool call: [(offset_inicio, lista), ...]
        criterio_blocks = self._build_blocks(criterio_results)
        expediente_blocks = self._build_blocks(expediente_results)

        references: list[ReferenceItem] = []
        seen_keys: set[str] = set()

        for match in re.finditer(r"\[([CE])(\d+)\]", llm_response):
            ref_type = match.group(1)
            ref_num = int(match.group(2))  # 1-indexed como lo cita el LLM
            ref_idx = ref_num - 1          # 0-indexed

            if ref_type == "C":
                item = self._resolve_item(
                    ref_idx, criterio_blocks, all_criterios
                )
                if item is None:
                    logger.debug(f"[C{ref_num}] no resuelve a ningún criterio")
                    continue
                ref = self._build_criterio_ref(item, ref_idx, seen_keys)
                if ref:
                    references.append(ref)

            elif ref_type == "E":
                item = self._resolve_item(
                    ref_idx, expediente_blocks, all_expedientes
                )
                if item is None:
                    logger.debug(f"[E{ref_num}] no resuelve a ningún expediente")
                    continue
                ref = self._build_expediente_ref(item, ref_idx, seen_keys)
                if ref:
                    references.append(ref)

        return llm_response, references

    # ── Resolución de índices con bloques ─────────────────────

    def _build_blocks(self, nested: list[list]) -> list[tuple[int, list]]:
        """
        Construye lista de (offset_acumulado, items_del_bloque).
        Ejemplo: [[a,b,c], [d,e]] → [(0, [a,b,c]), (3, [d,e])]
        """
        blocks = []
        offset = 0
        for sublist in nested:
            items = sublist if isinstance(sublist, list) else [sublist]
            blocks.append((offset, items))
            offset += len(items)
        return blocks

    def _resolve_item(
        self,
        ref_idx: int,
        blocks: list[tuple[int, list]],
        flat_list: list,
    ) -> dict | None:
        """
        Intenta resolver ref_idx contra los bloques de tool calls.

        Estrategia:
        1. Si hay un solo bloque → índice directo.
        2. Si hay múltiples bloques → probar ref_idx dentro de cada bloque
           (el LLM suele reiniciar a [C1] por cada búsqueda). Tomar el
           último bloque donde el índice sea válido (el más reciente).
        3. Fallback: ref_idx en la lista aplanada (por si el LLM sí
           usó numeración acumulada).
        """
        if not blocks and not flat_list:
            return None

        # Caso trivial: un solo bloque
        if len(blocks) == 1:
            _, items = blocks[0]
            if ref_idx < len(items):
                return items[ref_idx]
            return None

        # Múltiples bloques: probar ref_idx dentro de cada bloque,
        # preferir el último match (más reciente en la conversación)
        last_match = None
        for _offset, items in blocks:
            if ref_idx < len(items):
                last_match = items[ref_idx]

        if last_match is not None:
            return last_match

        # Fallback: lista aplanada (numeración acumulada)
        if ref_idx < len(flat_list):
            return flat_list[ref_idx]

        return None

    # ── Constructores de referencias ─────────────────────────

    def _build_criterio_ref(
        self, item: dict, ref_idx: int, seen_keys: set[str]
    ) -> ReferenceItem | None:
        meta = item.get("metadata", {}) if isinstance(item, dict) else {}
        id_exp = meta.get("id_expediente") or meta.get("caseLink") or ""
        item_id = item.get("id", ref_idx) if isinstance(item, dict) else ref_idx
        ref_key = f"C:{id_exp or item_id}:{ref_idx}"
        if ref_key in seen_keys:
            return None
        seen_keys.add(ref_key)

        title = meta.get("title") or meta.get("anchor", "")[:120] or None
        pages = meta.get("paginas_parrafos") or ""
        if not pages:
            pdf_p = meta.get("resolution_pages") or meta.get("pdf_pages") or []
            if pdf_p:
                pages = ", ".join(str(p) for p in pdf_p)

        return ReferenceItem(
            id_expediente=id_exp,
            nombre_expediente=meta.get("nombre_expediente") or "",
            source_type="criterio",
            relevance_score=item.get("score", 0) if isinstance(item, dict) else 0,
            url=self._build_url(id_exp),
            title=title,
            parent_titles=meta.get("parent_titles"),
            paginas_parrafos=pages or None,
            article=meta.get("article"),
        )

    def _build_expediente_ref(
        self, item: dict, ref_idx: int, seen_keys: set[str]
    ) -> ReferenceItem | None:
        if not isinstance(item, dict):
            return None
        id_exp = item.get("caseLink") or item.get("id_expediente", "")
        ref_key = f"E:{id_exp}:{ref_idx}"
        if ref_key in seen_keys:
            return None
        seen_keys.add(ref_key)

        agentes = item.get("economicAgents") or item.get("agentes_economicos", "")
        if isinstance(agentes, list):
            agentes = " / ".join(agentes)

        return ReferenceItem(
            id_expediente=id_exp,
            nombre_expediente=item.get("name") or str(agentes)[:80],
            source_type="estadistica",
            url=self._build_url(id_exp),
            autoridad=item.get("authority") or item.get("autoridad"),
            tipo_procedimiento=item.get("typeOfProcedure") or item.get("tipo_procedimiento"),
            sentido_resolucion=item.get("senseOfResolution") or item.get("sentido_resolucion"),
            fecha_resolucion=item.get("resolutionDate") or item.get("fecha_resolucion"),
        )

    def _build_url(self, id_expediente: str) -> str:
        if not id_expediente:
            return ""
        return f"/title?caseLink={id_expediente}"

    def _flatten(self, nested: list[list]) -> list:
        flat = []
        for sublist in nested:
            if isinstance(sublist, list):
                flat.extend(sublist)
            else:
                flat.append(sublist)
        return flat
