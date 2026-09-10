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
    search_api_base_url: str = "https://api-staging.normaplus.ai"
    search_api_key: str = ""  # formato: {chatbot_id}.{key}
    search_api_timeout: float = 10.0

    # --- Servicio ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:4200", "https://normaplus.ai"]

    # --- Agente ---
    agent_max_tool_calls: int = 6
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
    run_id: str = "dev"
    run_label: str = ""
    question_set: str = ""
    # Guarda el texto completo de cada documento recuperado en vez de los
    # primeros 2000 caracteres. Útil para depurar un caso puntual.
    tracing_full_text: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
