"""
Caché de contexto del proveedor (20-oct-2026).

Claude cobra la entrada leída de caché a 0.1× y la escrita a 1.25×, pero sólo
si la petición la pide; OpenAI la aplica sola. Los conteos de tokens no
cambian de significado: `input_tokens` sigue siendo el total procesado, que
es lo que se compromete en la cotización.
"""
import asyncio
from types import SimpleNamespace

import pytest


class TestCosto:

    def test_sin_cache_igual_que_antes(self):
        from core.costos import costo_estimado
        assert costo_estimado("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.00)

    def test_lectura_de_cache_a_su_precio(self):
        from core.costos import costo_estimado
        # 1M procesados, 800k leídos de caché: 200k a 2.00 + 800k a 0.20
        assert costo_estimado("claude-sonnet-5-5", 1_000_000, 0,
                              cache_lectura=800_000) == pytest.approx(0.56)

    def test_escritura_cuesta_mas(self):
        from core.costos import costo_estimado
        assert costo_estimado("claude-sonnet-5-5", 1_000_000, 0,
                              cache_escritura=1_000_000) == pytest.approx(2.50)

    def test_modelo_sin_precio_de_cache_se_cobra_completo(self):
        from core.costos import costo_estimado
        assert costo_estimado("gpt-5.6-terra", 1_000_000, 0,
                              cache_lectura=900_000) == pytest.approx(2.00)


class _Mensajes:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kw):
        self.kwargs = kw
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="ok",
                                     model_dump=lambda **k: {"type": "text", "text": "ok"})],
            usage=SimpleNamespace(input_tokens=100, output_tokens=5,
                                  cache_creation_input_tokens=2_000,
                                  cache_read_input_tokens=30_000))


def _adaptador():
    from llm.anthropic_adapter import AnthropicAdapter
    ad = AnthropicAdapter.__new__(AnthropicAdapter)
    ad.client = SimpleNamespace(messages=_Mensajes())
    return ad


class TestAnthropic:

    def _llamar(self):
        ad = _adaptador()
        r = asyncio.run(ad.completion_with_tools(
            messages=[{"role": "system", "content": "SIS"},
                      {"role": "user", "content": "hola"}],
            model="claude-sonnet-5-5", tools=[]))
        return r, ad.client.messages.kwargs

    def test_pide_cache_en_el_sistema_y_al_final(self):
        _, kw = self._llamar()
        assert kw["cache_control"] == {"type": "ephemeral"}
        assert kw["system"] == [{"type": "text", "text": "SIS",
                                 "cache_control": {"type": "ephemeral"}}]

    def test_input_tokens_es_el_total_procesado(self):
        r, _ = self._llamar()
        assert r.input_tokens == 32_100
        assert r.cache_lectura_tokens == 30_000
        assert r.cache_escritura_tokens == 2_000


def test_openai_reporta_lo_leido_de_cache():
    from llm.openai_adapter import _cache_leido
    uso = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=1_234))
    assert _cache_leido(uso) == 1_234
    assert _cache_leido(None) == 0
    assert _cache_leido(SimpleNamespace(prompt_tokens_details=None)) == 0


def test_escritura_de_una_hora_cuesta_el_doble():
    from core.costos import costo_estimado
    assert costo_estimado("claude-sonnet-5-5", 1_000_000, 0, cache_escritura=1_000_000,
                          cache_escritura_1h=1_000_000) == pytest.approx(4.00)


def test_el_sistema_del_agente_dura_una_hora_y_el_del_lector_cinco_minutos():
    ad = _adaptador()
    asyncio.run(ad.completion_with_tools(
        messages=[{"role": "system", "content": "SIS"}, {"role": "user", "content": "x"}],
        model="claude-sonnet-5-5",
        tools=[{"name": "t", "description": "d", "parameters": {"type": "object"}}]))
    assert ad.client.messages.kwargs["system"][0]["cache_control"]["ttl"] == "1h"
    ad = _adaptador()
    asyncio.run(ad.completion_with_tools(
        messages=[{"role": "system", "content": "SIS"}, {"role": "user", "content": "x"}],
        model="claude-sonnet-5-5", tools=[], solo_texto=True))
    assert "ttl" not in ad.client.messages.kwargs["system"][0]["cache_control"]
