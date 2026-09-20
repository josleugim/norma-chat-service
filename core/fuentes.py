"""
De quién es cada fuente: ¿la resolvió la autoridad de competencia, o la revisó
un juez?

Por qué existe. q10 pregunta *"¿cuáles son los criterios que usa **la COFECE**
para determinar el monto de las multas?"*. Medido el 19-sep-2026 contra el
índice de criterios, el top-60 de esa consulta trae **43 párrafos de
resoluciones VCN y 17 de sentencias judiciales** (9 expedientes distintos entre
juzgados de distrito y tribunales colegiados). El agente los citaba todos
juntos, así que criterios de un juez federal *revisando* a la COFECE salían
presentados como criterios *de* la COFECE.

Es el mismo error que COFECE ya nos marcó en el fix 2 de la ronda v1.8: Imanol
aclaró que el conocimiento general **sí se puede usar** —es útil— y que lo
incorrecto es *atribuírselo a la COFECE*. Aquí aplica más fuerte, porque una
sentencia no es conocimiento general: es otra autoridad, con otro peso, y
COFECE la lee distinto.

La regla no es filtrar, es **atribuir**. Una sentencia que interpreta el
artículo 130 de la LFCE es evidencia valiosa para la pregunta; lo que no puede
pasar es que se lea como si la hubiera escrito la Comisión.

Cómo se clasifica. La API no expone la autoridad en los párrafos, así que sale
de la forma del `caseLink`, que es la misma convención que usa el censo: los
procedimientos administrativos llevan prefijo con guión (`VCN-004-2024`,
`CNT-090-2025`, `IO-003-2018`) y los documentos judiciales no
(`1244_2017_2JD`, `480_2018_2SCJN`, `565_2023_1TCC_2025_04_24`).
"""

RESOLUCION = "resolucion"   # la emitió la autoridad de competencia
SENTENCIA = "sentencia"     # la emitió el poder judicial
DESCONOCIDA = "desconocida"

# Prefijos de procedimiento administrativo ante la autoridad de competencia.
PREFIJOS_ADMINISTRATIVOS = ("VCN", "CNT", "IO", "DE", "RA", "CON", "LI", "AD")

# Marcas de órgano jurisdiccional dentro del identificador.
MARCAS_JUDICIALES = ("JD", "TCC", "SCJN")

ETIQUETAS = {
    RESOLUCION: "resolución de la autoridad de competencia",
    SENTENCIA: "sentencia del poder judicial",
    DESCONOCIDA: "origen no determinado",
}


def clasificar_fuente(case_link: str | None) -> str:
    """
    `RESOLUCION`, `SENTENCIA` o `DESCONOCIDA` a partir del identificador.

    No adivina: un identificador que no encaja en ninguna convención regresa
    `DESCONOCIDA` en vez de caer al caso administrativo por defecto. Que una
    fuente salga sin clasificar tiene que notarse, no absorberse — es la regla
    que quedó después de que un 401 silencioso se volviera "no existe".
    """
    link = (case_link or "").strip().upper()
    if not link:
        return DESCONOCIDA

    # El prefijo administrativo manda: `VCN-002-2023_2025_10_09` es una
    # resolución en cumplimiento de amparo, emitida por la Comisión, aunque
    # lleve fecha de cumplimiento pegada.
    if "-" in link:
        prefijo = link.split("-", 1)[0]
        if prefijo in PREFIJOS_ADMINISTRATIVOS:
            return RESOLUCION

    if any(marca in link for marca in MARCAS_JUDICIALES):
        return SENTENCIA

    return DESCONOCIDA


def case_link_de(doc) -> str:
    """
    El identificador de expediente, venga donde venga.

    Los criterios lo traen en `metadata["id_expediente"]` (así lo arma
    `criterios_client`), los expedientes en `caseLink` al nivel de arriba, y
    el documento ya serializado para el modelo puede traerlo en los dos. Mirar
    un solo lugar deja la fuente sin clasificar, y sin clasificar se lee como
    si fuera de la COFECE — que es el caso que hay que evitar.
    """
    if not isinstance(doc, dict):
        return ""
    meta = doc.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    return (
        doc.get("caseLink")
        or doc.get("id_expediente")
        or meta.get("id_expediente")
        or meta.get("caseLink")
        or ""
    )


def etiqueta_fuente(case_link: str | None) -> str:
    """Etiqueta legible, para que el modelo pueda atribuir sin inventar."""
    return ETIQUETAS[clasificar_fuente(case_link)]


def composicion(case_links) -> dict:
    """
    Cuántas fuentes de cada tipo. Va a la traza: sin esto, una respuesta que
    mezcla 43 resoluciones con 17 sentencias se ve igual que una que sólo usó
    resoluciones.
    """
    conteo = {RESOLUCION: 0, SENTENCIA: 0, DESCONOCIDA: 0}
    for link in case_links:
        conteo[clasificar_fuente(link)] += 1
    return conteo
