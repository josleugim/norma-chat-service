"""
De quién es la voz de un criterio: ¿el tribunal, o uno de sus integrantes?

C05 del diagnóstico de COFECE (21-sep-2026). Ante *"¿qué sostuvo el Primer
Tribunal Colegiado en el 353/2024?"* el agente presentó como postura
**mayoritaria** el criterio 8422, que es el **voto particular de la Magistrada
Irma Leticia Flores Díaz**, e invirtió lo que sostenían mayoría y disidencia.
El documento citado era correcto; la atribución era falsa.

Un voto particular dice exactamente lo contrario de la sentencia: atribuirlo al
tribunal no es un matiz, es cambiar el sentido de lo resuelto.

**La API no expone la voz en campo propio.** Verificado el 21-sep: los campos
son `articleNames`, `caseLink`, `caseName`, `content`, `distance`, `id`,
`metadata` y `titleNames`. Pero el rastro sí está en `metadata.context`, en la
fórmula ritual con la que se firman estos documentos:

    "…Magistrada Irma Leticia Flores Díaz. […] Respetuosamente, formulo voto…"
    "SALVEDADES QUE FORMULA EL MAGISTRADO FRANCISCO GARCÍA SANDOVAL…"

## La regla que impone el diagnóstico

Sólo se afirma la voz cuando hay marca positiva de disidencia. **Lo demás es
`NO_IDENTIFICADA`, no "mayoría"**: deducir que un criterio es mayoritario
porque el documento es una sentencia es precisamente el error que hay que
impedir. La ausencia de marca no prueba que hable el pleno — prueba que no
sabemos.

Y el nombre del autor sale de la evidencia o no sale: si hay marca de voto sin
firma legible, el autor queda en None y quien responda no puede inventarlo.
"""
import re
import unicodedata

MAYORIA = "mayoria"
VOTO_PARTICULAR = "voto_particular"
VOTO_CONCURRENTE = "voto_concurrente"
NO_IDENTIFICADA = "no_identificada"

ETIQUETAS = {
    MAYORIA: "postura mayoritaria",
    VOTO_PARTICULAR: "voto particular (disidente)",
    VOTO_CONCURRENTE: "voto concurrente",
    NO_IDENTIFICADA: "voz no identificada",
}

# Disidencia declarada. "Me aparto" y "formulo voto" son las fórmulas con las
# que se abre un voto particular en la práctica mexicana.
_DISIDENTE = re.compile(
    r"voto\s+particular|voto\s+de\s+minor[íi]a|formulo\s+voto|"
    r"me\s+aparto\s+de|disiento|salvedades\s+que\s+formula|voto\s+disidente",
    re.IGNORECASE,
)
_CONCURRENTE = re.compile(r"voto\s+concurrente|concurriendo", re.IGNORECASE)

# Firma: "Magistrada Irma Leticia Flores Díaz", "MAGISTRADO FRANCISCO GARCÍA
# SANDOVAL". Se aceptan dos a cuatro palabras capitalizadas después del cargo.
_FIRMA = re.compile(
    r"\bMagistrad[ao]\s+((?:[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+\s+){1,4}"
    r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+)",
    re.IGNORECASE,
)

_CARGOS = ("magistrado", "magistrada", "ministro", "ministra", "juez", "jueza")


def _sin_acentos(t: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", t or "")
        if unicodedata.category(c) != "Mn"
    )


# El contexto viene paginado por el extractor. Es la frontera que separa el
# pasaje de lo que sólo comparte documento con él.
_PAGINA = re.compile(r"<<<PAGINA:(\d+)>>>")


def _pagina_del_pasaje(contexto: str, anchor: str) -> str | None:
    """
    El tramo del contexto que contiene al pasaje, o None si no se ubica.

    H16-A de la revisión del 23-sep. El criterio 4035 de VCN-005-2024 es
    razonamiento de la MAYORÍA, en la página 13. El clasificador barría los
    14,911 caracteres del contexto completo, encontraba en la página 14 la
    fórmula de firmas —"Con voto concurrente del Comisionado José Eduardo
    Mendoza Contreras"— y se la atribuía al criterio. El gold lo identifica
    como `pleno_mayoria`: falsedad material.

    Esa fórmula pertenece al DOCUMENTO, no al pasaje. Medido sobre los tres
    casos testigo, la frontera de página los separa limpiamente:

        doc 4035 (mayoría)          anchor p.13  →  marca en p.14   OTRA
        doc 8422 (voto particular)  anchor p.152 →  marca en p.152  MISMA
        doc 8423 (voto particular)  anchor p.153 →  marca en p.153  MISMA

    Se usa la estructura del documento y no una distancia en caracteres a
    propósito: un umbral ajustado a tres observaciones es un número inventado.
    """
    if not contexto:
        return None

    cortes = [m.start() for m in _PAGINA.finditer(contexto)]
    pos = contexto.find(anchor[:60]) if anchor else -1

    if pos < 0:
        # No se pudo ubicar el pasaje. Si el documento no está paginado, el
        # contexto ES una sola sección y sigue siendo del pasaje. Si SÍ lo
        # está, no hay forma de saber en qué tramo vive: no se atribuye.
        return None if cortes else contexto

    if not cortes:
        return contexto

    ini = max([c for c in cortes if c <= pos], default=0)
    fin = min([c for c in cortes if c > pos], default=len(contexto))
    return contexto[ini:fin]


