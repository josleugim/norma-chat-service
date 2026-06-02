# Norma+ Chat Agent — Plan de Pruebas

## Resumen

3 niveles de pruebas, de menor a mayor complejidad:

| Nivel | Qué prueba | Requiere | Tiempo |
|-------|-----------|----------|--------|
| **1. Unit** | Lógica interna de cada componente | Nada (offline) | ~5 seg |
| **2. Integration** | Clientes HTTP contra mock server | Mock server en :3000 | ~10 seg |
| **3. E2E** | Agente completo con LLM + mock API | Mock :3000 + Chat :8000 + API key LLM | ~3 min |

---

## Nivel 1 — Tests Unitarios (offline)

Prueban la lógica pura sin red ni LLM.

### Qué cubren

- **Parseo de respuesta de criterios**: campos de primer nivel (`caseLink`, `caseName`, `articleNames`, `titleNames`), fallbacks cuando `titleNames=[]` o `None`, conversión `distance→score`, preservación de `grounding`.
- **CitationBuilder**: indexación con 1 tool call, indexación con múltiples tool calls (último bloque preferido), citas de expedientes, índices inválidos, deduplicación.
- **EvidenceCache**: sesión vacía, lookup por ID de expediente, referencia conversacional ("ese caso"), aislamiento entre sesiones.
- **ExpedienteRecord**: `agentFines` como string/dict/{}/None, conversión de fechas DD-MM-YYYY→ISO, `economicAgents` como array, `relevantMarkets` como string/list/null.
- **Agent helpers**: `_prepare_messages_for_stream` con mensajes system, Anthropic tool_result (content como lista), OpenAI tool role, assistant con content=None, `_append_user_message` con alternancia Anthropic.
- **searchData behavior**: case-insensitive, unaccent, typos no matchean.

### Cómo ejecutar

```bash
cd chat-service
pip install pytest pytest-asyncio --break-system-packages
python -m pytest tests/test_unit.py -v
```

### Resultado esperado

```
tests/test_unit.py::TestCriteriosResponseParsing::test_caselink_from_top_level PASSED
tests/test_unit.py::TestCriteriosResponseParsing::test_titlenames_empty_falls_back_to_anchor PASSED
...
tests/test_unit.py::TestCitationBuilder::test_multiple_tool_calls_last_block_preferred PASSED
...
tests/test_unit.py::TestAgentMessagePreparation::test_anthropic_tool_result_list_content PASSED
...
~30 tests, todos PASSED
```

---

## Nivel 2 — Tests de Integración (mock server)

Prueban que los clientes HTTP parseen correctamente la respuesta del mock que replica el formato exacto de la API de José Miguel.

### Qué cubren

- **CriteriosSearchClient**: búsqueda retorna resultados, cada resultado tiene `caseLink` y `nombre_expediente`, `titleNames` como fallback, `articleNames` como array, score en rango [0,1], `grounding` presente.
- **EstadisticaSearchClient**: `searchData` encuentra por agente/mercado/nombre de caso, case-insensitive (ILIKE), filtro por autoridad, filtro por sentido de resolución, manejo de `agentFines` mixtos, paginación.
- **Mock server format**: respuesta de vector-search con campos de primer nivel, metadata con anchor/context/grounding, cases/search con `searchData` y estructura paginada.

### Cómo ejecutar

```bash
# Terminal 1 — levantar mock
cd chat-service
uvicorn mock_search_server:app --port 3000

# Terminal 2 — correr tests
cd chat-service
python -m pytest tests/test_integration.py -v
```

### Resultado esperado

```
tests/test_integration.py::TestCriteriosClientIntegration::test_basic_search PASSED
tests/test_integration.py::TestCriteriosClientIntegration::test_response_has_caselink PASSED
...
tests/test_integration.py::TestEstadisticaClientIntegration::test_searchdata_cross_field PASSED
tests/test_integration.py::TestEstadisticaClientIntegration::test_searchdata_case_insensitive PASSED
...
tests/test_integration.py::TestMockServerFormat::test_vector_search_top_level_fields PASSED
...
~20 tests, todos PASSED
```

---

## Nivel 3 — Tests End-to-End (agente completo)

Prueban el flujo completo: usuario → SSE endpoint → agente → tool calling → LLM → respuesta con citas.

### Qué cubren

