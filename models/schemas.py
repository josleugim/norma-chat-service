"""
Schemas Pydantic para request/response del Chat Agent Service.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional


# ── Sentido de resolución ───────────────────────────────────
# La API cambió este campo de string a arreglo (verificado el 19-sep-2026:
# 4,514 de 4,696 registros vienen como lista, 182 como null). El modelo lo
# declaraba como `str` desde junio, así que Pydantic rechazaba el registro
# COMPLETO —no solo el campo— y `estadistica_client` lo descartaba. El censo
# cargaba 182 expedientes de 4,696 y **cero VCN**: el agente contestaba que los
# expedientes no existían.
#
# Se normaliza a lista porque un expediente puede tener dos sentidos a la vez
# (`["sobresee", "niega"]`). Aplanarlos a una sola cadena volvería a mezclar
# sentidos opuestos en el matcher tolerante, que es el error que ya cometimos
# una vez: "NO SE ACREDITÓ INCUMPLIMIENTO" contra "SANCIÓN/ACREDITACIÓN DEL
# INCUMPLIMIENTO" comparten casi todas las palabras y significan lo contrario.


def sentidos_de(valor) -> list[str]:
    """Sentido(s) de resolución como lista, venga como string, lista o None."""
    if valor is None:
        return []
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return [str(valor).strip()] if str(valor).strip() else []
    return [str(v).strip() for v in valor if str(v).strip()]


def sentido_texto(valor) -> str:
    """Los sentidos como una sola cadena, sólo para mostrar o buscar texto."""
    return "; ".join(sentidos_de(valor))


# ── SSE Events ──────────────────────────────────────────────
# El agente usa StreamEvent con dicts genéricos para todos los eventos.
# Los tipos de evento son: "thinking", "token", "references", "done", "error"


class ReferenceItem(BaseModel):
    """Referencia citada en la respuesta — usado por CitationBuilder."""
    # Marcador exacto que emitió el modelo ([E3]). Cierra la cadena
    # afirmación → evidencia → registro → expediente → fuente visible.
    marker: Optional[str] = None
    id_expediente: str
    nombre_expediente: str = ""
    source_type: str  # "criterio" | "estadistica"
    relevance_score: float = 0.0
    url: str = ""

    # Campos de criterios
    title: Optional[str] = None
    parent_titles: Optional[str] = None
    paginas_parrafos: Optional[str] = None
    article: Optional[str] = None

    # Campos de estadística
    autoridad: Optional[str] = None
    tipo_procedimiento: Optional[str] = None
    sentido_resolucion: Optional[str] = None
    fecha_resolucion: Optional[str] = None


class StreamEvent(BaseModel):
    type: str  # "thinking" | "token" | "references" | "done" | "error"
    data: dict


# ── Request / Response ──────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    query: str
    provider: str = "openai"  # "openai" | "anthropic"
    model: str = "gpt-4.1"
    chat_history: list[dict] = Field(default_factory=list)
    is_first_message: bool = False

    # --- Trazabilidad (opcionales, para corridas de prueba) ---
    turn_index: Optional[int] = None      # si no viene, se infiere del historial
    question_set_id: Optional[str] = None  # id de la pregunta en la batería
    client: str = "frontend"               # "frontend" | "test_harness" | "curl"


class ModelInfo(BaseModel):
    provider: str
    model_id: str
    display_name: str


# ── Retrieval Results ───────────────────────────────────────

class CriterioResult(BaseModel):
    id: str = ""
    text: str = ""
    score: float = 0.0
    metadata: dict = Field(default_factory=dict)


class ExpedienteRecord(BaseModel):
    """
    Modelo que refleja la respuesta real de la API de casos de José Miguel.
    Los campos usan nombres en inglés camelCase para matchear la API directamente.
    """
    id: Optional[int] = None
    name: Optional[str] = None                          # Nombre del caso (e.g. "Cemex")
    caseLink: str = ""                                   # ID expediente (e.g. "VCN-001-2022")
    resolutionFileUrl: Optional[str] = None              # URL directa al PDF
    hasDigitalResolution: Optional[bool] = None
    authority: Optional[str] = None                      # "CFC" | "COFECE"
    typeOfProcedure: Optional[str] = None                # "Concentración" | "Conc. no notificada" | ...
    relevantMarkets: Optional[str | list] = None           # String o array — la API varía
    originTypeOfProcedure: Optional[str] = None
    economicAgents: Optional[list[str] | str] = None       # Array o string — la API varía
    startAgreementDate: Optional[str] = None             # DD-MM-YYYY
    notificationDate: Optional[str] = None               # DD-MM-YYYY
    basicInfoRequestDate: Optional[str] = None           # DD-MM-YYYY
    admissionDate: Optional[str] = None                  # DD-MM-YYYY
    additionalInfoRequestDate: Optional[str] = None      # DD-MM-YYYY
    resolutionDate: Optional[str] = None                 # DD-MM-YYYY
    # Arreglo desde sep-2026: ["Sanciona"], ["sobresee", "niega"]. Acepta
    # también el string suelto del vocabulario anterior. Ver `sentidos_de`.
    senseOfResolution: Optional[list[str]] = None
    resource: Optional[str] = None
    agentFines: Optional[str | dict] = None              # String con dict O dict vacío {}

    @field_validator("senseOfResolution", mode="before")
    @classmethod
    def _sentido_a_lista(cls, v):
        """
        Envuelve el string suelto del vocabulario anterior. Sin esto, un
        cambio de forma de la API descarta el expediente entero y el agente
        lo reporta como inexistente.
        """
        return sentidos_de(v) or None

    # --- Propiedades de conveniencia para el agente ---

    @property
    def id_expediente(self) -> str:
        return self.caseLink

    @property
    def autoridad(self) -> str | None:
        return self.authority

    @property
    def agentes_economicos_str(self) -> str:
        """Agentes como string para display."""
        if isinstance(self.economicAgents, list):
            return " / ".join(self.economicAgents)
        if isinstance(self.economicAgents, str):
            return self.economicAgents
        return ""

    @property
    def relevantMarkets_str(self) -> str:
        """Mercados como string para display."""
        if isinstance(self.relevantMarkets, list):
            return " / ".join(self.relevantMarkets)
        if isinstance(self.relevantMarkets, str):
            return self.relevantMarkets
        return ""

    @property
    def fecha_notificacion_iso(self) -> str | None:
        """Convierte DD-MM-YYYY a YYYY-MM-DD."""
        return self._to_iso(self.notificationDate)

    @property
    def fecha_resolucion_iso(self) -> str | None:
        return self._to_iso(self.resolutionDate)

    @property
    def fecha_admision_iso(self) -> str | None:
        return self._to_iso(self.admissionDate)

    @property
    def has_multas(self) -> bool:
        if self.agentFines is None:
            return False
        if isinstance(self.agentFines, dict):
            return bool(self.agentFines)  # {} = False, {'key': 'val'} = True
        return str(self.agentFines).strip() not in ("", "{}", "None", "null")

    def _to_iso(self, date_str: str | None) -> str | None:
        if not date_str or date_str.strip() in ("", "None", "null"):
            return None
        # DD-MM-YYYY → YYYY-MM-DD
        parts = date_str.strip().split("-")
        if len(parts) == 3 and len(parts[0]) == 2:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
        return date_str  # ya en ISO u otro formato


# ── LLM Types ───────────────────────────────────────────────

class LLMMessage(BaseModel):
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict


class LLMToolResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class LLMStreamChunk(BaseModel):
    text: str
    finish_reason: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
