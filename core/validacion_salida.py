"""
Validar el borrador antes de emitirlo, no después.

C06 del diagnóstico de COFECE (21-sep-2026). En `_run_traced`, las tres rutas
de salida —`content`, `stream` y el fallback por agotamiento— emitían tokens
antes de resolver las citas. Para cuando se descubría que `[C14]` no estaba en
el registro del turno, el texto ya había salido: la defensa existía y llegaba
tarde.

    token… token… token…  →  resolve_citations  →  done

La cita inválida se quitaba de las referencias estructuradas, así que el panel
lateral quedaba limpio — pero **el cuerpo de la respuesta conservaba el
marcador y la afirmación que colgaba de él**. Un lector veía una afirmación con
cita y una lista de fuentes donde esa cita no estaba.

Por qué se puede corregir sin costo de generación: las 180 respuestas del
holdout usaron `final_answer_path=content`. El borrador completo ya existía
antes de trocearse en tokens; sólo se emitía sin revisar.

## Qué hace y qué no

Repara lo que es determinista: quita del texto los marcadores que no resuelven
y, cuando una afirmación entera dependía de uno, la marca como no sustentada.

**No pretende verificar que el fragmento sostenga la afirmación.** Eso exige un
verificador semántico, y el diagnóstico es explícito en que no debe presentarse
como garantía de verdad. Aquí sólo se cierra la brecha mecánica: ninguna cita
emitida apunta a algo que no está en el registro del turno.
"""
import logging
import re

logger = logging.getLogger(__name__)

MARCADOR = re.compile(r"\[([CE]\d+)\]")


def validar_borrador(texto: str, registry) -> dict:
    """
    Revisa un borrador contra el registro del turno.

    Devuelve `{texto, marcadores_invalidos, frases_sin_respaldo, reparado}`.
    """
    if not texto or registry is None:
        return {"texto": texto, "marcadores_invalidos": [],
                "frases_sin_respaldo": [], "reparado": False}

    usados = MARCADOR.findall(texto)
    invalidos = sorted({m for m in usados if registry.resolve(m) is None})
    if not invalidos:
        return {"texto": texto, "marcadores_invalidos": [],
                "frases_sin_respaldo": [], "reparado": False}

    logger.warning(
        f"Borrador con {len(invalidos)} marcador(es) fuera del registro: "
        f"{', '.join(invalidos)}. Se reparan antes de emitir."
    )

    cuerpo, _, fuentes = texto.partition("FUENTES")
    invalidos_set = set(invalidos)
    sin_respaldo: list[str] = []
    salida: list[str] = []

    # Se trabaja por frase: si al quitar los marcadores inválidos una frase se
    # queda sin ninguna cita, es una afirmación que colgaba de una referencia
    # que no existe.
    for frase in _frases(cuerpo):
        marcas = set(MARCADOR.findall(frase))
        if not marcas & invalidos_set:
            salida.append(frase)
            continue
        quedan = marcas - invalidos_set
        limpia = MARCADOR.sub(
            lambda m: "" if m.group(1) in invalidos_set else m.group(0), frase
        )
        limpia = re.sub(r"\s{2,}", " ", limpia).strip()
        if quedan:
            salida.append(limpia)
        else:
            sin_respaldo.append(limpia)
            salida.append(
                (limpia + " [SIN RESPALDO EN LAS FUENTES DE ESTA RESPUESTA]")
                if limpia else ""
            )

    nuevo = " ".join(s for s in salida if s)
    if fuentes:
        renglones = [
            ln for ln in fuentes.splitlines()
            if not (set(MARCADOR.findall(ln)) & invalidos_set)
        ]
        nuevo += "\n\nFUENTES" + "\n".join(renglones)

    return {
        "texto": nuevo,
        "marcadores_invalidos": invalidos,
        "frases_sin_respaldo": sin_respaldo,
        "reparado": True,
    }


def _frases(texto: str) -> list[str]:
    """
    Corta por frase conservando los saltos de línea, que en estas respuestas
    separan elementos de lista y son parte del sentido.
    """
    piezas: list[str] = []
    for linea in (texto or "").splitlines():
        if not linea.strip():
            piezas.append("\n")
            continue
        trozos = re.split(r"(?<=[.;:])\s+", linea)
        piezas.extend(t for t in trozos if t.strip())
        piezas.append("\n")
    return piezas
