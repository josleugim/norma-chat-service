"""
Una pregunta de concepto contestada sin buscar sale de la memoria del modelo,
y ahí atribuía a la COFECE lo que no consta en el acervo (17-oct, 3 de 3 en
master). Se le pide una búsqueda antes de contestar, una sola vez.
"""
import asyncio

from models.schemas import LLMToolResponse
from tests.test_tracing import FakeAdapter, build_agent, read_trace


class _Registra(FakeAdapter):
    def __init__(self, script):
        super().__init__(script)
        self.vistos = []

    async def completion_with_tools(self, messages, model, tools):
        self.vistos.append([m.get("content") for m in messages])
        return await super().completion_with_tools(messages, model, tools)


def _corre(tmp_path, pregunta, script):
    adapter = _Registra(script)
    agent, _ = build_agent(tmp_path, adapter)

    async def drain():
        return [e async for e in agent.run(
            session_id="s", user_query=pregunta, provider="openai",
            model="gpt-4.1", chat_history=[])]
    asyncio.run(drain())
    return adapter, read_trace(tmp_path)


def _respuesta(texto="De memoria."):
    return LLMToolResponse(content=texto, input_tokens=10, output_tokens=5)


def test_concepto_sin_buscar_pide_una_busqueda(tmp_path):
    adapter, traza = _corre(tmp_path, "¿Qué es el mercado relevante en competencia económica?",
                            [_respuesta(), _respuesta("Con criterios.")])
    assert adapter.calls == 2
    assert "buscar_criterios" in adapter.vistos[1][-1]
    assert traza["decisions"]["busqueda_forzada"] is True
    assert traza["answer"]["text"].startswith("Con criterios")


def test_se_pide_una_sola_vez(tmp_path):
    adapter, _ = _corre(tmp_path, "¿Qué es el poder sustancial?",
                        [_respuesta(), _respuesta(), _respuesta()])
    assert adapter.calls == 2


def test_saludo_no_dispara_busqueda(tmp_path):
    adapter, traza = _corre(tmp_path, "hola", [_respuesta("Hola.")])
    assert adapter.calls == 1
    assert not traza["decisions"].get("busqueda_forzada")
