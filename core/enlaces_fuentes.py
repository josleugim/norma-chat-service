"""
Enlaces en la sección FUENTES.

El frontend convierte en link el markdown que recibe. José Miguel definió las
dos rutas (2 y 5-oct-2026):

    [E1] [VCN-001-2017](/case-search?caseLink=VCN-001-2017&details=true) | COFECE | ...
    [C1] [1251_2017_1JD](</digital-resolution?caseLink=…&anchor=…&paragraphId=8471>) | pp. 3 | ...

La de expediente abre su ficha; la de criterio abre la resolución digital con
el párrafo subrayado. El `anchor` es texto largo, así que la URL va entre
`<>` y codificada como `encodeURIComponent`, como la arma el frontend.

Se hace aquí y no en el prompt. Pedirle al modelo que escriba el markdown
funciona unas veces sí y otras no —las conductas narrativas varían entre
corridas idénticas—, y una URL que el modelo arma es una URL que puede
inventar.

El identificador del enlace sale del **registro de citas**, no del texto del
renglón: el modelo copió el expediente al lado de su marcador, pero lo que
sabemos que corresponde a `[E1]` es lo que dice el registro. Si el renglón no
contiene ese identificador, no se enlaza: enlazar otra cadena sería afirmar
que el modelo escribió lo que no escribió.

El `anchor` no viaja en el documento que ve el modelo —se le quita para no
sepultar el texto del criterio—, así que llega aparte, por `ancla_de`.
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


def _destino(marcador: str, case_link: str, registry, ancla_de) -> tuple[str, str]:
    """
    El destino del enlace en markdown, o el motivo por el que no lo hay.

    Un criterio sin `anchor` o sin `id` no se enlaza a la ficha del expediente
    como premio de consolación: el renglón dice "este párrafo", y mandar a
    otra cosa sería un enlace que no lleva a lo citado.
    """
    if marcador.startswith("E"):
        return f"({ruta_expediente(case_link)})", ""
    doc = registry.resolve(marcador) or {}
    paragraph_id = str(doc.get("id") or "")
    if not paragraph_id:
        return "", "criterio_sin_id"
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    anchor = (meta.get("anchor") or "") or (ancla_de(paragraph_id) if ancla_de else "")
    if not anchor:
        return "", "criterio_sin_anchor"
    return f"(<{ruta_parrafo(case_link, anchor, paragraph_id)}>)", ""


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


def enlazar_fuentes(texto: str, registry, ancla_de=None) -> tuple[str, dict]:
    """
    Enlaza el identificador de cada renglón de FUENTES: `[E#]` a la ficha del
    expediente, `[C#]` al párrafo citado.

    `ancla_de(paragraph_id)` devuelve el `anchor` de un criterio.

    Devuelve el texto y un resumen para la traza: cuántos renglones se
    enlazaron y cuáles no, con el motivo. Un renglón sin enlazar no es error
    —la respuesta sale igual—, pero tiene que quedar a la vista.
    """
    resumen = {"enlazados": 0, "sin_enlazar": []}
    if not texto or registry is None:
        return texto, resumen

    encabezados = list(ENCABEZADO_FUENTES.finditer(texto))
    if not encabezados:
        return texto, resumen
    corte = encabezados[-1].end()
    cuerpo, fuentes = texto[:corte], texto[corte:]

    renglones = fuentes.split("\n")
    for i, renglon in enumerate(renglones):
        # Puede haber varios marcadores en un renglón: el modelo a veces
        # agrupa (`[E1]–[E36] VCN-002-2024 a VCN-004-2022_2025_10_09`).
        # Cada uno enlaza su propio expediente, si el renglón lo nombra.
        marcas = list(MARCADOR_FUENTE.finditer(renglon))
        if not marcas:
            continue
        # Las posiciones de `finditer` son del renglón original; los enlaces
        # se insertan después del primer marcador, así que esa sí es estable.
        inicio = marcas[0].end()
        for m in marcas:
            marcador = m.group(1)
            case_link = registry.case_link_of(marcador)
            if not case_link:
                resumen["sin_enlazar"].append(
                    {"marker": marcador, "motivo": "sin_expediente_en_registro"})
                continue
            if f"[{case_link}](" in renglon:
                continue  # ya enlazado
            # Después del primer marcador, para no tocar los `[E#]`/`[C#]`.
            pos = _buscar_identificador(renglon, case_link, inicio)
            if pos < 0:
                resumen["sin_enlazar"].append(
                    {"marker": marcador, "case_link": case_link,
                     "motivo": "identificador_no_aparece_en_renglon"})
                continue
            destino, motivo = _destino(marcador, case_link, registry, ancla_de)
            if not destino:
                resumen["sin_enlazar"].append(
                    {"marker": marcador, "case_link": case_link, "motivo": motivo})
                continue
            enlace = f"[{case_link}]{destino}"
            renglon = renglon[:pos] + enlace + renglon[pos + len(case_link):]
            resumen["enlazados"] += 1
        renglones[i] = renglon

    return cuerpo + "\n".join(renglones), resumen
