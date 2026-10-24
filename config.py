"""
Configuración del Chat Agent Service.
Carga variables de entorno con pydantic-settings.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- LLM API Keys ---
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # --- Endpoints de José Miguel ---
    search_api_base_url: str = "https://api.normaplus.ai"
    search_api_key: str = ""  # formato: {chatbot_id}.{key}
    search_api_timeout: float = 10.0

    # --- Servicio ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:4200", "https://normaplus.ai"]

    # --- Modelo ---
    # El que se usa cuando la petición no trae `provider`/`model`. Con
    # `AGENT_MODELO_FIJO=true` se usa siempre, aunque el cliente mande otro:
    # es la forma de cambiar el modelo del sitio sin tocar el front ni el
    # proxy (19-oct, Sonnet 5.5 a pedido de COFECE).
    agent_default_provider: str = "openai"
    agent_default_model: str = "gpt-4.1"
    agent_modelo_fijo: bool = False

    # --- Agente ---
    agent_max_tool_calls: int = 6
    # Peticiones HTTP de recuperación por turno, que NO es lo mismo.
    #
    # Lo señaló COFECE en §1.7 de su revisión del 22-sep y tiene razón: una
    # llamada a `buscar_criterios` sobre dos documentos hace dos peticiones al
    # endpoint, porque el filtro acepta un solo `caseLink`. Contar sólo las
    # llamadas que elige el modelo oculta ese costo, y el punto ciego lo
    # introdujimos nosotros con la búsqueda por documento del 22-sep.
    #
    # Doce es su propuesta inicial. Cuando se agota no se calla: los
    # documentos que quedaron sin consultar se declaran en la cobertura.
    agent_max_http_requests: int = 12
    # Verificación semántica (I5), apagada por default: agrega una llamada
    # al modelo por turno y arranca en evaluación, sin bloquear nada.
    agent_verificacion_semantica: bool = False
    # Lectura de la resolución completa (17-oct). Cada lectura es una llamada
    # aparte al modelo con el documento entero (~90k tokens, ~0.18 USD con
    # gpt-4.1 la primera vez y ~0.05 con caché), así que tiene tope por turno.
    agent_leer_resolucion: bool = True
    agent_max_lecturas_resolucion: int = 3

    # --- Caché de evidencia por sesión ---
    # Sin límite crecía con cada conversación hasta que ECS mataba la tarea
    # por memoria (reporte de JM, 21-oct).
    evidence_cache_ttl_seconds: float = 4 * 3600
    evidence_cache_max_sessions: int = 500
    # Catálogo de acuerdos de suspensión para los avisos del §8.
    avisos_path: str = "data/calendario"
    agent_default_temperature: float = 0.3
    agent_max_tokens: int = 4096

    # --- Retrieval ---
    criterios_default_top_k: int = 15
    criterios_min_score: float = 0.3
    estadistica_default_limit: int = 50

    # --- Temporal ---
    holidays_path: str = "data/dias_inhabiles.xlsx"

    # --- Trazabilidad ---
    # Una corrida = un baseline congelado. Cambiar run_id abre una corrida nueva.
    tracing_enabled: bool = True
    traces_dir: str = "traces"
    # Destinos de trazas, separados por coma: jsonl (disco del contenedor; se
    # pierde al redesplegar), stdout (resumen a CloudWatch), s3 (traza completa).
    trace_sinks: str = "jsonl"

    # Conector MCP ("@norma"). Apagado por omisión: la fase 1 no tiene login.
    mcp_enabled: bool = False
    # Hosts que el conector acepta (protección contra DNS rebinding). En
    # producción: mcp.normaplus.ai.
    mcp_allowed_hosts: str = "localhost:*,127.0.0.1:*"
    # Llave de las herramientas internas (/internal/tools), que usa el servidor
    # MCP del repo aparte. Vacía = rutas sin montar.
    internal_tools_key: str = ""
    # Base de las URLs que devuelve el conector (fichas y párrafos).
    norma_web_url: str = "https://normaplus.ai"
    trace_s3_bucket: str = ""
    trace_s3_prefix: str = "trazas"
    trace_s3_region: str = "mx-central-1"
    run_id: str = "dev"
    run_label: str = ""
    question_set: str = ""
    # Guarda el texto completo de cada documento recuperado en vez de los
    # primeros 2000 caracteres. Útil para depurar un caso puntual.
    tracing_full_text: bool = False

    # --- Universo consultable ---
    # Ruta a un JSON con la lista cerrada de expedientes a los que se limita
    # TODO el acceso del agente. Vacío = acervo completo.
    #
    # Existe por el paso 01 del protocolo de holdout de COFECE, que prohíbe
    # expresamente lograr el alcance por prompt o por prefijo. Es configuración
    # del entorno, así que queda registrada en el manifiesto de la corrida y no
    # depende de que el modelo obedezca una instrucción.
    universo_path: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
