"""
Costo estimado de una respuesta, en dólares.

Sirve para dos cosas que pidió COFECE el 6-oct: definir límites de uso por
licencia y poder ofrecer un modelo distinto según el plan. El proxy puede
contar preguntas o dólares por usuario con el `costo_estimado_usd` del evento
`done`.

Desde el 20-oct descuenta la caché: la parte del contexto que el proveedor
leyó de caché se cobra a su precio, y la que escribió (Anthropic) también.
Un modelo sin precio de caché en la tabla se cobra completo, que es la cota
superior. No incluye los embeddings de las búsquedas, que van en la cuenta
de José Miguel.
"""

# USD por millón de tokens: (entrada, salida). Leídos el 6-oct-2026 en
# developers.openai.com/api/docs/pricing y en la tabla de modelos Claude
# vigente al 25-sep-2026. Si un precio cambia, se cambia aquí.
PRECIOS = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5.6-terra": (2.00, 12.00),
    "claude-sonnet-5-5": (2.00, 10.00),
}


# USD por millón de tokens de caché: (lectura, escritura 5 min, escritura
# 1 hora). OpenAI no cobra la escritura aparte (es entrada normal).
# Anthropic: lectura 0.1×, escritura 1.25× (5 min) o 2× (1 hora).
PRECIOS_CACHE = {
    "gpt-4.1": (0.50, 2.00, 2.00),
    "gpt-4.1-mini": (0.10, 0.40, 0.40),
    "claude-sonnet-5-5": (0.20, 2.50, 4.00),
}


def costo_estimado(model: str, tokens_entrada: int, tokens_salida: int,
                   cache_lectura: int = 0, cache_escritura: int = 0,
                   cache_escritura_1h: int = 0) -> float | None:
    """
    None si el modelo no está en la tabla: mejor sin cifra que con una inventada.

    `tokens_entrada` es el total procesado; `cache_lectura` y
    `cache_escritura` son partes de él, y `cache_escritura_1h` es la parte
    de la escritura que dura una hora.
    """
    precio = PRECIOS.get(model or "")
    if precio is None:
        return None
    entrada, salida = precio
    lectura_px, escritura_px, hora_px = PRECIOS_CACHE.get(
        model or "", (entrada, entrada, entrada))
    lectura, escritura = cache_lectura or 0, cache_escritura or 0
    hora = min(cache_escritura_1h or 0, escritura)
    normal = max((tokens_entrada or 0) - lectura - escritura, 0)
    return round((normal * entrada + lectura * lectura_px
                  + (escritura - hora) * escritura_px + hora * hora_px
                  + (tokens_salida or 0) * salida) / 1e6, 6)
