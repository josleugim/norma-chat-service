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
import time
import logging
import threading
from datetime import datetime, timezone
from collections import OrderedDict, defaultdict

logger = logging.getLogger(__name__)

# Patrones para detectar referencias conversacionales
REF_PREV_RE = re.compile(
    r"\b(ese|esa|eso|esos|esas|anterior|anteriores|mismo|misma|mismos|mismas"
    r"|como dijiste|lo de arriba|lo anterior|mencionaste|el caso|ese caso"
    r"|ese expediente|esos expedientes|esa concentración)\b",
    re.IGNORECASE,
)
ID_EXP_RE = re.compile(r"\b[A-Z]{2,5}-\d{3}-\d{4}\b")

# Material que cambia el sentido de un fragmento y que un recorte a la mitad
# puede borrar sin dejar rastro.
#
# COFECE lo señaló en §7 de su revisión del 22-sep: *"El límite de 400
# caracteres del cache no debe eliminar autor, negación, condición o
# atribución"*. Es la misma falla que ya habíamos medido con el texto del
# criterio truncado a 700 mientras el envoltorio viajaba entero: recortábamos
# lo que responde la pregunta.
#
# Lo peligroso no es que el fragmento salga corto. Es que salga corto **y se
# lea completo**: en H18 la conclusión pierde "una vez que cause ejecutoria" y
# se convierte en una multa firme. Un efecto condicionado leído sin su
# condición es un hecho distinto.
MATERIAL_SENSIBLE_RE = re.compile(
    r"\b(no|ni|sin|salvo|excepto|siempre que|una vez que|hasta que|mientras"
    r"|cause ejecutoria|condicionad\w+|improcedente|niega|nieg\w+|sobresee"
    r"|voto particular|voto concurrente|disidente|en contra"
    r"|ministr\w+|magistrad\w+|comisionad\w+)\b",
    re.IGNORECASE,
)


# Criterios citados en la respuesta anterior que se traen siempre al turno
# siguiente, aunque pasen del límite general.
MAX_CITADOS_PREVIOS = 30


