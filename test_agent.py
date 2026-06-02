"""
Tests end-to-end del Chat Agent Service.
Envía consultas al endpoint SSE y muestra los eventos en tiempo real.

Requiere:
  1. Mock server: uvicorn mock_search_server:app --port 3000
  2. Chat service: SEARCH_API_BASE_URL=http://localhost:3000 uvicorn main:app --port 8000

Uso:
  python test_agent.py              # usa openai por default
  python test_agent.py anthropic    # usa anthropic
  python test_agent.py openai 3     # solo ejecuta la query #3
"""
import httpx
import json
import sys

BASE_URL = "http://localhost:8000"

# Colores terminal
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def test_query(query: str, provider: str = "openai", model: str = "gpt-4.1",
               session_id: str = "test-session-001"):
    """Envía una consulta al agente y muestra los eventos SSE."""
    print(f"\n{'='*70}")
    print(f"{BOLD}{CYAN}📝 Query:{RESET} {query}")
    print(f"{DIM}   Provider: {provider} | Model: {model} | Session: {session_id}{RESET}")
    print(f"{'='*70}\n")

    payload = {
        "session_id": session_id,
        "query": query,
        "provider": provider,
        "model": model,
        "chat_history": [],
        "is_first_message": True,
    }

    result = {
        "tokens_in": 0, "tokens_out": 0, "tool_calls": 0,
        "references": 0, "has_text": False, "exhausted_tools": False,
        "errors": [],
    }

    try:
        with httpx.stream(
            "POST", f"{BASE_URL}/api/chat/completions",
            json=payload, timeout=120.0,
        ) as response:
            if response.status_code != 200:
                print(f"{RED}Error HTTP {response.status_code}{RESET}")
                print(response.text)
                return result

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
                        step = data.get("step", "?")
                        tool = data.get("tool", "")
                        desc = data.get("description", "")
                        print(f"{YELLOW}🔧 Paso {step} [{tool}]:{RESET} {desc}")

                    elif event_type == "token":
                        text = data.get("text", "")
                        print(text, end="", flush=True)
                        if text.strip():
                            result["has_text"] = True

                    elif event_type == "references":
                        items = data.get("items", [])
                        result["references"] = len(items)
                        if items:
                            print(f"\n\n{MAGENTA}{'─'*50}")
                            print(f"📚 Referencias ({len(items)}):{RESET}")
                            for ref in items:
                                src = ref.get("source_type", "?")
                                exp = ref.get("id_expediente", "?")
                                url = ref.get("url", "")
                                if src == "criterio":
                                    title = ref.get("title", "")
                                    pages = ref.get("paginas_parrafos", "")
                                    article = ref.get("article", "")
                                    print(f"  {GREEN}[C]{RESET} {exp} — {title}")
                                    if pages:
                                        print(f"       pp. {pages}")
                                    if article:
                                        print(f"       {article}")
                                else:
                                    sentido = ref.get("sentido_resolucion", "")
                                    autoridad = ref.get("autoridad", "")
                                    print(f"  {CYAN}[E]{RESET} {exp} — {autoridad} — {sentido}")
                                print(f"       {DIM}{url}{RESET}")

                    elif event_type == "done":
                        result["tokens_in"] = data.get("tokens_input", 0)
                        result["tokens_out"] = data.get("tokens_output", 0)
                        result["tool_calls"] = data.get("tool_calls_count", 0)
                        result["exhausted_tools"] = data.get("exhausted_tools", False)
                        title = data.get("session_title", "")

                        print(f"\n\n{GREEN}{'─'*50}")
                        print(f"✅ Completado")
                        print(f"   Tokens: {result['tokens_in']} in / {result['tokens_out']} out")
                        print(f"   Tool calls: {result['tool_calls']}")
                        if result["exhausted_tools"]:
                            print(f"   {YELLOW}⚠️  exhausted_tools=true (se forzó síntesis){RESET}")
                        if title:
                            print(f"   Título: \"{title}\"")
                        print(f"{'─'*50}{RESET}")

                    elif event_type == "error":
                        msg = data.get("message", "?")
                        result["errors"].append(msg)
                        print(f"\n{RED}❌ Error: {msg}{RESET}")

    except httpx.ConnectError:
        print(f"{RED}❌ No se pudo conectar a {BASE_URL}")
        print(f"   ¿Está corriendo el servicio?{RESET}")
    except Exception as e:
        print(f"{RED}❌ Error: {e}{RESET}")
        result["errors"].append(str(e))

    return result


