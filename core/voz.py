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


def _texto_de(doc) -> str:
    if not isinstance(doc, dict):
        return ""
    meta = doc.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    partes = [
        str(doc.get("content") or doc.get("text") or ""),
        str(meta.get("context") or ""),
        str(meta.get("anchor") or ""),
    ]
    return "\n".join(p for p in partes if p)


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
    return {"voz": NO_IDENTIFICADA, "autor": None, "evidencia": None}


def _fragmento(texto: str, pos: int, ancho: int = 90) -> str:
    ini = max(0, pos - ancho // 2)
    return " ".join(texto[ini:pos + ancho].split())


def etiqueta(voz: str) -> str:
    return ETIQUETAS.get(voz, ETIQUETAS[NO_IDENTIFICADA])
