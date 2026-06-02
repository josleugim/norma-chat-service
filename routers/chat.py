"""
Router del chat — endpoint SSE que el NestJS consume como proxy.
"""
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from agent.agent import NormaPlusAgent
from models.schemas import ChatRequest
from config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# El agente se inyecta desde main.py via app.state
def get_agent(settings: Settings = Depends(get_settings)) -> NormaPlusAgent:
    from main import app
    return app.state.agent


@router.post("/completions")
async def chat_completions(
    request: ChatRequest,
    agent: NormaPlusAgent = Depends(get_agent),
):
    """
    Endpoint SSE que ejecuta el agente y retorna eventos de streaming.

    Events:
    - thinking: {tool, description, step} — paso de razonamiento
    - token: {text} — token de la respuesta
    - references: {items: [...]} — referencias citadas
    - done: {tokens_input, tokens_output, tool_calls_count, session_title}
    - error: {message} — error durante la ejecución
    """

    async def event_generator() -> AsyncIterator[dict]:
        try:
            async for event in agent.run(
                session_id=request.session_id,
                user_query=request.query,
                provider=request.provider,
                model=request.model,
                chat_history=request.chat_history,
                is_first_message=request.is_first_message,
            ):
                yield {
                    "event": event.type,
                    "data": json.dumps(event.data, ensure_ascii=False),
                }
        except ValueError as e:
            # Proveedor no registrado, modelo inválido, etc.
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            }
        except Exception as e:
            logger.exception(f"Error en agente: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": f"Error interno: {str(e)}"}),
            }

    return EventSourceResponse(event_generator())


@router.get("/models")
async def list_models(
    agent: NormaPlusAgent = Depends(get_agent),
):
    """Retorna la lista de modelos disponibles."""
    models = agent.llm_registry.all_models()
    return {"models": [m.model_dump() for m in models]}
