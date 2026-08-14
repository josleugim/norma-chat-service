"""
Atribución de fallas por etapa del pipeline.

Responde al criterio de aceptación que puso COFECE:

    "ante una respuesta incorrecta debemos poder abrir el trace y determinar
     si el error fue: pregunta/filtros → retrieval → selección de contexto/
     ranking → tool calling → generación → cita"

Las etapas se evalúan **en orden**, porque los errores se propagan: si el
agente buscó en el universo equivocado, no tiene sentido culpar a la
generación. Se reporta la etapa más temprana con evidencia.

Esto NO dice si la respuesta fue correcta —eso lo juzga una persona— sino
dónde se rompió el flujo cuando algo salió mal. La distinción importa: sirve
para decirle al equipo qué componente corregir, en vez de "Norma contestó mal".
"""
from typing import Any, Optional

# Orden del pipeline, tal como lo planteó COFECE.
ETAPAS = [
    "pregunta/filtros",
    "retrieval",
    "seleccion_contexto",
    "tool_calling",
    "generacion",
    "cita",
]


def diagnose(trace) -> dict[str, Any]:
    """
    Devuelve {stage, reason, signals} con la etapa más temprana que muestra
    evidencia de problema, o stage=None si el flujo se ve limpio.
    """
    d = trace.decisions
    a = trace.answer
    hallazgos: list[dict] = []

    # ── 1. Pregunta / filtros ───────────────────────────────
    # El agente buscó en un universo distinto al que se le pidió.
    if a.scope_mismatch:
        esperado = ",".join(trace.interpretation.scope.procedure_prefix) or "?"
        observado = ",".join(a.scope_observed) or "?"
        hallazgos.append({
            "stage": "pregunta/filtros",
            "reason": (
                f"Se pidió {esperado} y el retrieval trajo {observado}: "
                f"los filtros no corresponden a la consulta."
            ),
        })

    # ── 2. Retrieval ────────────────────────────────────────
    # Consulta exhaustiva resuelta sobre una fracción del universo.
    if d.exhaustive_but_truncated:
        cov = _peor_cobertura(trace)
        detalle = (
            f" ({cov['returned']} de {cov['total_available']})"
            if cov and cov.get("total_available") else ""
        )
        hallazgos.append({
            "stage": "retrieval",
            "reason": (
                f"La consulta exige recorrer el universo y la búsqueda quedó "
                f"truncada{detalle}: la respuesta no puede sostenerse."
            ),
        })
    elif _sin_resultados(trace):
        hallazgos.append({
            "stage": "retrieval",
            "reason": "Las búsquedas no devolvieron ningún documento.",
        })

    # ── 3. Selección de contexto / ranking ──────────────────
    if d.context_condensed:
        hallazgos.append({
            "stage": "seleccion_contexto",
            "reason": (
                "La respuesta final se generó con los resultados condensados: "
                "el modelo no vio la evidencia completa que recuperó."
            ),
        })

    # ── 4. Tool calling ─────────────────────────────────────
    if d.tools_expected_not_called:
        faltantes = ", ".join(d.tools_expected_not_called)
        hallazgos.append({
            "stage": "tool_calling",
            "reason": f"No se llamó a {faltantes}, que la consulta requería.",
        })
    plazos_sin_datos = _plazos_sin_datos(trace)
    if plazos_sin_datos:
        hallazgos.append({
            "stage": "tool_calling",
            "reason": (
                f"{plazos_sin_datos} expedientes entraron al cómputo de plazos "
                f"sin fecha de inicio: la herramienta no tenía con qué calcular."
            ),
        })

    # ── 5. Generación ───────────────────────────────────────
    # Recuperó evidencia y no la usó: se perdió al redactar.
    if a.docs_in_context_uncited and trace.context.docs_in_context:
        sin_usar = len(a.docs_in_context_uncited)
        total = len(trace.context.docs_in_context)
        if total and sin_usar / total > 0.5:
            hallazgos.append({
                "stage": "generacion",
                "reason": (
                    f"{sin_usar} de {total} documentos recuperados no se usaron "
                    f"en la respuesta."
                ),
            })

    # ── 6. Cita ─────────────────────────────────────────────
    if a.citations_unresolved:
        hallazgos.append({
            "stage": "cita",
            "reason": (
                f"Citas que no resuelven a ningún documento: "
                f"{', '.join(a.citations_unresolved)}."
            ),
        })

    if trace.outcome.status != "ok":
        hallazgos.append({
            "stage": "tool_calling",
            "reason": f"El turno terminó con estado '{trace.outcome.status}'.",
        })

    if not hallazgos:
        return {"stage": None, "reason": "Flujo sin anomalías detectables.",
                "signals": []}

    hallazgos.sort(key=lambda h: ETAPAS.index(h["stage"]))
    primero = hallazgos[0]
    return {
        "stage": primero["stage"],
        "reason": primero["reason"],
        "signals": [f"{h['stage']}: {h['reason']}" for h in hallazgos],
    }


# ── Helpers ─────────────────────────────────────────────────

def _peor_cobertura(trace) -> Optional[dict]:
    """La búsqueda que dejó fuera más universo."""
    peor, brecha = None, -1
    for s in trace.steps:
        c = s.coverage
        if c and c.truncated and c.total_available:
            actual = c.total_available - c.returned
            if actual > brecha:
                peor, brecha = c.model_dump(), actual
    return peor


def _sin_resultados(trace) -> bool:
    busquedas = [s for s in trace.steps
                 if s.tool in ("buscar_criterios", "buscar_expedientes")]
    if not busquedas:
        return False
    return all(
        (s.coverage.returned if s.coverage else 0) == 0 for s in busquedas
    )


def _plazos_sin_datos(trace) -> int:
    return sum(
        1
        for s in trace.steps
        if s.computation
        for c in s.computation.get("per_case", [])
        if c.get("date_start") is None or c.get("date_end") is None
    )