def _texto_de(doc) -> str:
    """
    El texto donde se puede leer la voz DE ESTE pasaje.

    Incluye el criterio y su anchor —que son el pasaje— y sólo el tramo del
    contexto que lo contiene. Lo que quede en otras páginas pertenece al
    documento y se reporta aparte, sin atribuirse.

    Si el pasaje no se puede ubicar dentro del contexto, el contexto no entra:
    sin poder situarlo no hay forma de afirmar que la marca sea suya, y este
    módulo prefiere `NO_IDENTIFICADA` sobre una atribución que no se sostiene.
    """
    if not isinstance(doc, dict):
        return ""
    meta = doc.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    anchor = str(meta.get("anchor") or "")
    partes = [
        str(doc.get("content") or doc.get("text") or ""),
        _pagina_del_pasaje(str(meta.get("context") or ""), anchor) or "",
        anchor,
    ]
    return "\n".join(p for p in partes if p)


def _marca_en_otra_parte(doc) -> str | None:
    """
    Marca de voto que existe en el documento pero FUERA del pasaje.

    No clasifica: informa. Que la resolución lleve un voto concurrente en sus
    firmas es un hecho del documento, y ocultarlo sería el error opuesto al de
    H16-A. Lo que no puede hacerse es atribuírselo a este criterio.
    """
    if not isinstance(doc, dict):
        return None
    meta = doc.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    contexto = str(meta.get("context") or "")
    propio = _texto_de(doc)
    for pat in (_DISIDENTE, _CONCURRENTE):
        for m in pat.finditer(contexto):
            frag = contexto[max(0, m.start() - 45):m.start() + 90]
            if frag.strip() and frag not in propio:
                return " ".join(frag.split())
    return None


def autor_de(texto: str) -> str | None:
    """
    Nombre del firmante, o None.

    Devuelve None cuando no hay firma legible, a propósito: el diagnóstico pide
    que un voto sin autor no produzca un nombre.
    """
    m = _FIRMA.search(texto or "")
    if not m:
        return None
    nombre = " ".join(m.group(1).split())
    # Cortar en la primera palabra que ya no parece parte del nombre.
    palabras = []
    for p in nombre.split():
        if _sin_acentos(p).lower() in ("en", "el", "la", "del", "de", "y") and palabras:
            break
        palabras.append(p)
        if len(palabras) >= 5:
            break
    return " ".join(palabras) if palabras else None


def clasificar_voz(doc) -> dict:
    """
    `{voz, autor, evidencia}` para un criterio.

    `evidencia` es el fragmento que justificó la clasificación, para que la
    atribución sea auditable y no haya que creerle al clasificador.
    """
    texto = _texto_de(doc)
    if not texto:
        return {"voz": NO_IDENTIFICADA, "autor": None, "evidencia": None}

    m = _DISIDENTE.search(texto)
    if m:
        return {
            "voz": VOTO_PARTICULAR,
            "autor": autor_de(texto),
            "evidencia": _fragmento(texto, m.start()),
        }

    m = _CONCURRENTE.search(texto)
    if m:
        return {
            "voz": VOTO_CONCURRENTE,
            "autor": autor_de(texto),
            "evidencia": _fragmento(texto, m.start()),
        }

    # Sin marca de disidencia NO se concluye mayoría. Que el documento sea una
    # sentencia no dice quién habla en este fragmento.
    #
    # Si el documento SÍ lleva una marca de voto en otra parte —típicamente la
    # fórmula de firmas— se reporta sin atribuirla. Es un hecho del documento y
    # callarlo sería el error opuesto al de H16-A; presentarlo como la voz de
    # este pasaje fue exactamente el de H16-A.
    return {
        "voz": NO_IDENTIFICADA,
        "autor": None,
        "evidencia": None,
        "marca_en_documento": _marca_en_otra_parte(doc),
    }


def _fragmento(texto: str, pos: int, ancho: int = 90) -> str:
    ini = max(0, pos - ancho // 2)
    return " ".join(texto[ini:pos + ancho].split())


def etiqueta(voz: str) -> str:
    return ETIQUETAS.get(voz, ETIQUETAS[NO_IDENTIFICADA])
