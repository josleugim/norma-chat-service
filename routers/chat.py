"""
Router del chat — endpoint SSE que el NestJS consume como proxy.
"""
import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Response
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


def resolver_modelo(request: ChatRequest, settings: Settings) -> tuple[str, str]:
    """
    El modelo con que se contesta: el de la configuración si es fijo o si la
    petición no trae uno; si no, el que pidió el cliente.

    `provider` y `model` van juntos: tomar uno del cliente y otro de la
    configuración armaría un par que no existe.
    """
    if settings.agent_modelo_fijo or not (request.provider and request.model):
        if settings.agent_modelo_fijo and request.provider and (
                (request.provider, request.model)
                != (settings.agent_default_provider, settings.agent_default_model)):
            logger.info(
                "Modelo fijo: %s/%s en lugar de %s/%s",
                settings.agent_default_provider, settings.agent_default_model,
                request.provider, request.model)
        return settings.agent_default_provider, settings.agent_default_model
    return request.provider, request.model


@router.post("/completions")
async def chat_completions(
    request: ChatRequest,
    agent: NormaPlusAgent = Depends(get_agent),
    settings: Settings = Depends(get_settings),
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

    # El turno se infiere del historial si el cliente no lo manda; es lo que
    # ordena las trazas dentro de una conversación.
    turn_index = (
        request.turn_index
        if request.turn_index is not None
        else len(request.chat_history) // 2
    )

    provider, model = resolver_modelo(request, settings)

    async def event_generator() -> AsyncIterator[dict]:
        # Último evento emitido: si el cliente se desconecta, dice en qué
        # paso iba el turno (el `thinking` trae la herramienta).
        ultimo = "inicio"
        try:
            async for event in agent.run(
                session_id=request.session_id,
                user_query=request.query,
                provider=provider,
                model=model,
                chat_history=request.chat_history,
                is_first_message=request.is_first_message,
                turn_index=turn_index,
                question_set_id=request.question_set_id,
                client=request.client,
                usuario_ref=request.usuario_ref,
                plan=request.plan,
            ):
                if event.type == "thinking":
                    ultimo = f"thinking:{event.data.get('tool') or ''}"
                elif event.type != ultimo:
                    ultimo = event.type
                yield {
                    "event": event.type,
                    "data": json.dumps(event.data, ensure_ascii=False),
                }
        except asyncio.CancelledError:
            # El cliente cerró la conexión (NestJS abortó el fetch). La
            # cancelación llega al `await` en curso del agente: no se sigue
            # buscando ni generando, y el caché no se escribe. Se registra y se
            # vuelve a lanzar: tragarla dejaría el turno corriendo.
            logger.info("Cliente desconectado: turno detenido (sesión %s, último paso %s)",
                        request.session_id, ultimo)
            raise
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


@router.get("/run")
async def run_info(
    agent: NormaPlusAgent = Depends(get_agent),
    settings: Settings = Depends(get_settings),
):
    """
    Estado de la corrida en curso: baseline congelado y drift detectado.
    Permite verificar antes de empezar que se está midiendo lo que se cree.
    """
    store = agent.manifest_store
    manifest = getattr(store, "_manifest", None) if store else None
    return {
        "tracing_enabled": settings.tracing_enabled,
        "run_id": settings.run_id,
        "traces_dir": settings.traces_dir,
        "manifest": manifest.model_dump(mode="json") if manifest else None,
    }


@router.delete("/sessions/{session_id}/cache", status_code=204, include_in_schema=False)
async def borrar_cache_de_sesion(
    session_id: str,
    agent: NormaPlusAgent = Depends(get_agent),
) -> Response:
    """
    Borra la evidencia en caché de una sesión. Lo llama la API de NestJS
    después de eliminar el chat; la verificación de propiedad se hace allá.

    Interno (Cloud Map), fuera del esquema público. Idempotente: 204 aunque
    la sesión ya no esté (expiró, se desalojó o se perdió en un reinicio).
    """
    agent.evidence_cache.clear_session(session_id)
    logger.info("Caché de sesión borrado a petición de la API (sesión %s)", session_id)
    return Response(status_code=204)
