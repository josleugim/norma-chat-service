"""
Enlaces de las citas: en el texto y en FUENTES.

El frontend convierte en link el markdown que recibe. José Miguel definió las
dos rutas (2 y 5-oct-2026):

    expediente → /case-search?caseLink=…&details=true
    criterio   → /digital-resolution?caseLink=…&anchor=…&paragraphId=…

La de expediente abre su ficha; la de criterio abre la resolución digital con
el párrafo subrayado. El `anchor` es texto largo, así que esa URL va entre
`<>` y codificada como `encodeURIComponent`, como la arma el frontend.

Dónde va el enlace (Imanol, 5-oct-2026):

- **En el texto**, cada marcador es enlace: `[[C1]](…)` se ve como `[C1]`.
- **En FUENTES**, el enlace es **todo el renglón**, no sólo el expediente: el
  renglón describe la referencia exacta —página, título del criterio—, y un
  enlace sólo sobre el expediente parece llevar al caso en general.

Los corchetes del marcador se dejan sin escapar a propósito. Los corchetes
balanceados son válidos dentro del texto de un enlace, y así `[C1]` sigue
apareciendo literal para todo lo que lee marcadores después: el constructor de
referencias, el análisis de la traza.

Se hace aquí y no en el prompt. Pedirle al modelo que escriba el markdown
funciona unas veces sí y otras no —las conductas narrativas varían entre
corridas idénticas—, y una URL que el modelo arma es una URL que puede
inventar.

El destino sale del **registro de citas**, no del texto. Un renglón de FUENTES
que no nombra el expediente de su marcador no se enlaza: el texto del enlace
diría un expediente y llevaría a otro.

El `anchor` no viaja en el documento que ve el modelo —se le quita para no
sepultar el texto del criterio—, así que llega aparte, por `ancla_de`.

Y un renglón de FUENTES con etiqueta de tipo pero sin marcador
(`- [RESOLUCIÓN] Cálculo agregado sobre 32 expedientes`) se presenta como
fuente sin serlo: no hay documento detrás. Se le quita la etiqueta y queda
como nota. Pasaba en q01 y en q20 (`[RESOLUCIÓN] CFC | Total: 2,792`).
"""
import re
from urllib.parse import quote

MARCADOR_FUENTE = re.compile(r"\[([CE]\d+)\]")

# Lo que `encodeURIComponent` deja sin codificar. `quote` por defecto deja
# también `/`, y con `safe=""` codifica `!*'()`: ninguno es lo que hace el
# frontend.
_SEGURO_JS = "-_.!~*'()"

