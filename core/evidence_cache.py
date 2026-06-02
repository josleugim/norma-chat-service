"""
Cache de evidencia por sesión.

Almacena los resultados de herramientas (criterios y expedientes) de turnos
anteriores para que el agente pueda referenciarlos en preguntas de seguimiento
sin volver a buscar.

Ejemplo: el usuario pregunta sobre mercado relevante (turno 1), el agente busca
criterios. En el turno 2 el usuario dice "y en ese mismo caso, ¿qué multas hubo?"
— el cache permite inyectar los expedientes del turno anterior como contexto.
"""
import re
import logging
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)

# Patrones para detectar referencias conversacionales
REF_PREV_RE = re.compile(
    r"\b(ese|esa|eso|esos|esas|anterior|anteriores|mismo|misma|mismos|mismas"
    r"|como dijiste|lo de arriba|lo anterior|mencionaste|el caso|ese caso"
    r"|ese expediente|esos expedientes|esa concentración)\b",
    re.IGNORECASE,
)
ID_EXP_RE = re.compile(r"\b[A-Z]{2,5}-\d{3}-\d{4}\b")


class EvidenceCache:
    """
    Cache en memoria por sesión. Cada sesión mantiene:
    - turns: lista de turnos con evidencia recuperada
    - index: índice por id_expediente para lookup rápido
    """

    def __init__(
        self,
        max_turns: int = 15,
        prev_turn_top_k: int = 5,
        max_cached_criterios: int = 10,
        max_cached_expedientes: int = 15,
    ):
        self.max_turns = max_turns
        self.prev_turn_top_k = prev_turn_top_k
        self.max_cached_criterios = max_cached_criterios
        self.max_cached_expedientes = max_cached_expedientes

        # {session_id: {"turns": [...], "index": {id_exp: {...}}}}
        self._sessions: dict[str, dict] = {}

    def _ensure_session(self, session_id: str) -> dict:
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "turns": [],
                "index": defaultdict(lambda: {"criterios": [], "expedientes": []}),
            }
        return self._sessions[session_id]

    def update(
        self,
        session_id: str,
        query: str,
        criterios: list[dict],
        expedientes: list[dict],
    ):
        """
        Guarda los resultados de herramientas de este turno en el cache.
        Llamar al final de cada turno del agente.
        """
        session = self._ensure_session(session_id)

        # Extraer ids de expediente de los resultados
        used_ids = set()
        for item in criterios:
            meta = item.get("metadata", {})
            exp_id = meta.get("id_expediente", "")
            if exp_id:
                used_ids.add(exp_id)
        for item in expedientes:
            exp_id = item.get("id_expediente", "")
            if exp_id:
                used_ids.add(exp_id)

        # Actualizar índice por expediente
        for exp_id in used_ids:
            entry = session["index"][exp_id]
            # Agregar criterios nuevos (dedup por id)
            existing_crit_ids = {c.get("id") for c in entry["criterios"]}
            for c in criterios:
                if c.get("metadata", {}).get("id_expediente") == exp_id:
                    if c.get("id") not in existing_crit_ids:
                        entry["criterios"].append(c)
                        existing_crit_ids.add(c.get("id"))
            # Agregar expedientes nuevos (dedup por id_expediente)
            existing_exp_ids = {e.get("id_expediente") for e in entry["expedientes"]}
            for e in expedientes:
                if e.get("id_expediente") == exp_id:
                    if exp_id not in existing_exp_ids:
                        entry["expedientes"].append(e)
                        existing_exp_ids.add(exp_id)

        # Guardar turno
        session["turns"].append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "criterios": criterios,
            "expedientes": expedientes,
            "used_ids": sorted(list(used_ids)),
        })

        # Recortar historial de turnos
        if len(session["turns"]) > self.max_turns:
            session["turns"] = session["turns"][-self.max_turns:]

        logger.debug(
            f"Cache actualizado para sesión {session_id}: "
            f"{len(criterios)} criterios, {len(expedientes)} expedientes, "
            f"ids: {used_ids}"
        )

    def select(
        self,
        session_id: str,
        user_query: str,
    ) -> tuple[list[dict], list[dict], bool]:
        """
        Decide qué evidencia cacheada inyectar según la consulta.

        Retorna: (criterios_cached, expedientes_cached, used_cache)

        Reglas:
        1. Si el usuario menciona IDs de expediente explícitos → traer todo
           lo cacheado para esos expedientes.
        2. Si referencia "lo anterior" / "ese caso" → traer evidencia del
           último turno.
        3. Si no hay referencia explícita → traer un top-k pequeño del
           último turno para continuidad suave.
        """
        session = self._sessions.get(session_id)
        if not session or not session["turns"]:
            return [], [], False

        q = user_query.strip()

        # Caso 1: IDs explícitos en la consulta
        explicit_ids = ID_EXP_RE.findall(q)
        if explicit_ids:
            cached_criterios = []
            cached_expedientes = []
            for exp_id in explicit_ids:
                entry = session["index"].get(exp_id)
                if entry:
                    cached_criterios.extend(entry.get("criterios", []))
                    cached_expedientes.extend(entry.get("expedientes", []))
            if cached_criterios or cached_expedientes:
                logger.debug(f"Cache hit por IDs explícitos: {explicit_ids}")
                return (
                    cached_criterios[:self.max_cached_criterios],
                    cached_expedientes[:self.max_cached_expedientes],
                    True,
                )

        # Caso 2: referencia conversacional ("ese caso", "lo anterior", etc.)
        has_ref_prev = bool(REF_PREV_RE.search(q))
        last_turn = session["turns"][-1]

        if has_ref_prev:
            logger.debug("Cache hit por referencia conversacional")
            return (
                (last_turn.get("criterios") or [])[:self.prev_turn_top_k],
                (last_turn.get("expedientes") or [])[:self.prev_turn_top_k],
                True,
            )

        # Caso 3: continuidad suave — top-k mínimo del turno anterior
        logger.debug("Cache: continuidad suave del turno anterior")
        return (
            (last_turn.get("criterios") or [])[:self.prev_turn_top_k],
            (last_turn.get("expedientes") or [])[:self.prev_turn_top_k],
            True,
        )

    def get_context_summary(self, session_id: str) -> str:
        """
        Genera un resumen de la evidencia cacheada para inyectar
        en el contexto del agente. Formato compacto.
        """
        session = self._sessions.get(session_id)
        if not session or not session["turns"]:
            return ""

        lines = ["=== EVIDENCIA DE TURNOS ANTERIORES (CACHE) ==="]
        last_turns = session["turns"][-3:]  # últimos 3 turnos

        for turn in last_turns:
            lines.append(f"\nConsulta previa: \"{turn['query']}\"")

            if turn.get("criterios"):
                lines.append("  Criterios encontrados:")
                for i, c in enumerate(turn["criterios"][:5], 1):
                    meta = c.get("metadata", {})
                    lines.append(
                        f"    [{i}] {meta.get('id_expediente', '?')} — "
                        f"{meta.get('title', '?')} (pp. {meta.get('paginas_parrafos', '?')})"
                    )

            if turn.get("expedientes"):
                lines.append("  Expedientes encontrados:")
                for i, e in enumerate(turn["expedientes"][:5], 1):
                    lines.append(
                        f"    [{i}] {e.get('id_expediente', '?')} — "
                        f"{e.get('sentido_resolucion', '?')} — "
                        f"{e.get('agentes_economicos', '?')[:60]}"
                    )

        lines.append("=== FIN CACHE ===\n")
        return "\n".join(lines)

    def clear_session(self, session_id: str):
        """Limpia el cache de una sesión."""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def session_count(self) -> int:
        return len(self._sessions)