def check_health():
    """Verifica servicios."""
    print(f"{BOLD}Verificando servicios...{RESET}\n")

    # Chat service
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=5)
        data = r.json()
        providers = data.get("providers", [])
        print(f"  {GREEN}✓{RESET} Chat Agent Service → {BASE_URL}")
        print(f"    Providers: {', '.join(providers) if providers else 'ninguno ⚠️'}")
    except Exception:
        print(f"  {RED}✗{RESET} Chat Agent Service → {BASE_URL} (no responde)")
        print(f"    {DIM}Ejecuta: uvicorn main:app --port 8000{RESET}")
        return False

    # Mock server
    try:
        r = httpx.get("http://localhost:3000/health", timeout=5)
        data = r.json()
        print(f"  {GREEN}✓{RESET} Mock Search API → http://localhost:3000 ({data.get('type', '?')})")
    except Exception:
        print(f"  {YELLOW}⚠{RESET} Mock Search API → http://localhost:3000 (no responde)")
        print(f"    {DIM}Si usas la API real, ignora esto{RESET}")

    print()
    return True


# ── Consultas de prueba ─────────────────────────────────────

QUERIES = [
    # 0: Criterios vectoriales — verifica caseLink, titleNames, articleNames
    "¿Qué criterios ha usado COFECE para definir mercado relevante?",

    # 1: Expedientes via searchData — verifica búsqueda cross-field
    "¿En qué operaciones ha participado Scotiabank?",

    # 2: Temporal — plazos en días hábiles
    "¿Cuánto tardó en resolverse el expediente IO-001-2019 en días hábiles?",

    # 3: Multas — verifica parseo de agentFines
    "¿Qué expedientes han tenido multas y cuánto pagaron?",

    # 4: Compleja (criterios + expedientes) — puede triggear multiple tool calls
    "¿Qué criterios de barreras a la entrada aplicó COFECE en concentraciones condicionadas?",

    # 5: searchData por mercado relevante
    "¿Qué casos involucran el mercado de telecomunicaciones?",

    # 6: Continuidad conversacional (se ejecuta como follow-up de la 5)
    "¿Y en ese caso hubo multas?",

    # 7: Estrés — consulta que puede agotar tool calls (comparación amplia)
    "Compara las resoluciones de CFC vs COFECE en concentraciones condicionadas de los últimos 10 años",
]


if __name__ == "__main__":
    provider = sys.argv[1] if len(sys.argv) > 1 else "openai"
    single_idx = int(sys.argv[2]) if len(sys.argv) > 2 else None
    model_map = {
        "openai": "gpt-4.1",
        "anthropic": "claude-sonnet-4-20250514",
    }
    model = model_map.get(provider, "gpt-4.1")

    if not check_health():
        print(f"\n{RED}Arranca los servicios antes de ejecutar.{RESET}")
        sys.exit(1)

    queries = [(single_idx, QUERIES[single_idx])] if single_idx is not None else list(enumerate(QUERIES))
    print(f"{BOLD}Ejecutando {len(queries)} consultas con {provider}/{model}...{RESET}")

    results_summary = []
    for idx, query in queries:
        session = "test-continuity" if idx == 6 else f"test-{idx}"
        r = test_query(query, provider=provider, model=model, session_id=session)
        results_summary.append((idx, query[:60], r))
        print()

    # ── Resumen final ──
    print(f"\n{'='*70}")
    print(f"{BOLD}📊 RESUMEN DE PRUEBAS{RESET}")
    print(f"{'='*70}")

    all_passed = True
    for idx, q, r in results_summary:
        status = GREEN + "PASS" + RESET
        notes = []

        if r["errors"]:
            status = RED + "FAIL" + RESET
            notes.append(f"errores: {len(r['errors'])}")
            all_passed = False
        if not r["has_text"]:
            status = RED + "FAIL" + RESET
            notes.append("sin texto de respuesta")
            all_passed = False
        if r["exhausted_tools"]:
            notes.append("⚠️ exhausted_tools")

        notes_str = f" ({', '.join(notes)})" if notes else ""
        print(f"  [{status}] #{idx} {q}...{notes_str}")
        print(f"         {DIM}tools={r['tool_calls']} refs={r['references']} "
              f"tokens={r['tokens_in']}→{r['tokens_out']}{RESET}")

    print(f"\n{'─'*70}")
    if all_passed:
        print(f"{GREEN}{BOLD}✅ Todas las pruebas pasaron{RESET}")
    else:
        print(f"{RED}{BOLD}❌ Algunas pruebas fallaron{RESET}")
    print()
