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

    # Conjuntos identificados de expedientes, uno por búsqueda.
    #
    # `last_expedientes` se sobrescribe con cada búsqueda, así que una
    # operación de cálculo no podía decir sobre QUÉ conjunto se hizo: si el
    # modelo buscaba otra cosa entre el cálculo y la auditoría, la referencia
    # apuntaba a un conjunto distinto. COFECE lo pidió como `dataset_id`:
    # "otra búsqueda crea otro conjunto sin sustituirlo".
    #
    # Aquí cada búsqueda deja su conjunto con un id, y las operaciones citan
    # ese id. Inmutable no significa persistente: significa que una búsqueda
    # posterior no cambia la base de una operación ya hecha.
    datasets: dict[str, list[dict]] = field(default_factory=dict)
    dataset_actual: str | None = None

    def nuevo_dataset(self, registros: list[dict]) -> str:
        """Guarda un conjunto recuperado y devuelve su identificador."""
        ds = f"ds{len(self.datasets) + 1}"
        self.datasets[ds] = [dict(r) for r in registros if isinstance(r, dict)]
        self.dataset_actual = ds
        return ds

    def dataset(self, ds: str | None) -> list[dict]:
        """El conjunto pedido, o el actual. Vacío si no existe."""
        return self.datasets.get(ds or self.dataset_actual or "", [])

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

    # ── Composición de la evidencia ─────────────────────────
    # Cuántas resoluciones de la autoridad de competencia y cuántas sentencias
    # del poder judicial entraron al contexto. Sin esto, una respuesta sobre
    # "los criterios de la COFECE" construida con 17 sentencias de 60 fuentes
    # se ve idéntica a una construida sólo con resoluciones.
    composicion_fuentes: dict | None = None

    # ── Rutas de búsqueda ejercidas en el turno ─────────────
    # Cuántos resultados devolvió cada herramienta de recuperación. Sirve para
    # una sola pregunta, que es la que q14 contestó mal: ¿se puede afirmar que
    # algo NO existe?
    #
    # Ante "¿qué precedentes hay en el mercado de distribución de
    # medicamentos?" el agente hizo UNA búsqueda léxica sobre metadatos, recibió
    # cero, y afirmó que no existen precedentes — sin consultar nunca el índice
    # semántico de criterios, que es donde vivirían. La respuesta resultó
    # correcta, y eso es lo que la hace peligrosa: falsa exhaustividad
    # acertando por suerte, igual que el filtro de negación de §24.6.
    resultados_por_herramienta: dict[str, int] = field(default_factory=dict)

    # ── Alcance documental de la búsqueda de criterios ──────
    # Qué expedientes se pidieron explícitamente y cuánta evidencia devolvió
    # cada uno. Una comparación de dos documentos a la que le falta un lado
    # tiene que poder saberse incompleta: en H15 el agente afirmó que dos
    # posturas "coinciden plenamente" sin haber recuperado una de las dos.
    cobertura_por_documento: list[dict] = field(default_factory=list)

    # Menciones naturales de expediente resueltas a identidades reales del
    # universo, antes de llamar cualquier herramienta. "677/2024" no es un
    # identificador: `677_2024_1SCJN` sí. Se conservan los candidatos y si la
    # mención era ambigua, porque un número compartido por dos órganos son dos
    # asuntos y no se elige uno en silencio.
    identidades_resueltas: list[dict] = field(default_factory=list)

    # Reparación aplicada al borrador antes de emitirlo (C06): qué marcadores
    # no resolvían y cuántas afirmaciones colgaban sólo de ellos.
    reparacion_salida: dict | None = None

    # Enlaces de FUENTES a la ficha del expediente: cuántos se pusieron y
    # cuáles no, con el motivo.
    enlaces_fuentes: dict | None = None

    # Requisitos verificables de la pregunta (C03): qué documento, qué voz y
    # si exige comparar dos fuentes. Se comprueban contra la evidencia
    # identificada, no contra una bolsa de palabras — el check léxico descarta
    # los números de expediente y aprobaba una pregunta sobre un documento
    # exacto con vocabulario de cualquier otro del mismo tema.
    requisitos: list[dict] = field(default_factory=list)
    requisitos_verificados: dict | None = None

    # Toda la evidencia recuperada en el turno, no sólo la de la última
    # herramienta.
    #
    # Lo encontró COFECE leyendo el código: `requisitos_verificados` se
    # calculaba contra el `result` de la llamada en curso y se sobrescribía.
    # Como H14 llama `buscar_expedientes` **y** `buscar_criterios`, la que
    # corriera al final decidía el veredicto: si los criterios iban después,
    # el requisito de `campos_registro` —que se cumple porque el registro trajo
    # `relatedTccCaseFile`— volvía a leerse como incumplido, y el agente
    # recibía la orden de buscar algo que ya tenía.
    #
    # Un requisito satisfecho no puede dejar de estarlo porque después se
    # buscara otra cosa. La verificación se hace sobre esta acumulación.
    evidencia_acumulada: list[dict] = field(default_factory=list)

    # Peticiones HTTP de recuperación gastadas en el turno.
    #
    # No es `tool_calls_count`. Una llamada a `buscar_criterios` sobre dos
    # documentos hace dos peticiones, porque el endpoint acepta un solo
    # `caseLink`. COFECE lo señaló en §1.7 y el punto ciego lo introdujimos
    # nosotros al hacer la búsqueda por documento.
    peticiones_http: int = 0
    # Ampliación dentro del precedente mejor rankeado: una vez por turno.
    amplio_precedente: bool = False
    ampliacion_precedente: list[dict] = field(default_factory=list)
    # Resultado del verificador semántico (I5). En evaluación: se guarda
    # para medirlo, no condiciona la publicación.
    verificacion_semantica: dict | None = None
    # Documentos que quedaron sin consultar por presupuesto. Van aparte para
    # que una comparación incompleta no se lea como una comparación.
    recortes_por_presupuesto: list[dict] = field(default_factory=list)

    def acumular_evidencia(self, docs) -> int:
        """
        Agrega documentos recuperados, sin repetir. Devuelve cuántos son nuevos.

        Deduplica por expediente + identificador del fragmento: el mismo
        criterio puede volver en dos búsquedas distintas, y contarlo dos veces
        no agrega evidencia pero sí ensucia el conteo de cobertura.
        """
        from core.fuentes import case_link_de

        vistos = {
            (case_link_de(d), str(d.get("id") or d.get("caseLink") or ""))
            for d in self.evidencia_acumulada
        }
        nuevos = 0
        for d in docs or []:
            if not isinstance(d, dict):
                continue
            clave = (case_link_de(d), str(d.get("id") or d.get("caseLink") or ""))
            if clave in vistos:
                continue
            vistos.add(clave)
            self.evidencia_acumulada.append(d)
            nuevos += 1
        return nuevos
