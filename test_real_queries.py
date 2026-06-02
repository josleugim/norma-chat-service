"""
Pruebas con datos reales — consultas diseñadas para verificar
que el agente funciona correctamente contra la API de José Miguel.

Cada query tiene:
- La pregunta en lenguaje natural (como la haría un abogado)
- Qué tools debería llamar el agente
- Qué verificar en la respuesta

Ejecutar:
  cd chat-service
  uvicorn main:app --port 8000    ← con .env apuntando a API real
  python test_real_queries.py openai
  python test_real_queries.py anthropic
"""
import httpx
import json
import sys
import re
from typing import Optional

BASE_URL = "http://localhost:8000"

# Colores
C = "\033[96m"; G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"
M = "\033[95m"; B = "\033[1m"; D = "\033[2m"; X = "\033[0m"


# ═══════════════════════════════════════════════════════════════
# Definición de pruebas
# ═══════════════════════════════════════════════════════════════

TESTS = [
    # ─── BLOQUE 1: Criterios analíticos (buscar_criterios) ────

    {
        "id": "C1",
        "name": "Definición de mercado relevante",
        "query": "¿Cómo define COFECE el mercado relevante en sus resoluciones?",
        "expect_tools": ["buscar_criterios"],
        "expect_in_response": ["mercado relevante"],
        "expect_citations": "C",
        "expect_references_type": "criterio",
        "description": "Concepto fundamental. La búsqueda vectorial debe encontrar "
                       "criterios relacionados con mercado relevante.",
    },
    # {
    #     "id": "C2",
    #     "name": "Barreras a la entrada",
    #     "query": "¿Qué tipos de barreras a la entrada ha identificado COFECE?",
    #     "expect_tools": ["buscar_criterios"],
    #     "expect_in_response": ["barreras"],
    #     "expect_citations": "C",
    #     "expect_references_type": "criterio",
    #     "description": "Concepto analítico clave en concentraciones.",
    # },
    # {
    #     "id": "C3",
    #     "name": "Poder sustancial de mercado",
    #     "query": "¿Qué factores analiza COFECE para determinar si un agente económico tiene poder sustancial de mercado?",
    #     "expect_tools": ["buscar_criterios"],
    #     "expect_in_response": ["poder sustancial", "mercado"],
    #     "expect_citations": "C",
    #     "expect_references_type": "criterio",
    #     "description": "Debe citar artículo 60 o criterios de la LFCE sobre PSM.",
    # },
    # {
    #     "id": "C4",
    #     "name": "Eficiencias en concentraciones",
    #     "query": "¿Cuándo acepta COFECE un argumento de eficiencias para autorizar una concentración?",
    #     "expect_tools": ["buscar_criterios"],
    #     "expect_in_response": ["eficiencia"],
    #     "expect_citations": "C",
    #     "expect_references_type": "criterio",
    #     "description": "Tema doctrinal: eficiencias como defensa en concentraciones.",
    # },

    # # ─── BLOQUE 2: Expedientes con searchData ────────────────

    # {
    #     "id": "E1",
    #     "name": "Búsqueda por agente económico",
    #     "query": "¿En qué expedientes ha participado Walmart?",
    #     "expect_tools": ["buscar_expedientes"],
    #     "expect_citations": "E",
    #     "expect_references_type": "estadistica",
    #     "expect_in_response": ["Walmart"],
    #     "description": "searchData debe encontrar a Walmart en economicAgents.",
    # },
    # {
    #     "id": "E2",
    #     "name": "Búsqueda por mercado relevante",
    #     "query": "¿Qué expedientes de concentración involucran el mercado de telecomunicaciones?",
    #     "expect_tools": ["buscar_expedientes"],
    #     "expect_citations": "E",
    #     "expect_references_type": "estadistica",
    #     "expect_in_response": ["telecomunicaciones"],
    #     "description": "searchData busca en relevantMarkets.",
    # },
    # {
    #     "id": "E3",
    #     "name": "Concentraciones condicionadas",
    #     "query": "¿Cuáles han sido las concentraciones condicionadas por COFECE en los últimos 5 años?",
    #     "expect_tools": ["buscar_expedientes"],
    #     "expect_citations": "E",
    #     "expect_references_type": "estadistica",
    #     "expect_in_response": ["condicionada"],
    #     "description": "Filtro por sentido de resolución + rango de años.",
    # },
    # {
    #     "id": "E4",
    #     "name": "Expedientes con multas",
    #     "query": "¿Qué concentraciones no notificadas han sido sancionadas con multa?",
    #     "expect_tools": ["buscar_expedientes"],
    #     "expect_citations": "E",
    #     "expect_references_type": "estadistica",
    #     "expect_in_response": ["multa", "sanción"],
    #     "description": "Debe filtrar por tipo_procedimiento='Concentración no notificada' "
    #                    "y has_multas=true o senseOfResolution con SANCIÓN.",
    # },
    # {
    #     "id": "E5",
    #     "name": "Expediente específico por caseLink",
    #     "query": "Dame los detalles del expediente CNT-095-2013",
    #     "expect_tools": ["buscar_expedientes"],
    #     "expect_citations": "E",
    #     "expect_references_type": "estadistica",
    #     "expect_in_response": ["CNT-095-2013"],
    #     "description": "Búsqueda exacta por ID de expediente.",
    # },

    # # ─── BLOQUE 3: Plazos y cálculos temporales ──────────────

    # {
    #     "id": "T1",
    #     "name": "Plazo de un expediente específico",
    #     "query": "¿Cuánto tiempo tardó en resolverse la concentración CNT-095-2013 en días hábiles?",
    #     "expect_tools": ["buscar_expedientes", "calcular_plazos"],
    #     "expect_in_response": ["días hábiles", "CNT-095-2013"],
    #     "description": "Debe buscar el expediente primero y luego calcular plazos.",
    # },
    # {
    #     "id": "T2",
    #     "name": "Promedio de plazos en concentraciones",
    #     "query": "¿Cuál es el promedio de tiempo de resolución de concentraciones de COFECE en 2023 en días hábiles?",
    #     "expect_tools": ["buscar_expedientes", "calcular_plazos"],
    #     "expect_in_response": ["promedio", "días hábiles"],
    #     "description": "Requiere buscar múltiples expedientes + stats.",
    # },

    # # ─── BLOQUE 4: Consultas cruzadas (criterios + expedientes) ──

    # {
    #     "id": "X1",
    #     "name": "Criterios aplicados en caso específico",
    #     "query": "¿Qué criterios de mercado relevante aplicó COFECE en concentraciones del sector telecomunicaciones?",
    #     "expect_tools": ["buscar_criterios"],
    #     "expect_citations": "C",
    #     "expect_in_response": ["mercado relevante", "telecomunicaciones"],
    #     "description": "Búsqueda vectorial con filtro temático.",
    # },
    # {
    #     "id": "X2",
    #     "name": "Análisis argumental completo",
    #     "query": "Un cliente quiere notificar una concentración en el sector farmacéutico. ¿Qué criterios de mercado relevante y barreras a la entrada ha aplicado COFECE en ese sector?",
    #     "expect_tools": ["buscar_criterios"],
    #     "expect_citations": "C",
    #     "expect_in_response": ["farmacéutic"],
    #     "description": "Consulta de asesoría — debe dar criterios aplicables al sector.",
    # },

    # # ─── BLOQUE 5: Continuidad conversacional ─────────────────

    # {
    #     "id": "S1",
    #     "name": "Primera pregunta de secuencia",
    #     "query": "¿Qué expedientes de COFECE involucran a CEMEX?",
    #     "session_id": "test-continuity-real",
    #     "expect_tools": ["buscar_expedientes"],
    #     "expect_citations": "E",
    #     "expect_in_response": ["CEMEX"],
    #     "description": "Setup para la pregunta de follow-up.",
    # },
    # {
    #     "id": "S2",
    #     "name": "Follow-up conversacional",
    #     "query": "¿Y en ese caso hubo multas? ¿De cuánto?",
    #     "session_id": "test-continuity-real",
    #     "is_followup": True,
    #     "expect_in_response": ["multa"],
    #     "description": "Debe usar EvidenceCache para resolver 'ese caso' "
    #                    "sin nueva búsqueda, o buscar con el contexto previo.",
    # },

    # # ─── BLOQUE 6: Estrés ────────────────────────────────────

    # {
    #     "id": "Z1",
    #     "name": "Consulta amplia (potencial exhausted_tools)",
    #     "query": "Haz una comparación completa de cómo la CFC y COFECE han tratado las concentraciones condicionadas: criterios usados, sectores más frecuentes, multas aplicadas y tiempos promedio de resolución.",
    #     "expect_tools": ["buscar_criterios", "buscar_expedientes"],
    #     "allow_exhausted_tools": True,
    #     "description": "Consulta deliberadamente amplia. Puede agotar las 6 tool calls. "
    #                    "Lo importante es que dé una respuesta (aunque sea parcial) "
    #                    "gracias al fallback exhausted_tools.",
    # },
]


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════

