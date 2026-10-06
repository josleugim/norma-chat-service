"""
Costo estimado de una respuesta, en dólares.

Sirve para dos cosas que pidió COFECE el 6-oct: definir límites de uso por
licencia y poder ofrecer un modelo distinto según el plan. El proxy puede
contar preguntas o dólares por usuario con el `costo_estimado_usd` del evento
`done`.

**Es una cota superior.** Los proveedores cobran menos por la parte del
contexto que se repite (caché) y aquí no se descuenta, porque hoy no se
registra cuánto entró como caché. Tampoco incluye los embeddings de las
búsquedas, que van en la cuenta de José Miguel.
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


def costo_estimado(model: str, tokens_entrada: int, tokens_salida: int) -> float | None:
    """None si el modelo no está en la tabla: mejor sin cifra que con una inventada."""
    precio = PRECIOS.get(model or "")
    if precio is None:
        return None
    entrada, salida = precio
    return round(((tokens_entrada or 0) * entrada + (tokens_salida or 0) * salida) / 1e6, 6)
