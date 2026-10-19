"""
Búsqueda de expedientes por persona: quién resolvió, quién votó y quién recurrió.

`searchData` de la API sólo mira número, nombre, agentes y mercados, así que
"todas las sentencias en que participó el juez X" no tenía ruta: el dato
existe (`decisionOfficials` en 41 de 51 judiciales al 18-oct-2026) pero no se
podía buscar. Imanol lo pidió en vivo y el chat contestó que el nombre del
juez "no es un campo de resultado", que además era falso.

La búsqueda se hace localmente sobre el universo completo, que cabe en una
petición. Cada coincidencia lleva el papel con que aparece la persona: un
mismo nombre puede ser juez de distrito en un expediente y magistrado en
otro, y el usuario puede haber preguntado con el título equivocado.
"""
from __future__ import annotations

import re
import unicodedata

# Campos que nombran personas o instituciones que intervinieron en la
# decisión. Las partes notificantes y los agentes económicos ya los cubre
# `text_search` en el servidor (agentes) o no son personas que deciden.
CAMPOS_PERSONA = (
    "decisionOfficials",
    "dissentingOpinions",
    "dissentingAndConcurringOpinions",
    "principalAppellants",
    "adhesiveAppellants",
)

# Títulos que el usuario antepone y que el registro no escribe igual
# ("Magistrado Gómez Fierro" contra "Juan Pablo Gómez Fierro").
_TITULOS = {
    "magistrado", "magistrada", "juez", "jueza", "ministro", "ministra",
    "comisionado", "comisionada", "secretario", "secretaria", "lic", "licenciado",
    "licenciada", "mtro", "mtra", "dr", "dra", "el", "la", "de", "del",
}

_PAPEL_RECURRENTE = {
    "principalAppellants": "recurrente principal",
    "adhesiveAppellants": "recurrente adhesivo",
}


def _norm(texto) -> str:
    s = str(texto or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9ñ]+", " ", s).split())


def palabras_de_consulta(persona: str) -> list[str]:
    """Las palabras del nombre buscado, sin títulos ni partículas."""
    return [p for p in _norm(persona).split() if p not in _TITULOS]


def _entradas(campo: str, valor) -> list[tuple[str, str]]:
    """(nombre, papel) de cada persona en un campo, sea cual sea su forma."""
    elementos = valor if isinstance(valor, list) else [valor]
    salida = []
    for e in elementos:
        if isinstance(e, dict):
            nombre = e.get("nombre") or ""
            if campo == "decisionOfficials":
                # `rol` es la categoría normalizada (magistrado_presidente) y
                # `rol_literal` lo que dice el documento ("Presidencia"): el
                # primero dice qué cargo, el segundo de qué órgano.
                rol = _humano(e.get("rol"))
                literal = str(e.get("rol_literal") or "").strip()
                papel = rol or literal
                if rol and literal and _norm(literal) != _norm(rol):
                    papel = f"{rol} ({literal})"
            else:
                papel = f"voto {_humano(e.get('posicion'))}" if e.get("posicion") else "voto"
            if nombre.strip():
                salida.append((nombre.strip(), papel.strip()))
            continue
        # Los votos a veces vienen varios en una cadena:
        # "Brenda Hernández (en contra); Alejandra Palacios (en contra); ..."
        partes = str(e or "").split(";") if campo == "dissentingOpinions" else [str(e or "")]
        for texto in partes:
            texto = texto.strip()
            m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", texto)
            if campo == "dissentingOpinions":
                nombre, papel = (m.group(1), f"voto {_humano(m.group(2))}") if m else (texto, "voto")
            else:
                nombre, papel = texto, _PAPEL_RECURRENTE.get(campo, "")
            if nombre.strip():
                salida.append((nombre.strip(), papel.strip()))
    return salida


def _humano(etiqueta) -> str:
    return " ".join(str(etiqueta or "").replace("_", " ").split())


def coincidencias(registro: dict, persona: str) -> list[dict]:
    """
    Dónde y con qué papel aparece la persona en un registro.

    Coincide si TODAS las palabras del nombre buscado están, como palabras
    completas, en el nombre de una misma entrada. "Gómez" solo coincide con
    Gómez Fierro y con Gómez Romero: se devuelven los nombres completos para
    que la ambigüedad quede a la vista en vez de resolverse en silencio.
    """
    buscadas = palabras_de_consulta(persona)
    if not buscadas:
        return []
    salida = []
    for campo in CAMPOS_PERSONA:
        valor = registro.get(campo)
        if not valor:
            continue
        for nombre, papel in _entradas(campo, valor):
            palabras = set(_norm(nombre).split())
            if all(b in palabras for b in buscadas):
                c = {"nombre": nombre, "papel": papel}
                if c not in salida:
                    salida.append(c)
    return salida


def filtrar_por_persona(registros: list[dict], persona: str) -> list[dict]:
    """Los registros donde aparece la persona, cada uno con `coincidencia_persona`."""
    salida = []
    for r in registros:
        c = coincidencias(r, persona)
        if c:
            salida.append({**r, "coincidencia_persona": c})
    return salida