def run_test(test: dict, provider: str, model: str, prev_history: list = None):
    """Ejecuta una query y valida las expectativas."""
    query = test["query"]
    session_id = test.get("session_id", f"test-{test['id']}")
    is_followup = test.get("is_followup", False)

    print(f"\n{'━'*70}")
    print(f"{B}{C}[{test['id']}] {test['name']}{X}")
    print(f"{D}   {query}{X}")
    print(f"{'━'*70}")

    payload = {
        "session_id": session_id,
        "query": query,
        "provider": provider,
        "model": model,
        "chat_history": prev_history or [],
        "is_first_message": not is_followup,
    }

    result = {
        "text": "",
        "tools_used": [],
        "references": [],
        "tokens_in": 0, "tokens_out": 0,
        "tool_calls": 0,
        "exhausted_tools": False,
        "errors": [],
    }

    try:
        with httpx.stream(
            "POST", f"{BASE_URL}/api/chat/completions",
            json=payload, timeout=120.0,
        ) as response:
            if response.status_code != 200:
                result["errors"].append(f"HTTP {response.status_code}")
                print(f"{R}   Error HTTP {response.status_code}: {response.text[:200]}{X}")
                return result, []

            event_type = ""
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event_type = line[7:].strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue

                    if event_type == "thinking":
                        tool = data.get("tool", "")
                        desc = data.get("description", "")
                        if tool and tool != "_synthesis":
                            result["tools_used"].append(tool)
                        print(f"{Y}   🔧 [{tool}] {desc}{X}")

                    elif event_type == "token":
                        result["text"] += data.get("text", "")

                    elif event_type == "references":
                        result["references"] = data.get("items", [])

                    elif event_type == "done":
                        result["tokens_in"] = data.get("tokens_input", 0)
                        result["tokens_out"] = data.get("tokens_output", 0)
                        result["tool_calls"] = data.get("tool_calls_count", 0)
                        result["exhausted_tools"] = data.get("exhausted_tools", False)

                    elif event_type == "error":
                        result["errors"].append(data.get("message", "?"))

    except httpx.ConnectError:
        result["errors"].append("Connection refused")
        print(f"{R}   ❌ No se pudo conectar a {BASE_URL}{X}")
        return result, []
    except Exception as e:
        result["errors"].append(str(e))

    # Mostrar respuesta (truncada)
    text_preview = result["text"][:400].replace("\n", "\n   ")
    print(f"\n   {text_preview}")
    if len(result["text"]) > 400:
        print(f"{D}   ... ({len(result['text'])} chars total){X}")

    # Mostrar referencias
    if result["references"]:
        print(f"\n{M}   📚 {len(result['references'])} referencias:{X}")
        for ref in result["references"][:5]:
            src = ref.get("source_type", "?")
            exp = ref.get("id_expediente", "—")
            title = ref.get("title", "") or ref.get("nombre_expediente", "")
            print(f"   {'🟢' if src=='criterio' else '🔵'} [{src[0].upper()}] {exp} — {title[:60]}")

    # ── Validaciones ──
    print(f"\n{B}   Validaciones:{X}")
    checks = []

    # 1. Tiene texto
    has_text = len(result["text"].strip()) > 50
    checks.append(("Respuesta con texto", has_text))

    # 2. No tiene errores
    no_errors = len(result["errors"]) == 0
    checks.append(("Sin errores", no_errors))

    # 3. Tools esperadas
    if test.get("expect_tools"):
        for tool in test["expect_tools"]:
            found = tool in result["tools_used"]
            checks.append((f"Usó {tool}", found))

    # 4. Palabras esperadas en respuesta
    if test.get("expect_in_response"):
        text_lower = result["text"].lower()
        for word in test["expect_in_response"]:
            found = word.lower() in text_lower
            checks.append((f"Menciona '{word}'", found))

    # 5. Citas del tipo esperado
    if test.get("expect_citations"):
        ctype = test["expect_citations"]
        pattern = rf"\[{ctype}\d+\]"
        has_cites = bool(re.search(pattern, result["text"]))
        checks.append((f"Tiene citas [{ctype}N]", has_cites))

    # 6. Referencias del tipo esperado
    if test.get("expect_references_type"):
        rtype = test["expect_references_type"]
        has_refs = any(r.get("source_type") == rtype for r in result["references"])
        checks.append((f"Referencias tipo '{rtype}'", has_refs))

    # 7. Referencias tienen caseLink (no vacío)
    if result["references"]:
        has_caselink = any(r.get("id_expediente", "").strip() for r in result["references"])
        checks.append(("Referencias con id_expediente", has_caselink))

    # 8. Si es criterio, tiene título
    if test.get("expect_references_type") == "criterio":
        has_title = any(r.get("title", "").strip() for r in result["references"])
        checks.append(("Criterios con título", has_title))

    # 9. exhausted_tools
    if test.get("allow_exhausted_tools"):
        if result["exhausted_tools"]:
            checks.append(("Fallback exhausted_tools", has_text))  # OK si tiene texto
        # else: no exhausted, aun mejor
    elif result["exhausted_tools"]:
        checks.append(("⚠ exhausted_tools inesperado", False))

    # Mostrar resultados
    all_passed = True
    for label, passed in checks:
        icon = f"{G}✓{X}" if passed else f"{R}✗{X}"
        print(f"   {icon} {label}")
        if not passed:
            all_passed = False

    status = "PASS" if all_passed else "FAIL"
    color = G if all_passed else R
    print(f"\n   {color}{B}→ {status}{X} | tools={result['tool_calls']} refs={len(result['references'])} "
          f"tokens={result['tokens_in']}→{result['tokens_out']}")

    # Armar historial para follow-ups
    history = prev_history or []
    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": result["text"][:2000]})

    return result, history


