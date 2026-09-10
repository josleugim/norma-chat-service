"""
Mock server que simula los endpoints de José Miguel.
Actualizado mayo 2026: refleja el formato real confirmado.

- /paragraphs/vector-search: caseName, caseLink, articleNames, titleNames 
  como campos de PRIMER NIVEL (fuera de metadata)
- /cases/agent-search: filtros con AND, sin paginación, meta{returned,limit}
- /cases/search: retirado (401), como en staging desde el 7-sep-2026

Ejecutar: uvicorn mock_search_server:app --port 3000
"""
import random
import unicodedata
from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="Mock Search API — Norma+")


# ── Helpers ──────────────────────────────────────────────────

def unaccent(text: str) -> str:
    """Simula PostgreSQL unaccent."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

def ilike_match(haystack: str, needle: str) -> bool:
    """Simula ILIKE + unaccent de PostgreSQL."""
    return unaccent(needle.lower()) in unaccent(haystack.lower())


# ── Datos de ejemplo ─────────────────────────────────────────

CRITERIOS_DB = [
    {
        "id": 2966,
        "content": "La finalidad de la sanción por la omisión de notificar una concentración es fundamentalmente disuasiva y su individualización debe atender al principio de proporcionalidad.",
        "metadata": {
            "anchor": "Gradación de las multas por omisión de notificación",
            "context": "En el contexto de concentraciones no notificadas, la autoridad...",
            "grounding": {"box": {"b": 0.36, "l": 0.11, "r": 0.88, "t": 0.32}, "page": 40},
            "pdf_pages": ["41"],
            "resolution_pages": ["40"],
        },
        "caseName": "Jiye",
        "caseLink": "VCN-001-2019",
        "distance": 0.52,
        "articleNames": ["Artículo 127, LFCE (2014)"],
        "titleNames": ["Gradación de las multas"],
    },
    {
        "id": 2450,
        "content": "El mercado relevante se define como la combinación del mercado de producto y el mercado geográfico. Para determinar el mercado de producto, se identifican los bienes o servicios que son razonablemente sustituibles entre sí.",
        "metadata": {
            "anchor": "Definición de mercado relevante",
            "context": "El análisis parte de identificar el mercado relevante...",
            "grounding": {"box": {"b": 0.50, "l": 0.10, "r": 0.90, "t": 0.42}, "page": 44},
            "pdf_pages": ["45"],
            "resolution_pages": ["44"],
        },
        "caseName": "Telecomunicaciones",
        "caseLink": "CNT-045-2020",
        "distance": 0.31,
        "articleNames": ["Artículo 58, LFCE (2014)"],
        "titleNames": ["Mercado relevante (definición)"],
    },
    {
        "id": 3120,
        "content": "Las barreras a la entrada son obstáculos que dificultan o impiden la participación de nuevos competidores en el mercado relevante. Pueden ser de naturaleza legal, económica o estratégica.",
        "metadata": {
            "anchor": "Barreras a la entrada en sector energético",
            "context": "Al evaluar las condiciones de competencia...",
            "grounding": None,
            "pdf_pages": ["12"],
            "resolution_pages": ["12"],
        },
        "caseName": "Sector Energético",
        "caseLink": "CNT-112-2018",
        "distance": 0.28,
        "articleNames": ["Artículo 61, LFCE (2014)"],
        "titleNames": ["Barreras a la entrada"],
    },
    {
        "id": 4890,
        "content": "La eficiencia económica se evalúa considerando si la concentración genera eficiencias que se trasladan al consumidor y que no podrían alcanzarse por medios menos restrictivos de la competencia.",
        "metadata": {
            "anchor": "Eficiencias en concentraciones",
            "context": "",
            "grounding": None,
            "pdf_pages": ["78"],
            "resolution_pages": ["78"],
        },
        "caseName": "Restaurantes",
        "caseLink": "CNT-095-2013",
        "distance": 0.35,
        "articleNames": ["Artículo 63, Fracción V, LFCE (2014)"],
        "titleNames": ["Eficiencias"],
    },
    {
        "id": 1560,
        "content": "Para determinar la existencia de poder sustancial de mercado, se consideran: participación de mercado, barreras a la entrada, existencia y poder de competidores, y acceso a insumos.",
        "metadata": {
            "anchor": "Poder sustancial de mercado",
            "context": "La Comisión analizó los factores del artículo 60...",
            "grounding": None,
            "pdf_pages": ["34"],
            "resolution_pages": ["34"],
        },
        "caseName": "Sector Financiero",
        "caseLink": "IO-003-2018",
        "distance": 0.40,
        "articleNames": ["Artículo 60, LFCE (2014)"],
        "titleNames": ["Poder sustancial de mercado"],
    },
    {
        "id": 5670,
        "content": "La concentración puede generar efectos unilaterales cuando la entidad resultante adquiere la capacidad de fijar precios de manera independiente.",
        "metadata": {
            "anchor": "", "context": "", "grounding": None,
            "pdf_pages": ["89"], "resolution_pages": ["89"],
        },
        "caseName": "Telecomunicaciones",
        "caseLink": "CNT-045-2020",
        "distance": 0.42,
        # Caso: titleNames y articleNames vacíos (confirmado por José Miguel)
        "articleNames": [],
        "titleNames": [],
    },
]

ESTADISTICA_DB = [
    {
        "id": 10001, "name": "Azúcar", "caseLink": "CNT-008-1993",
        "resolutionFileUrl": None, "authority": "CFC",
        "typeOfProcedure": "Concentración",
        "relevantMarkets": "Producción y/o distribución de azúcar",
        "originTypeOfProcedure": None,
        "economicAgents": ["XABRE", "CONSORCIO INTEGRAL DE EMPRESAS", "XAFRA"],
        "startAgreementDate": None, "notificationDate": "21-09-1993",
        "basicInfoRequestDate": None, "admissionDate": "01-10-1993",
        "additionalInfoRequestDate": None, "resolutionDate": "11-11-1993",
        "senseOfResolution": "CONDICIONADA", "resource": None, "agentFines": {},
    },
    {
        "id": 10002, "name": "Restaurantes", "caseLink": "CNT-095-2013",
        "resolutionFileUrl": None, "authority": "COFECE",
        "typeOfProcedure": "Concentración",
        "relevantMarkets": "Operación de restaurantes de servicio completo",
        "originTypeOfProcedure": None,
        "economicAgents": ["ALSEA, S.A.B. DE C.V.", "GRUPO ZENA"],
        "startAgreementDate": None, "notificationDate": "15-08-2013",
        "basicInfoRequestDate": None, "admissionDate": "30-08-2013",
        "additionalInfoRequestDate": None, "resolutionDate": "20-12-2013",
        "senseOfResolution": "AUTORIZADA", "resource": None, "agentFines": {},
    },
    {
        "id": 10003, "name": "Gasolineras", "caseLink": "IO-001-2019",
        "resolutionFileUrl": None, "authority": "COFECE",
        "typeOfProcedure": "Concentración no notificada",
        "relevantMarkets": "Distribución de combustibles zona norte",
        "originTypeOfProcedure": None,
        "economicAgents": ["Servicios Gasolineros de Mexico, S.A. de C.V.",
                          "Gasolinera Boquilla, S.A. de C.V.", "Combylub, S.A. de C.V."],
        "startAgreementDate": "25-09-2019", "notificationDate": None,
        "basicInfoRequestDate": None, "admissionDate": None,
        "additionalInfoRequestDate": None, "resolutionDate": "25-04-2024",
        "senseOfResolution": "SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO",
        "resource": None,
        "agentFines": "{'Servicios Gasolineros de Mexico, S.A. de C.V.':'$3,792,079.86'}",
    },
    {
        "id": 10004, "name": "Telecomunicaciones", "caseLink": "CNT-045-2020",
        "resolutionFileUrl": None, "authority": "COFECE",
        "typeOfProcedure": "Concentración",
        "relevantMarkets": "Servicios de telecomunicaciones fijas",
        "originTypeOfProcedure": None,
        "economicAgents": ["TELEVISIÓN INTERNACIONAL, S.A. DE C.V.", "MEGACABLE HOLDINGS"],
        "startAgreementDate": None, "notificationDate": "10-03-2020",
        "basicInfoRequestDate": None, "admissionDate": "25-03-2020",
        "additionalInfoRequestDate": None, "resolutionDate": "15-07-2020",
        "senseOfResolution": "CONDICIONADA", "resource": None, "agentFines": {},
    },
    {
        "id": 10005, "name": "Sector Energético", "caseLink": "CNT-112-2018",
        "resolutionFileUrl": None, "authority": "COFECE",
        "typeOfProcedure": "Concentración",
        "relevantMarkets": "Distribución de gas natural",
        "originTypeOfProcedure": None,
        "economicAgents": ["INFRAESTRUCTURA ENERGÉTICA NOVA, S.A.B. DE C.V."],
        "startAgreementDate": None, "notificationDate": "20-05-2018",
        "basicInfoRequestDate": None, "admissionDate": "05-06-2018",
        "additionalInfoRequestDate": None, "resolutionDate": "30-09-2018",
        "senseOfResolution": "AUTORIZADA", "resource": None, "agentFines": {},
    },
    {
        "id": 10006, "name": "Scotiabank-Credijusto", "caseLink": "CNT-200-2022",
        "resolutionFileUrl": None, "authority": "COFECE",
        "typeOfProcedure": "Concentración",
        "relevantMarkets": "Servicios financieros digitales",
        "originTypeOfProcedure": None,
        "economicAgents": ["SCOTIABANK INVERLAT, S.A.", "CREDIJUSTO"],
        "startAgreementDate": None, "notificationDate": "15-01-2022",
        "basicInfoRequestDate": None, "admissionDate": "01-02-2022",
        "additionalInfoRequestDate": None, "resolutionDate": "10-04-2022",
        "senseOfResolution": "AUTORIZADA", "resource": None, "agentFines": {},
    },
    {
        "id": 10007, "name": "Fármacos", "caseLink": "VCN-001-2018",
        "resolutionFileUrl": None, "authority": "COFECE",
        "typeOfProcedure": "Concentración no notificada",
        "relevantMarkets": "Distribución de medicamentos",
        "originTypeOfProcedure": None,
        "economicAgents": ["GRUPO FÁRMACOS ESPECIALIZADOS, S.A. DE C.V."],
        "startAgreementDate": "10-11-2018", "notificationDate": None,
        "basicInfoRequestDate": None, "admissionDate": None,
        "additionalInfoRequestDate": None, "resolutionDate": "20-08-2019",
        "senseOfResolution": "SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO",
        "resource": None,
        "agentFines": "{'GRUPO FÁRMACOS ESPECIALIZADOS, S.A. DE C.V.':'$5,000,000.00'}",
    },
]


# ── Endpoints ────────────────────────────────────────────────

@app.post("/paragraphs/vector-search")
async def search_criterios(payload: dict):
    """
    Formato real confirmado por José Miguel (mayo 2026):
    caseName, caseLink, articleNames, titleNames como PRIMER NIVEL.
    """
    query = payload.get("question", "").lower()
    top_k = payload.get("limit", 10)

    results = []
    for crit in CRITERIOS_DB:
        text_lower = crit["content"].lower()
        title_words = " ".join(crit.get("titleNames") or []).lower()
        query_words = [w for w in query.split() if len(w) > 2]
        matches = sum(1 for w in query_words if w in text_lower or w in title_words)

        if matches > 0 or not query.strip():
            noise = random.uniform(-0.03, 0.03)
            results.append({
                "id": crit["id"],
                "content": crit["content"],
                "metadata": crit["metadata"],
                "caseName": crit["caseName"],
                "caseLink": crit["caseLink"],
                "distance": round(max(0.05, crit["distance"] + noise), 4),
                "articleNames": crit.get("articleNames", []),
                "titleNames": crit.get("titleNames", []),
            })

    if not results:
        for c in CRITERIOS_DB[:3]:
            results.append({
                "id": c["id"], "content": c["content"],
                "metadata": c["metadata"], "caseName": c["caseName"],
                "caseLink": c["caseLink"],
                "distance": round(c["distance"] + random.uniform(0, 0.15), 4),
                "articleNames": c.get("articleNames", []),
                "titleNames": c.get("titleNames", []),
            })

    results.sort(key=lambda x: x["distance"])
    return results[:top_k]


@app.get("/cases/agent-search")
async def agent_search_cases(
    limit: int = 10,
    searchData: str | None = None,
    name: str | None = None,
    authority: str | None = None,
    typeOfProcedure: str | None = None,
    senseOfResolution: str | None = None,
    caseLink: str | None = None,
    economicAgents: str | None = None,
    relevantMarkets: str | None = None,
    applicableLaw: str | None = None,
    agentFines: str | None = None,
    senseOfResolutionFrom: int | None = None,
    senseOfResolutionTo: int | None = None,
):
    """
    Endpoint de sep-2026. Reproduce el comportamiento verificado contra
    staging, incluidas las asperezas, porque son justo lo que el cliente
    tiene que sortear:

    - Los filtros se combinan con AND.
    - `caseLink` es match PARCIAL, no igualdad.
    - No hay `page`; `limit` no tiene tope y su default es 10.
    - `meta` trae solo `returned` y `limit`: no hay `total`.
    - Un parámetro con nombre desconocido se ignora en silencio (FastAPI ya
      lo hace por su cuenta) y devuelve el universo sin filtrar.
    - El alias de SANCION expande a tres valores que casi no existen en los
      datos: el sentido dominante es `Sanciona`, y no lo cubre.
    """
    if (senseOfResolutionFrom is None) != (senseOfResolutionTo is None):
        raise HTTPException(
            status_code=400,
            detail="Sense of resolution is missing a date",
        )
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be positive")
    if agentFines is not None and agentFines.strip().lower() not in ("true", "false"):
        raise HTTPException(status_code=400, detail="agentFines must be true or false")

    results = list(ESTADISTICA_DB)

    if searchData:
        results = [
            r for r in results
            if any(ilike_match(f, searchData) for f in (
                r.get("caseLink") or "",
                r.get("name") or "",
                " ".join(r.get("economicAgents") or []),
                r.get("relevantMarkets") if isinstance(r.get("relevantMarkets"), str) else "",
            ))
        ]
    if name:
        results = [r for r in results if ilike_match(r.get("name") or "", name)]
    if authority:
        results = [r for r in results
                   if unaccent((r.get("authority") or "").upper()) == unaccent(authority.upper())]
    if typeOfProcedure:
        results = [r for r in results
                   if unaccent((r.get("typeOfProcedure") or "").upper()) == unaccent(typeOfProcedure.upper())]
    if senseOfResolution:
        objetivo = unaccent(senseOfResolution.upper())
        if objetivo == "SANCION":
            aceptados = {
                "SANCION",
                "SANCION/ACREDITACION DEL INCUMPLIMIENTO",
                "ACREDITACION DEL INCUMPLIMIENTO",
            }
        else:
            aceptados = {objetivo}
        results = [r for r in results
                   if unaccent((r.get("senseOfResolution") or "").upper()) in aceptados]
    if caseLink:
        # Parcial, no igualdad: es lo que permite pedir un prefijo entero.
        results = [r for r in results if ilike_match(r.get("caseLink") or "", caseLink)]
    if economicAgents:
        results = [r for r in results
                   if any(ilike_match(a, economicAgents) for a in (r.get("economicAgents") or []))]
    if relevantMarkets:
        results = [
            r for r in results
            if ilike_match(
                r.get("relevantMarkets") if isinstance(r.get("relevantMarkets"), str) else "",
                relevantMarkets,
            )
        ]
    if applicableLaw:
        results = [r for r in results if ilike_match(r.get("applicableLaw") or "", applicableLaw)]
    if agentFines is not None:
        quiere = agentFines.strip().lower() == "true"
        results = [r for r in results if bool(r.get("agentFines")) is quiere]
    if senseOfResolutionFrom:
        results = [r for r in results if r.get("resolutionDate") and
                   int(r["resolutionDate"].split("-")[-1]) >= senseOfResolutionFrom]
    if senseOfResolutionTo:
        results = [r for r in results if r.get("resolutionDate") and
                   int(r["resolutionDate"].split("-")[-1]) <= senseOfResolutionTo]

    # Resolución más reciente primero; sin fecha al final; desempate por
    # caseLink para que dos peticiones idénticas den lo mismo.
    def orden(r):
        fecha = r.get("resolutionDate") or ""
        partes = fecha.split("-")
        clave = (partes[2], partes[1], partes[0]) if len(partes) == 3 else ("",)
        return (fecha == "", tuple(-ord(c) for c in "".join(clave)), r.get("caseLink") or "")

    results.sort(key=orden)
    recortado = results[:limit]
    return {"data": recortado, "meta": {"returned": len(recortado), "limit": limit}}


@app.get("/cases/search")
async def search_cases_retirado():
    """
    Retirado. Staging responde 401 a este endpoint desde el 7-sep-2026; el
    mock lo reproduce para que ningún camino se quede colgado del contrato
    viejo sin que se note.
    """
    raise HTTPException(
        status_code=401,
        detail="Endpoint retirado: usa GET /cases/agent-search",
    )


@app.get("/health")
async def health():
    return {"status": "ok", "type": "mock", "version": "2026-05-21"}
