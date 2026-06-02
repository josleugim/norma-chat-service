"""
Definición de herramientas (tools) para el agente de Norma+.
Formato agnóstico: se convierte al formato nativo de cada proveedor
en el adaptador LLM correspondiente.
"""

TOOLS = [
    {
        "name": "buscar_criterios",
        "description": (
            "Busca criterios analíticos de competencia económica en el acervo "
            "de Norma+ por similitud semántica. Usa esta herramienta cuando el "
            "usuario pregunte sobre conceptos, definiciones, argumentos, mercado "
            "relevante, poder de mercado, barreras a la entrada, eficiencias, "
            "sustitutos, prácticas coordinadas/unilaterales, o cualquier análisis "
            "jurídico de competencia económica. Retorna texto de criterios con "
            "metadata (expediente, páginas, artículo de ley)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta semántica para búsqueda vectorial"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Número máximo de resultados (5-30). Usa 8 para preguntas simples, 15 para medias, 25 para complejas.",
                    "default": 15,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "buscar_expedientes",
        "description": (
            "Busca expedientes de competencia económica por filtros "
            "estructurados (autoridad, tipo de procedimiento, sentido de "
            "resolución, rango de fechas, multas) y/o por nombre de agente "
            "económico o mercado relevante. Usa esta herramienta cuando el "
            "usuario pregunte sobre datos procesales: fechas, resoluciones, "
            "multas, agentes involucrados, o cuando necesites datos para "
            "calcular plazos después."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text_search": {
                    "type": "string",
                    "description": "Búsqueda libre en nombre del caso, agentes económicos, mercados relevantes y número de expediente simultáneamente (e.g. 'Scotiabank', 'telecomunicaciones', 'Cemex', 'CNT-095-2013')",
                },
                "autoridad": {
                    "type": "string",
                    "enum": ["CFC", "COFECE", "Cofece"],
                    "description": "Filtrar por autoridad: CFC (histórica) o COFECE/Cofece (actual)",
                },
                "tipo_procedimiento": {
                    "type": "string",
                    "enum": [
                        "Concentración",
                        "Concentración no notificada",
                        "Concentración ilícita",
                    ],
                    "description": "Filtrar por tipo de procedimiento",
                },
                "sentido_resolucion": {
                    "type": "string",
                    "description": "Filtrar por sentido de resolución: AUTORIZADA, CONDICIONADA, NO AUTORIZADA, OBJETADA, SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO, etc.",
                },
                "has_multas": {
                    "type": "boolean",
                    "description": "true para filtrar solo expedientes que tienen multas. Se aplica después de la búsqueda, filtrando los que tienen agentFines no vacío.",
                },
                "fecha_resolucion_desde": {
                    "type": "integer",
                    "description": "Año mínimo de resolución (e.g. 2020). Se usa como rango junto con fecha_resolucion_hasta.",
                },
                "fecha_resolucion_hasta": {
                    "type": "integer",
                    "description": "Año máximo de resolución (e.g. 2024). Se usa como rango junto con fecha_resolucion_desde.",
                },
                "id_expediente": {
                    "type": "string",
                    "description": "Búsqueda exacta por número de expediente / caseLink (e.g. 'CNT-095-2013', 'IO-001-2019')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de resultados a retornar (default 50, max 200)",
                    "default": 50,
                },
            },
        },
    },
    {
        "name": "calcular_plazos",
        "description": (
            "Calcula plazos procesales en días hábiles entre fechas de "
            "expedientes. SIEMPRE usa esta herramienta DESPUÉS de "
            "buscar_expedientes cuando el usuario pregunte sobre duración "
            "de procedimientos, tiempos de resolución, cuánto tardó un caso, "
            "o comparaciones temporales. Puede calcular estadísticas "
            "(promedio, mediana, percentiles) sobre un conjunto de expedientes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expedientes": {
                    "type": "array",
                    "description": "Lista de expedientes con sus fechas (del resultado de buscar_expedientes)",
                    "items": {"type": "object"},
                },
                "fecha_inicio": {
                    "type": "string",
                    "enum": ["fecha_notificacion", "fecha_admision", "fecha_requerimiento_basica"],
                    "description": "Campo de fecha inicial para el cálculo",
                    "default": "fecha_notificacion",
                },
                "fecha_fin": {
                    "type": "string",
                    "enum": ["fecha_resolucion"],
                    "description": "Campo de fecha final para el cálculo",
                    "default": "fecha_resolucion",
                },
                "max_dias_habiles": {
                    "type": "integer",
                    "description": "Filtrar expedientes con plazo menor o igual a N días hábiles",
                },
                "min_dias_habiles": {
                    "type": "integer",
                    "description": "Filtrar expedientes con plazo mayor o igual a N días hábiles",
                },
                "compute_stats": {
                    "type": "boolean",
                    "description": "true para calcular promedio, mediana, percentiles sobre los plazos",
                    "default": False,
                },
            },
            "required": ["expedientes"],
        },
    },
]