def check_health():
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=5)
        d = r.json()
        print(f"{G}✓{X} Chat service en {BASE_URL} — providers: {d.get('providers', [])}")
        return True
    except Exception:
        print(f"{R}✗{X} Chat service no responde en {BASE_URL}")
        return False


if __name__ == "__main__":
    provider = sys.argv[1] if len(sys.argv) > 1 else "openai"
    single_id = sys.argv[2] if len(sys.argv) > 2 else None
    model_map = {"openai": "gpt-4.1", "anthropic": "claude-sonnet-4-20250514"}
    model = model_map.get(provider, "gpt-4.1")

    print(f"\n{B}═══ Norma+ Chat Agent — Pruebas con datos reales ═══{X}")
    print(f"{D}Provider: {provider} | Model: {model}{X}\n")

    if not check_health():
        print(f"\n{R}Levanta el chat service: uvicorn main:app --port 8000{X}")
        sys.exit(1)

    # Filtrar tests
    tests_to_run = TESTS
    if single_id:
        tests_to_run = [t for t in TESTS if t["id"] == single_id.upper()]
        if not tests_to_run:
            print(f"{R}No se encontró test con ID '{single_id}'. IDs válidos: {[t['id'] for t in TESTS]}{X}")
            sys.exit(1)

    print(f"\nEjecutando {len(tests_to_run)} pruebas...\n")

    # Ejecutar
    summaries = []
    session_histories = {}  # para continuidad

    for test in tests_to_run:
        session_id = test.get("session_id", f"test-{test['id']}")
        prev_history = session_histories.get(session_id, [])

        result, new_history = run_test(test, provider, model, prev_history)
        session_histories[session_id] = new_history

        passed = (len(result["text"].strip()) > 50 and
                  len(result["errors"]) == 0)
        summaries.append((test["id"], test["name"], passed, result))

    # ── Resumen final ──
    print(f"\n\n{'═'*70}")
    print(f"{B}📊 RESUMEN FINAL{X}")
    print(f"{'═'*70}\n")

    total_pass = 0
    total_fail = 0
    for tid, tname, passed, result in summaries:
        color = G if passed else R
        status = "PASS" if passed else "FAIL"
        extras = []
        if result.get("exhausted_tools"):
            extras.append("exhausted_tools")
        if result.get("errors"):
            extras.append(f"errors={len(result['errors'])}")
        ext = f" ({', '.join(extras)})" if extras else ""

        print(f"  [{color}{status}{X}] {tid} — {tname}{ext}")
        if passed:
            total_pass += 1
        else:
            total_fail += 1

    print(f"\n{'─'*70}")
    print(f"  {G}{total_pass} passed{X} / {R}{total_fail} failed{X} / {len(summaries)} total")

    if total_fail == 0:
        print(f"\n  {G}{B}✅ Todas las pruebas pasaron{X}")
    else:
        print(f"\n  {Y}Revisa las pruebas fallidas arriba.{X}")
    print()
