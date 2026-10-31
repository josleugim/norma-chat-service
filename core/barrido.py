"""
Barrido de criterios para preguntas que piden "todos", agrupar por tema o ver
la evolución en el tiempo.

Medido el 31-oct contra la API. Con la pregunta de Imanol sobre sustitución
por el lado de la oferta, el top-15 traía **cero** criterios del tema; los
mismos estaban entre las posiciones 16 y 500. Con 300 resultados aparecen 11 de
los 13 expedientes del tema (y 23 de 23 en la de no competencia). El filtro de
distancia (0.7) no corta nada: la distancia más lejana en mil resultados fue
0.63. El tope real era el top-k.

Trescientos fragmentos no caben en el contexto, así que el barrido se condensa:

- sin `tema`: un pasaje por par expediente-tema (el más cercano), hasta
  `MAX_PASAJES`, más un índice de los temas encontrados con cuántos criterios
  y expedientes trae cada uno y el rango de años;
- con `tema`: todos los criterios de ese tema dentro del barrido, en orden
  cronológico, para ver si la línea cambió.

**No es una lista completa del tema.** La API no permite listar un tema (los
filtros por `titleNames` se aceptan con 201 y se ignoran); un barrido es una
muestra mucho más grande, no un inventario. La advertencia viaja con el
resultado y el modelo no debe afirmar que son todos.
"""
from __future__ import annotations

import unicodedata
from collections import defaultdict

PROFUNDIDAD = 300
MAX_PASAJES = 40
MAX_PASAJES_TEMA = 60
MAX_TEMAS_INDICE = 25

ADVERTENCIA = (
    "Barrido de los {revisados} criterios más cercanos a la consulta. La "
    "búsqueda no permite listar un tema completo: puede haber más criterios "
    "fuera del barrido. Di cuántos encontraste y en cuántos expedientes, y no "
    "afirmes que son todos ni que no existe un criterio contrario."
)


def _norm(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto or "")
    return " ".join("".join(c for c in sin_acentos if not unicodedata.combining(c)).lower().split())


def temas_de(resultado) -> list[str]:
    meta = getattr(resultado, "metadata", None) or {}
    temas = meta.get("titleNames") or []
    if not temas and meta.get("title"):
        temas = [meta["title"]]
    return [str(t).strip() for t in temas if str(t or "").strip()]


def _case_link(resultado) -> str:
    meta = getattr(resultado, "metadata", None) or {}
    return str(meta.get("id_expediente") or meta.get("caseLink") or "").strip()


def _anio(ficha: dict | None) -> int | None:
    fecha = (ficha or {}).get("fecha_resolucion")
    return int(fecha[:4]) if fecha else None


def coincide_tema(resultado, tema: str) -> bool:
    buscado = _norm(tema)
    if not buscado:
        return False
    return any(buscado == _norm(t) or buscado in _norm(t) for t in temas_de(resultado))


def condensar(candidatos: list, tema: str | None = None, fichas=None) -> tuple[list, dict]:
    """
    `candidatos` vienen ordenados por cercanía. Devuelve los pasajes que entran
    al contexto y la descripción del barrido para el modelo y la traza.
    """
    ficha = (lambda cl: fichas.get(cl)) if fichas is not None else (lambda cl: None)
    revisados = len(candidatos)
    expedientes = {_case_link(r) for r in candidatos if _case_link(r)}
    info = {
        "revisados": revisados,
        "expedientes_en_el_barrido": len(expedientes),
        "advertencia": ADVERTENCIA.format(revisados=revisados),
    }

    if tema:
        del_tema = [r for r in candidatos if coincide_tema(r, tema)]
        # Orden cronológico: lo que pidió Imanol es poder ver si la línea
        # cambió, no que lo antiguo pese menos. Sin fecha, al final.
        orden = sorted(
            enumerate(del_tema),
            key=lambda p: ((_anio(ficha(_case_link(p[1]))) is None),
                           (ficha(_case_link(p[1])) or {}).get("fecha_resolucion") or "",
                           p[0]),
        )
        pasajes = [r for _, r in orden][:MAX_PASAJES_TEMA]
        exps = {_case_link(r) for r in del_tema if _case_link(r)}
        info.update({
            "modo": "tema",
            "tema": tema,
            "criterios_del_tema": len(del_tema),
            "expedientes_del_tema": len(exps),
            "mostrados": len(pasajes),
            "orden": "cronologico_por_fecha_de_resolucion",
        })
        if not del_tema:
            info["nota"] = (
                "Ningún criterio del barrido trae ese tema. Revisa el nombre "
                "exacto en el índice de un barrido sin tema; esto no prueba que "
                "el tema no exista en el acervo.")
        return pasajes, info

    # Un pasaje por par expediente-tema: el más cercano. Sin esto, el
    # expediente con más fragmentos parecidos ocupa todo el contexto.
    vistos: set = set()
    pasajes = []
    for r in candidatos:
        clave = (_case_link(r), (temas_de(r) or [""])[0])
        if clave in vistos:
            continue
        vistos.add(clave)
        pasajes.append(r)
    duplicados = revisados - len(pasajes)
    pasajes = pasajes[:MAX_PASAJES]

    por_tema: dict = defaultdict(lambda: {"criterios": 0, "expedientes": set(), "anios": []})
    for r in candidatos:
        cl = _case_link(r)
        anio = _anio(ficha(cl))
        for t in temas_de(r) or ["(sin tema)"]:
            g = por_tema[t]
            g["criterios"] += 1
            if cl:
                g["expedientes"].add(cl)
            if anio:
                g["anios"].append(anio)
    indice = []
    for t, g in sorted(por_tema.items(), key=lambda kv: (-kv[1]["criterios"], kv[0])):
        fila = {"tema": t, "criterios": g["criterios"], "expedientes": len(g["expedientes"])}
        if g["anios"]:
            fila["anios"] = f"{min(g['anios'])}-{max(g['anios'])}"
        indice.append(fila)
    info.update({
        "modo": "barrido",
        "mostrados": len(pasajes),
        "agrupados_por_expediente_y_tema": duplicados,
        "temas_encontrados": len(indice),
        "indice_de_temas": indice[:MAX_TEMAS_INDICE],
        "siguiente_paso": (
            "Para ver todos los criterios de un tema dentro del barrido, en "
            "orden cronológico, vuelve a llamar con la misma consulta y "
            "`tema` igual al nombre del índice."),
    })
    return pasajes, info
