"""
Registry de adaptadores LLM.
"""
from llm.base import BaseLLMAdapter
from models.schemas import ModelInfo


class LLMRegistry:

    def __init__(self):
        self._adapters: dict[str, BaseLLMAdapter] = {}

    def register(self, provider: str, adapter: BaseLLMAdapter):
        self._adapters[provider] = adapter

    def get_adapter(self, provider: str) -> BaseLLMAdapter:
        if provider not in self._adapters:
            raise ValueError(
                f"Proveedor '{provider}' no registrado. "
                f"Disponibles: {list(self._adapters.keys())}"
            )
        return self._adapters[provider]

    def all_models(self) -> list[ModelInfo]:
        models = []
        for adapter in self._adapters.values():
            models.extend(adapter.supported_models())
        return models

    @property
    def providers(self) -> list[str]:
        return list(self._adapters.keys())
