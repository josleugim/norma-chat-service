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
                "prefijo_expediente": {
                    "type": "string",
                    "enum": ["VCN", "IO", "CNT", "DE", "RA", "CON"],
                    "description": (
                        "Filtra por TIPO de expediente según su prefijo. ÚSALO SIEMPRE "
                        "que el usuario nombre un tipo: VCN (concentración no notificada), "
                        "IO (omisión a la obligación de notificar), CNT (concentración "
                        "notificada). Es la única forma correcta de acotar a VCN: "
                        "tipo_procedimiento NO distingue entre VCN e IO, y las CNT son el "
                        "99% del acervo, así que sin este filtro tu búsqueda se llenará "
                        "de CNT aunque hayas preguntado por otra cosa."
                    ),
                },
                "exhaustivo": {
                    "type": "boolean",
                    "description": (
                        "true para recorrer TODAS las páginas de resultados en vez de "
                        "una sola. Úsalo SIEMPRE que la pregunta implique el universo "
                        "completo: 'todos', 'cuántos', 'cuáles', 'el mayor', 'el menor', "
                        "'promedio', 'nunca', 'lista completa'. Sin esto solo ves la "
                        "primera página y no puedes sostener una afirmación sobre el total."
                    ),
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de resultados a retornar (default 50). Con exhaustivo=true el tope sube a 500.",
                    "default": 50,
                },
            },
        },
    },
    {
        "name": "contar_expedientes",
        "description": (
            "Cuenta cuántos expedientes cumplen unos filtros, SIN traer los "
            "registros. Úsala cuando la pregunta sea '¿cuántos...?' o cuando "
            "necesites saber el tamaño del universo antes de decidir si puedes "
            "responder. Es mucho más barata que traer todos los expedientes y "
            "devuelve el total exacto, no una estimación."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prefijo_expediente": {
                    "type": "string",
                    "enum": ["VCN", "IO", "CNT", "DE", "RA", "CON"],
                    "description": "Tipo de expediente a contar",
                },
                "autoridad": {
                    "type": "string",
                    "enum": ["CFC", "COFECE", "Cofece"],
                    "description": "Filtrar por autoridad",
                },
                "tipo_procedimiento": {"type": "string"},
                "sentido_resolucion": {"type": "string"},
                "text_search": {
                    "type": "string",
                    "description": "Búsqueda libre para acotar el conteo",
                },
            },
        },
    },
    {
        "name": "calcular_plazos",
        "description": (
            "Calcula plazos en días hábiles con el calendario oficial de días "
            "inhábiles de COFECE/CNA. USA SIEMPRE esta herramienta para "
            "cualquier cómputo de días hábiles: NUNCA lo estimes tú, porque no "
            "conoces el calendario de suspensión de labores. "
            "Tiene dos modos: (a) pasar dos fechas sueltas en fecha_inicio_explicita "
            "y fecha_fin_explicita, para preguntas del tipo '¿cuántos días hábiles "
            "hay entre X e Y?'; (b) pasar expedientes recuperados, o "
            "usar_ultima_busqueda=true para reutilizar los de tu búsqueda anterior "
            "sin tener que copiarlos. Regla de cómputo: se excluye el día inicial "
            "y se incluye el día final."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fecha_inicio_explicita": {
                    "type": "string",
                    "description": "Fecha inicial en formato YYYY-MM-DD o DD-MM-YYYY, para calcular entre dos fechas sueltas",
                },
                "fecha_fin_explicita": {
                    "type": "string",
                    "description": "Fecha final en formato YYYY-MM-DD o DD-MM-YYYY",
                },
                "institucion": {
                    "type": "string",
                    "enum": ["COFECE", "CNA"],
                    "description": "Calendario a usar con fechas explícitas (default COFECE)",
                },
                "usar_ultima_busqueda": {
                    "type": "boolean",
                    "description": (
                        "true para calcular sobre los expedientes de tu última "
                        "búsqueda sin volver a enviarlos. PREFIERE esto a copiar el "
                        "arreglo: enviar muchos expedientes puede exceder el límite "
                        "de tokens y truncar la llamada."
                    ),
                    "default": False,
                },
                "expedientes": {
                    "type": "array",
                    "description": "Lista de expedientes con sus fechas. Opcional si usas usar_ultima_busqueda o fechas explícitas.",
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
        },
    },
]