def recortar_sin_borrar_en_silencio(texto: str, limite: int = 700) -> str:
    """
    Recorta un fragmento largo **declarando** lo que quedó fuera.

    Dos reglas, y las dos salen de errores medidos:

    1. **Cortar en frontera de oración**, no a media palabra. Un corte en seco
       produce texto que parece completo.
    2. **Decir que hay más, y de qué tipo.** Si lo que queda fuera contiene
       negaciones, condiciones o atribuciones, se dice expresamente. El modelo
       puede entonces recuperar el criterio en vez de concluir sobre un
       fragmento mutilado; callarlo lo invita a tratar la ausencia como dato.
    """
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto

    cabeza, cola = texto[:limite], texto[limite:]
    # Hacia atrás hasta el final de oración más cercano, sin perder más de un
    # tercio: si no hay puntuación cerca, vale más cortar por palabra.
    corte = max(cabeza.rfind(". "), cabeza.rfind(".\n"), cabeza.rfind("; "))
    if corte < limite * 2 // 3:
        corte = cabeza.rfind(" ")
    if corte > 0:
        cola = texto[corte + 1:]
        cabeza = cabeza[:corte + 1]

    aviso = f"[…fragmento recortado, faltan {len(cola)} caracteres"
    if MATERIAL_SENSIBLE_RE.search(cola):
        aviso += (
            "; la parte omitida contiene negaciones, condiciones o "
            "atribuciones. NO concluyas sobre el alcance de este criterio sin "
            "recuperarlo completo con buscar_criterios"
        )
    return cabeza.rstrip() + " " + aviso + "]"


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
        ttl_segundos: float = 4 * 3600,
        max_sesiones: int = 500,
        reloj=time.monotonic,
    ):
        self.max_turns = max_turns
        # Desalojo (21-oct, reporte de JM): sin él, cada session_id nuevo se
        # quedaba en memoria hasta reiniciar el proceso. Se desaloja de forma
        # perezosa al entrar a cada método público: primero lo inactivo más
        # del TTL, luego lo menos usado si se pasa del máximo.
        self.ttl_segundos = ttl_segundos
        self.max_sesiones = max_sesiones
        self._reloj = reloj
        self._acceso: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.RLock()
        self.prev_turn_top_k = prev_turn_top_k
        self.max_cached_criterios = max_cached_criterios
        self.max_cached_expedientes = max_cached_expedientes

        # {session_id: {"turns": [...], "index": {id_exp: {...}}}}
        self._sessions: dict[str, dict] = {}

        # {session_id: {paragraph_id: anchor}}. Un turno que responde desde
        # caché cita criterios de turnos anteriores, y su enlace al párrafo
        # necesita el `anchor`, que no viaja en el documento cacheado.
        self._anclas: dict[str, dict[str, str]] = {}

    # ── Desalojo ────────────────────────────────────────────

    def _drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._anclas.pop(session_id, None)
        self._acceso.pop(session_id, None)

    def _desalojar(self) -> None:
        ahora = self._reloj()
        vencidas = [s for s, t in self._acceso.items() if ahora - t > self.ttl_segundos]
        for s in vencidas:
            self._drop(s)
        excedentes = []
        while len(self._acceso) > self.max_sesiones:
            s, _ = self._acceso.popitem(last=False)
            self._drop(s)
            excedentes.append(s)
        if vencidas or excedentes:
            logger.info(
                "EvidenceCache: desalojadas %d por inactividad y %d por tope; "
                "quedan %d sesiones", len(vencidas), len(excedentes), len(self._acceso))

    def _tocar(self, session_id: str) -> None:
        """Marca uso de una sesión que existe (o que se está creando)."""
        self._acceso[session_id] = self._reloj()
        self._acceso.move_to_end(session_id)

    def _entrar(self, session_id: str, escribe: bool = False) -> None:
        self._desalojar()
        if escribe or session_id in self._sessions or session_id in self._anclas:
            self._tocar(session_id)
        self._desalojar()

    # ── API pública ─────────────────────────────────────────

    def recordar_anclas(self, session_id: str, anclas: dict[str, str]) -> None:
        if not anclas:
            return
        with self._lock:
            self._entrar(session_id, escribe=True)
            self._anclas.setdefault(session_id, {}).update(anclas)

    def anclas(self, session_id: str) -> dict[str, str]:
        with self._lock:
            self._entrar(session_id)
            return dict(self._anclas.get(session_id, {}))

    def _ensure_session(self, session_id: str) -> dict:
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "turns": [],
                "index": defaultdict(lambda: {"criterios": [], "expedientes": []}),
            }
        return self._sessions[session_id]

    def _update(
        self,
        session_id: str,
        query: str,
        criterios: list[dict],
        expedientes: list[dict],
        marcadores: dict | None = None,
        citados: list[str] | None = None,
    ):
        """
        Guarda los resultados de herramientas de este turno en el cache.

        `marcadores` (id de criterio → marcador) y `citados` (marcadores que
        usó la respuesta) permiten que el turno siguiente registre esos mismos
        documentos con el mismo marcador.
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
            "marcadores": dict(marcadores or {}),
            "citados": list(citados or []),
        })

        # Recortar historial de turnos
        if len(session["turns"]) > self.max_turns:
            session["turns"] = session["turns"][-self.max_turns:]

        logger.debug(
            f"Cache actualizado para sesión {session_id}: "
            f"{len(criterios)} criterios, {len(expedientes)} expedientes, "
            f"ids: {used_ids}"
        )

    def _select(
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

    def _contexto_para_turno(
        self, session_id: str, registry, limite: int = 12,
    ) -> str:
        """
        Evidencia de turnos previos, **registrada en el turno actual**.

        C04 del diagnóstico de COFECE (21-sep-2026). `get_context_summary`
        imprimía títulos con índices posicionales `[1]..[5]`, que no son los
        marcadores de ningún registro. El modelo veía `[C14]` en su propia
        respuesta anterior —dentro del historial— y lo reutilizaba en el turno
        siguiente, cuyo registro sólo llegaba a C10. La cita quedaba inválida
        aunque el documento fuera real: en H19 `C14` era `511_2023_2TCC`,
        páginas 89-91.

        Aquí cada pieza se registra en el registro del turno actual y se
        presenta con el marcador que le acaba de tocar. Así no hay dos espacios
        de nombres: el que el modelo lee es el que resuelve.

        Se incluye el texto y la voz, no sólo el título: un criterio del que
        sólo se conoce el encabezado no permite sostener nada, y sin la voz un
        voto particular se lee como la sentencia.
        """
        session = self._sessions.get(session_id)
        if not session or not session["turns"] or registry is None:
            return ""

        from core.voz import clasificar_voz, etiqueta as etiqueta_voz

        lineas: list[str] = []
        vistos: set[str] = set()
        # Lo que la respuesta anterior CITÓ entra siempre, aunque pase del
        # límite, y con su mismo marcador: es lo que un seguimiento ("¿estás
        # seguro?", "amplía el punto 3") va a volver a citar. El 31-oct, en un
        # seguimiento, `[C14]` quedó fuera de los 12 y su frase se retiró.
        ultimo = session["turns"][-1]
        citados = set(ultimo.get("citados") or [])
        limite = max(limite, min(len(citados), MAX_CITADOS_PREVIOS))

        def _orden(turn, c):
            m = (turn.get("marcadores") or {}).get(str(c.get("id")))
            return (0 if (turn is ultimo and m in citados) else 1)

        # De los turnos más recientes hacia atrás: lo último pesa más.
        for turn in reversed(session["turns"][-3:]):
            criterios = turn.get("criterios") or []
            for c in sorted(criterios, key=lambda c: _orden(turn, c)):
                if len(vistos) >= limite:
                    break
                previo = (turn.get("marcadores") or {}).get(str(c.get("id")))
                marker = registry.assign(c, "C", preferido=previo)
                if not marker or marker in vistos:
                    continue
                vistos.add(marker)
                meta = c.get("metadata") or {}
                v = clasificar_voz(c)
                texto = (c.get("text") or c.get("content") or "").strip()
                linea = (
                    f"[{marker}] {meta.get('id_expediente', '?')} — "
                    f"{meta.get('title', '?')} (pp. "
                    f"{meta.get('paginas_parrafos', '?')})"
                )
                if v["voz"] != "no_identificada":
                    linea += f" · {etiqueta_voz(v['voz'])}"
                    if v["autor"]:
                        linea += f" de {v['autor']}"
                lineas.append(linea)
                if texto:
                    lineas.append(
                        "      " + recortar_sin_borrar_en_silencio(texto)
                    )

        if not lineas:
            return ""
        return (
            "Evidencia recuperada en turnos anteriores de esta conversación, "
            "ya registrada para ESTE turno. Cita con estos marcadores; los de "
            "respuestas anteriores no son válidos aquí.\n"
            + "\n".join(lineas)
        )

    def _get_context_summary(self, session_id: str) -> str:
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


    def update(self, session_id: str, query: str, criterios: list[dict],
               expedientes: list[dict], marcadores: dict | None = None,
               citados: list[str] | None = None):
        with self._lock:
            self._entrar(session_id, escribe=True)
            return self._update(session_id=session_id, query=query,
                                criterios=criterios, expedientes=expedientes,
                                marcadores=marcadores, citados=citados)

    def select(self, session_id: str, user_query: str):
        with self._lock:
            self._entrar(session_id)
            return self._select(session_id, user_query)

    def contexto_para_turno(self, session_id: str, registry, limite: int = 12) -> str:
        with self._lock:
            self._entrar(session_id)
            return self._contexto_para_turno(session_id, registry, limite=limite)

    def get_context_summary(self, session_id: str) -> str:
        with self._lock:
            self._entrar(session_id)
            return self._get_context_summary(session_id)

    def clear_session(self, session_id: str) -> None:
        """Borra todo lo de una sesión (evidencia y anclas). Idempotente."""
        with self._lock:
            self._drop(session_id)

    def session_count(self) -> int:
        with self._lock:
            self._desalojar()
            return len(self._sessions)