| # | Query | Verifica |
|---|-------|----------|
| 0 | Criterios de mercado relevante | Tool `buscar_criterios`, citas [C1] con `caseLink` y `titleNames` |
| 1 | Operaciones de Scotiabank | Tool `buscar_expedientes` via `searchData`, citas [E1] |
| 2 | Plazos IO-001-2019 | Tool `calcular_plazos`, días hábiles |
| 3 | Expedientes con multas | Filtro `has_multas`, parseo de `agentFines` string |
| 4 | Barreras + condicionadas | Múltiples tools, citation builder con bloques |
| 5 | Mercado telecomunicaciones | `searchData` por mercado relevante |
| 6 | Follow-up "ese caso" | Continuidad conversacional via EvidenceCache |
| 7 | Comparación CFC vs COFECE | Estrés: puede agotar tool calls → fallback `exhausted_tools` |

### Cómo ejecutar

```bash
# Terminal 1 — mock server
cd chat-service
uvicorn mock_search_server:app --port 3000

# Terminal 2 — chat service (apuntando al mock)
cd chat-service
SEARCH_API_BASE_URL=http://localhost:3000 \
SEARCH_API_KEY=1.test-key \
OPENAI_API_KEY=sk-proj-... \
uvicorn main:app --port 8000

# Terminal 3 — ejecutar pruebas
cd chat-service
python test_agent.py openai        # todas las queries con OpenAI
python test_agent.py anthropic     # todas con Anthropic
python test_agent.py openai 0      # solo query #0
python test_agent.py openai 7      # solo query #7 (estrés)
```

### Criterios de éxito

- **PASS**: tiene texto de respuesta y no tiene errores
- **WARN**: `exhausted_tools=true` (query #7 es esperado)
- **FAIL**: sin texto o con errores

### Resultado esperado

```
📊 RESUMEN DE PRUEBAS
═══════════════════════════════════════════
  [PASS] #0 ¿Qué criterios ha usado COFECE para definir mercado r...
         tools=1 refs=3 tokens=1200→450
  [PASS] #1 ¿En qué operaciones ha participado Scotiabank?...
         tools=1 refs=2 tokens=1100→380
  [PASS] #2 ¿Cuánto tardó en resolverse el expediente IO-001-2019...
         tools=2 refs=1 tokens=1300→290
  [PASS] #3 ¿Qué expedientes han tenido multas y cuánto pagaron?...
         tools=1 refs=3 tokens=1200→500
  [PASS] #4 ¿Qué criterios de barreras a la entrada aplicó COFEC...
         tools=2 refs=4 tokens=1500→600
  [PASS] #5 ¿Qué casos involucran el mercado de telecomunicacione...
         tools=1 refs=2 tokens=1100→350
  [PASS] #6 ¿Y en ese caso hubo multas?...
         tools=1 refs=1 tokens=900→200
  [PASS] #7 Compara las resoluciones de CFC vs COFECE... (⚠️ exhausted_tools)
         tools=6 refs=5 tokens=3000→800
```

---

## Nivel 3b — Tests contra API Real de José Miguel

Misma mecánica que nivel 3 pero apuntando a la API real en Cloud Run.

```bash
# Terminal 1 — chat service con API real
cd chat-service
SEARCH_API_BASE_URL=https://norma-api-791270202407.us-central1.run.app \
SEARCH_API_KEY=1.e32b6ae2b94d0681c1eb9cd73b65c7a48560d42ae96f1f0a8314f34c5fffa591 \
OPENAI_API_KEY=sk-proj-... \
uvicorn main:app --port 8000

# Terminal 2
cd chat-service
python test_agent.py openai
```

### Qué verificar específicamente con la API real

1. **caseLink presente**: cada referencia [C1] debe tener un id_expediente no vacío
2. **titleNames vs anchor**: verificar que el título en la referencia sea un tema descriptivo (e.g. "Mercado relevante") y no un anchor genérico
3. **articleNames como array**: los artículos deben ser legibles (e.g. "Artículo 127, LFCE (2014)")
4. **searchData funciona**: la query #1 (Scotiabank) y #5 (telecomunicaciones) deben retornar resultados
5. **Latencia**: cada query debería completarse en <30s (sin rate limiting de Anthropic)

---

## Checklist rápido de validación

```
□ python -m pytest tests/test_unit.py -v                    → todos PASSED
□ uvicorn mock_search_server:app --port 3000                 → health OK
□ python -m pytest tests/test_integration.py -v              → todos PASSED
□ python test_agent.py openai                                → todas PASS (o WARN #7)
□ python test_agent.py anthropic                             → todas PASS
□ Repetir test_agent.py con SEARCH_API_BASE_URL apuntando   → caseLink no vacío,
  a la API real de José Miguel                                 titleNames descriptivos
```
