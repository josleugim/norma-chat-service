"""
Estado de un turno del agente.

Existe por dos razones:

1. **Concurrencia.** El agente es un singleton en `app.state`, así que guardar
   estado del turno en `self.*` hacía que dos peticiones simultáneas se
   pisaran: los expedientes de un usuario podían acabar en la respuesta de
   otro. Todo lo que dura un turno vive aquí y se crea por petición.

2. **Trazabilidad de citas.** El registro de citas tiene que acompañar al turno
   completo —desde que se recupera un documento hasta que se muestra la
   fuente— y ese es exactamente el alcance de este objeto.
"""
from dataclasses import dataclass, field

from core.citations import CitationRegistry


@dataclass
class TurnState:
    # Identificadores estables de cita para este turno
    registry: CitationRegistry = field(default_factory=CitationRegistry)

    # Últimos expedientes recuperados. Permite que calcular_plazos opere sobre
    # ellos sin que el modelo tenga que devolverlos, que es lo que truncaba
    # sus argumentos contra el límite de tokens.
    last_expedientes: list[dict] = field(default_factory=list)

    # Cobertura de la última búsqueda
    universo_completo: bool = False
    universo_tamano: int = 0

    # Filtro local que no coincidió con nada, para poder distinguir
    # "no hay ninguno" de "tu filtro estaba mal escrito"
    filtro_vacio: dict | None = None

    # ── Routing y control de suficiencia ────────────────────
    # La secuencia que pidió COFECE para v1, sin reranker ni multiagente:
    #   routing → evidence check → un retry focalizado → abstención
    query: str = ""
    query_type: str = ""
    sufficiency_checks: list[dict] = field(default_factory=list)
    retrieval_retries: int = 0
    pending_retry_terms: list[str] = field(default_factory=list)
    abstention_reason: str | None = None

    # ── Linaje de filtros ───────────────────────────────────
    # Mientras la API una con OR en vez de intersectar, hay que poder ver
    # filtros pedidos → resultados de la API → postfiltro local → universo
    # final, con cuántos descartó cada condición.
    filtros_aplicados: list[dict] = field(default_factory=list)

    # ── Auditoría de cálculos deterministas ─────────────────
    # Un registro por expediente y operación, para poder reconstruir un
    # promedio o un máximo desde los artifacts sin releer el código.
    computation_audit: list[dict] = field(default_factory=list)
    # Anomalías de datos encontradas, en formato analizable.
    anomalias: list[dict] = field(default_factory=list)
