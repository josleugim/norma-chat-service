"""
Ninguna llamada a OpenAI deja una copia guardada en la cuenta (6-oct-2026).

A COFECE se le dijo que OpenAI conserva los datos hasta 30 días por monitoreo
de abuso. Con `store=True` habría además una copia en el panel de la cuenta,
sin plazo.
"""
import asyncio
import inspect


class _Respuesta:
    """Lo mínimo que lee el adaptador de una respuesta no-streaming."""
    class _Msg:
        content = "ok"
        tool_calls = None
    class _Choice:
        pass
    usage = None

    def __init__(self):
        c = self._Choice()
        c.message = self._Msg()
        self.choices = [c]


class _Stream:
    def __aiter__(self):
        return self
    async def __anext__(self):
        raise StopAsyncIteration


class _Completions:
    def __init__(self): self.llamadas = []
    async def create(self, **kwargs):
        self.llamadas.append(kwargs)
        return _Stream() if kwargs.get("stream") else _Respuesta()


def _adaptador():
    from llm.openai_adapter import OpenAIAdapter
    ad = OpenAIAdapter.__new__(OpenAIAdapter)
    comp = _Completions()
    ad.client = type("C", (), {"chat": type("Ch", (), {"completions": comp})()})()
    return ad, comp


def test_las_tres_rutas_mandan_store_false():
    from models.schemas import LLMMessage
    ad, comp = _adaptador()
    msgs = [LLMMessage(role="user", content="hola")]

    async def correr():
        async for _ in ad.stream_completion(msgs, "gpt-4.1"):
            pass
        await ad.completion_with_tools([{"role": "user", "content": "hola"}], "gpt-4.1", [])
        await ad.quick_completion(msgs, "gpt-4.1")
    asyncio.run(correr())

    assert len(comp.llamadas) == 3
    assert all(k.get("store") is False for k in comp.llamadas)


def test_ninguna_llamada_al_sdk_se_salta_el_parametro():
    """Una cuarta llamada que se agregue sin `store` tiene que romper esto."""
    import llm.openai_adapter as m
    src = inspect.getsource(m)
    llamadas = src.count("chat.completions.create(")
    assert llamadas == src.count("store=STORE")
    assert m.STORE is False
