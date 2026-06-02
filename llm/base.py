"""
Interfaz abstracta para adaptadores de LLM.
Soporta streaming y tool calling.
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.schemas import LLMMessage, LLMStreamChunk, LLMToolResponse, ModelInfo


class BaseLLMAdapter(ABC):

    @abstractmethod
    async def stream_completion(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream de tokens para la respuesta final."""
        ...

    @abstractmethod
    async def completion_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMToolResponse:
        """
        Completación con posibilidad de tool calls.
        Recibe messages en formato nativo del proveedor.
        Retorna contenido textual O tool calls.
        """
        ...

    @abstractmethod
    async def quick_completion(
        self,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int = 50,
    ) -> str:
        """Completación rápida sin streaming (para generar títulos, etc.)."""
        ...

    @abstractmethod
    def supported_models(self) -> list[ModelInfo]:
        """Lista de modelos disponibles."""
        ...