# El encabezado de la sección, con o sin negritas, almohadillas o dos puntos.
# Se toma el último: un "FUENTES" en el cuerpo no abre la sección.
ENCABEZADO_FUENTES = re.compile(
    r"^[ \t]*(?:#+[ \t]*)?(?:\*\*)?[ \t]*FUENTES[ \t]*(?:\*\*)?[ \t]*:?[ \t]*(?:\*\*)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Etiqueta de tipo de fuente que pone el modelo (`[RESOLUCIÓN]`, `[SENTENCIA]`).
ETIQUETA_TIPO = re.compile(r"\[(?:RESOLUCI[ÓO]N|SENTENCIA)\][ \t]*", re.IGNORECASE)

# Viñeta o numeración al inicio del renglón: queda fuera del enlace.
_PREFIJO_RENGLON = re.compile(r"^([ \t]*(?:[-*•][ \t]+|\d+[.)][ \t]+)?)")


def ruta_expediente(case_link: str) -> str:
    """Ruta del frontend que abre la ficha del expediente."""
    if not case_link:
        return ""
    # `safe=""`: hay identificadores con espacio y paréntesis
    # (`184_2018 1JD`, `CNT-002-2020 (Proplastic)`). Un paréntesis sin
    # codificar cierra el link de markdown antes de tiempo.
    return f"/case-search?caseLink={quote(case_link, safe='')}&details=true"


def ruta_parrafo(case_link: str, anchor: str, paragraph_id: str) -> str:
    """Ruta del frontend que abre la resolución con el párrafo subrayado."""
    if not (case_link and anchor and paragraph_id):
        return ""
    return (
        f"/digital-resolution?caseLink={quote(case_link, safe=_SEGURO_JS)}"
        f"&anchor={quote(anchor, safe=_SEGURO_JS)}"
        f"&paragraphId={quote(str(paragraph_id), safe=_SEGURO_JS)}"
    )


def _destino(marcador: str, registry, ancla_de) -> tuple[str, str, str]:
    """
    `(destino_markdown, case_link, motivo)`. Sin destino, `motivo` dice por qué.

    Un criterio sin `anchor` o sin `id` no se enlaza a la ficha del expediente
    como premio de consolación: la cita es un párrafo, y mandar a otra cosa
    sería un enlace que no lleva a lo citado.
    """
    case_link = registry.case_link_of(marcador)
    if not case_link:
        return "", "", "sin_expediente_en_registro"
    if marcador.startswith("E"):
        return f"({ruta_expediente(case_link)})", case_link, ""
    doc = registry.resolve(marcador) or {}
    paragraph_id = str(doc.get("id") or "")
    if not paragraph_id:
        return "", case_link, "criterio_sin_id"
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    anchor = (meta.get("anchor") or "") or (ancla_de(paragraph_id) if ancla_de else "")
    if not anchor:
        return "", case_link, "criterio_sin_anchor"
    return f"(<{ruta_parrafo(case_link, anchor, paragraph_id)}>)", case_link, ""


def _buscar_identificador(renglon: str, case_link: str, desde: int) -> int:
    """
    Posición del identificador completo y todavía sin enlazar.

    Completo: `VCN-004-2022` es prefijo de `VCN-004-2022_2025_10_09`, que es
    otro documento —el acto de cumplimiento—; enlazar el prefijo mandaría al
    principal desde el renglón del cumplimiento.

    Sin enlazar: tras poner un enlace, el identificador vuelve a aparecer en
    el texto del link y en su URL (`caseLink=…`), y no hay que tocarlo ahí.
    """
    pos = renglon.find(case_link, desde)
    while pos >= 0:
        ant = renglon[pos - 1: pos] if pos > 0 else ""
        sig = renglon[pos + len(case_link): pos + len(case_link) + 1]
        # Un carácter vacío es borde de renglón; ojo: `"" in "_-"` es True.
        pegado_antes = bool(ant) and (ant.isalnum() or ant in "_-=[")
        pegado_despues = bool(sig) and (sig.isalnum() or sig in "_-")
        if not pegado_antes and not pegado_despues:
            return pos
        pos = renglon.find(case_link, pos + 1)
    return -1


def _ya_enlazado(texto: str, m: re.Match) -> bool:
    """`[[C1]](…)`: el marcador ya es el texto de un enlace."""
    return texto[m.start() - 1: m.start()] == "[" and texto[m.end(): m.end() + 2] == "]("


def _enlazar_cuerpo(cuerpo: str, registry, ancla_de, resumen: dict) -> str:
    """Cada marcador del texto se vuelve enlace a lo que cita."""
    def sustituir(m: re.Match) -> str:
        if _ya_enlazado(cuerpo, m):
            return m.group(0)
        destino, _, motivo = _destino(m.group(1), registry, ancla_de)
        if not destino:
            resumen["sin_enlazar"].append(
                {"marker": m.group(1), "donde": "texto", "motivo": motivo})
            return m.group(0)
        resumen["en_texto"] += 1
        return f"[{m.group(0)}]{destino}"
    return MARCADOR_FUENTE.sub(sustituir, cuerpo)


def _enlazar_renglon(renglon: str, registry, ancla_de, resumen: dict) -> str:
    marcas = list(MARCADOR_FUENTE.finditer(renglon))
    if not marcas:
        if ETIQUETA_TIPO.search(renglon):
            resumen["etiquetas_sin_cita_retiradas"].append(renglon.strip()[:160])
            return ETIQUETA_TIPO.sub("", renglon)
        return renglon
    if "](" in renglon:
        return renglon  # ya enlazado

    # Un solo marcador: el renglón entero es la referencia y el enlace la
    # cubre completa.
    if len({m.group(1) for m in marcas}) == 1:
        marcador = marcas[0].group(1)
        destino, case_link, motivo = _destino(marcador, registry, ancla_de)
        if destino and _buscar_identificador(renglon, case_link, marcas[0].end()) < 0:
            destino, motivo = "", "identificador_no_aparece_en_renglon"
        if not destino:
            resumen["sin_enlazar"].append({
                "marker": marcador, "case_link": case_link,
                "donde": "fuentes", "motivo": motivo})
            return renglon
        prefijo = _PREFIJO_RENGLON.match(renglon).group(1)
        contenido = renglon[len(prefijo):].rstrip()
        resto = renglon[len(prefijo) + len(contenido):]
        resumen["en_fuentes"] += 1
        return f"{prefijo}[{contenido}]{destino}{resto}"

    # Varios marcadores en un renglón: el modelo a veces agrupa
    # (`[E1]–[E36] VCN-002-2024 a VCN-004-2022_2025_10_09`). Un solo enlace
    # sobre todo el renglón llevaría a uno de ellos; cada marcador enlaza su
    # propio identificador.
    #
    # Las posiciones de `finditer` son del renglón original; los enlaces se
    # insertan después del primer marcador, así que esa sí es estable.
    inicio = marcas[0].end()
    for m in marcas:
        marcador = m.group(1)
        destino, case_link, motivo = _destino(marcador, registry, ancla_de)
        if case_link and f"[{case_link}](" in renglon:
            continue
        pos = _buscar_identificador(renglon, case_link, inicio) if case_link else -1
        if destino and pos < 0:
            destino, motivo = "", "identificador_no_aparece_en_renglon"
        if not destino:
            resumen["sin_enlazar"].append({
                "marker": marcador, "case_link": case_link,
                "donde": "fuentes", "motivo": motivo})
            continue
        renglon = renglon[:pos] + f"[{case_link}]{destino}" + renglon[pos + len(case_link):]
        resumen["en_fuentes"] += 1
    return renglon


# Centinela que la herramienta pone en `ficha_fuente` para decir "este dato no
# vino en esta búsqueda". Es para el modelo; el 6-oct apareció copiado en
# FUENTES ("| pp. NO_DISPONIBLE_EN_ESTA_BUSQUEDA") en 2 de 60 respuestas.
_CENTINELA = re.compile(
    r"[ \t]*(?:\|[ \t]*)?(?:pp?\.[ \t]*)?NO_DISPONIBLE_EN_ESTA_BUSQUEDA")


def retirar_centinelas(texto: str) -> tuple[str, int]:
    """
    Quita de FUENTES los campos que sólo dicen "no disponible" con el nombre
    interno. Un dato que no hay se omite; no se escribe su ausencia en jerga.
    """
    if not texto:
        return texto, 0
    encabezados = list(ENCABEZADO_FUENTES.finditer(texto))
    if not encabezados:
        return texto, 0
    corte = encabezados[-1].end()
    fuentes, n = _CENTINELA.subn("", texto[corte:])
    return texto[:corte] + fuentes, n


def enlazar_fuentes(texto: str, registry, ancla_de=None) -> tuple[str, dict]:
    """
    Enlaza las citas del texto y los renglones de FUENTES.

    `ancla_de(paragraph_id)` devuelve el `anchor` de un criterio.

    Devuelve el texto y un resumen para la traza: cuántos enlaces se pusieron
    en cada lugar y cuáles no, con el motivo. Una cita sin enlace no es error
    —la respuesta sale igual—, pero tiene que quedar a la vista.
    """
    resumen = {"en_texto": 0, "en_fuentes": 0, "sin_enlazar": [],
               "etiquetas_sin_cita_retiradas": []}
    if not texto or registry is None:
        return texto, resumen

    encabezados = list(ENCABEZADO_FUENTES.finditer(texto))
    corte = encabezados[-1].end() if encabezados else len(texto)
    cuerpo, fuentes = texto[:corte], texto[corte:]

    cuerpo = _enlazar_cuerpo(cuerpo, registry, ancla_de, resumen)
    fuentes = "\n".join(
        _enlazar_renglon(r, registry, ancla_de, resumen) for r in fuentes.split("\n"))
    return cuerpo + fuentes, resumen
