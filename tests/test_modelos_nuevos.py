"""
Modelos nuevos (6-oct-2026): gpt-5.6-terra y Claude Sonnet 5.5.

Los dos piensan, y los dos exigen que su razonamiento vuelva sin cambios con
los resultados de herramientas. Estas pruebas fijan la forma del historial.
"""
from types import SimpleNamespace


def test_los_parametros_de_razonamiento_no_llevan_temperature():
    from llm.openai_adapter import _parametros, es_de_razonamiento
    assert es_de_razonamiento("gpt-5.6-terra") and es_de_razonamiento("o3-mini")
    assert not es_de_razonamiento("gpt-4.1")
    p = _parametros("gpt-5.6-terra", 0.3, 4096)
    assert "temperature" not in p and p["max_completion_tokens"] >= 16000
    assert _parametros("gpt-4.1", 0.3, 4096) == {"temperature": 0.3, "max_tokens": 4096}


def test_responses_reemite_el_razonamiento_una_sola_vez():
    """
    El agente parte el turno en un mensaje por herramienta; el razonamiento y
    las dos llamadas tienen que salir una vez, antes de sus resultados.
    """
    from llm.openai_adapter import a_entrada_responses
    raw = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "x"},
           {"type": "function_call", "id": "fc_a", "call_id": "A", "name": "f", "arguments": "{}"},
           {"type": "function_call", "id": "fc_b", "call_id": "B", "name": "g", "arguments": "{}"}]

    def asist(cid, name):
        return {"role": "assistant", "content": None, "_responses_output": raw,
                "tool_calls": [{"id": cid, "type": "function",
                                "function": {"name": name, "arguments": "{}"}}]}
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"},
            asist("A", "f"), {"role": "tool", "tool_call_id": "A", "content": "ra"},
            asist("B", "g"), {"role": "tool", "tool_call_id": "B", "content": "rb"}]
    instr, entrada = a_entrada_responses(msgs)
    assert instr == "S"
    tipos = [e.get("type") or e.get("role") for e in entrada]
    assert tipos == ["user", "reasoning", "function_call", "function_call",
                     "function_call_output", "function_call_output"]


def test_sin_razonamiento_guardado_se_reconstruyen_las_llamadas():
    from llm.openai_adapter import a_entrada_responses
    _, entrada = a_entrada_responses([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "A", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "A", "content": "r"}])
    assert entrada[0] == {"type": "function_call", "call_id": "A", "name": "f", "arguments": "{}"}


def test_sonnet_55_piensa_sin_temperature_y_con_drop_block():
    from llm.anthropic_adapter import _parametros
    p = _parametros("claude-sonnet-5-5", 0.3, 4096)
    assert "temperature" not in p
    assert p["thinking"]["type"] == "adaptive"
    assert p["thinking"]["block_binding"]["prefix_mismatch_behavior"] == "drop_block"
    assert p["output_config"] == {"effort": "medium"}
    assert p["max_tokens"] >= 16000


def test_ya_no_se_ofrecen_modelos_que_no_existen():
    from llm.anthropic_adapter import AnthropicAdapter
    ids = [m.model_id for m in AnthropicAdapter.__new__(AnthropicAdapter).supported_models()]
    assert ids == ["claude-sonnet-5-5"]


def test_turno_anthropic_completo_y_resultados_juntos():
    """El turno vuelve como lo produjo el modelo; los resultados, en un solo turno."""
    from agent.agent import NormaPlusAgent
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"},
           {"type": "tool_use", "id": "A", "name": "f", "input": {}},
           {"type": "tool_use", "id": "B", "name": "g", "input": {}}]
    resp = SimpleNamespace(raw_content=raw)
    tcs = [SimpleNamespace(id="A", name="f", arguments={}),
           SimpleNamespace(id="B", name="g", arguments={})]
    msgs = NormaPlusAgent._append_turno_anthropic([], resp, [(tcs[0], "ra"), (tcs[1], "rb")])
    assert msgs[0] == {"role": "assistant", "content": raw}
    assert [b["tool_use_id"] for b in msgs[1]["content"]] == ["A", "B"]
    assert len(msgs) == 2
