"""
Qué exige la pregunta, comprobado contra lo que se recuperó.

C03 del diagnóstico de COFECE (21-sep-2026). El control de suficiencia unía el
texto de todos los documentos y medía coincidencia de palabras. Dos
consecuencias, las dos medidas:

1. `_terminos` usa `[a-z]{4,}`, así que **los números de expediente, las fechas
   y los artículos desaparecen**. Verificado:

       _terminos("criterio del amparo 178/2017 del 2TCC") → {'amparo', 'criterio'}

   Una pregunta sobre un documento exacto se aprobaba con vocabulario de
   cualquier otro documento del mismo tema.

2. Al unir los textos, evidencia de **una** fuente podía cubrir una consulta
   que pedía **dos posturas distintas**. En H15 el agente afirmó que COFECE y
   el Segundo Tribunal Colegiado "coinciden plenamente" sin haber recuperado el
   criterio del 178/2017.

Y en H19 el check registró SUFFICIENT con cobertura 0.88 aunque "mayoria"
figuraba entre los términos ausentes: el criterio recuperado era un voto
particular, y el check no distingue voz.

## Lo que hace este módulo

Construye **requisitos por componente** —documento, voz, comparación— y los
comprueba contra la evidencia identificada, no contra una bolsa de palabras.

El check léxico sigue existiendo como señal auxiliar. Lo que ya no hace es
aprobar identidad ni voz, que es lo que el diagnóstico prohíbe expresamente.
"""
import re

from core.voz import clasificar_voz, MAYORIA, NO_IDENTIFICADA, VOTO_PARTICULAR

# Pide expresamente la postura del órgano, no la de uno de sus integrantes.
_PIDE_MAYORIA = re.compile(
    r"\b(?:la\s+)?mayor[íi]a\b|\bel\s+tribunal\s+(?:sostuvo|resolvi[óo]|"
    r"consider[óo]|determin[óo])|\bel\s+pleno\b|\bqu[ée]\s+sostuvo\s+el\b|"
    r"\bcriterio\s+del\s+tribunal\b",
    re.IGNORECASE,
)
_PIDE_VOTO = re.compile(
    r"\bvoto\s+particular\b|\bvoto\s+concurrente\b|\bvoto\s+disidente\b|"
    r"\balg[úu]n\s+voto\b|\bdiscrepa\w*\b|\bse\s+apart\w+\b",
    re.IGNORECASE,
)
_COMPARA = re.compile(
    r"\bcompar\w+\b|\bfrente\s+a\b|\bcontra\s+lo\s+que\b|\bdiferencias?\s+entre\b|"
    r"\bcoincide\w*\b|\bversus\b|\bvs\.?\b",
    re.IGNORECASE,
)


# Preguntas que exigen campos concretos del registro, no criterios. Van con
# los nombres reales del modelo para que el requisito sea comprobable.
_CAMPOS_PEDIDOS = [
    # Sólo en forma INTERROGATIVA. "del Segundo Tribunal Colegiado" nombra el
    # órgano; "¿qué tribunal colegiado?" lo pregunta. La primera versión
    # disparaba con cualquier mención y exigía campos del registro a una
    # comparación que sólo estaba identificando su documento.
    (re.compile(r"\bqu[ée]\s+tribunal\b|\bcu[áa]l\s+(?:tribunal|[óo]rgano)\b|"
                r"\bqu[ée]\s+expediente\b|\bde\s+qu[ée]\s+(?:tribunal|[óo]rgano)\b|"
                r"\bqu[ée]\s+[óo]rgano\b", re.IGNORECASE),
     ["relatedCollegiateCourt", "relatedTccCaseFile", "judicialBody"],
     "el tribunal y su expediente, tomados del registro"),
    (re.compile(r"\bqui[ée]n(?:es)?\s+(?:vot[óo]|resolvi[óo]|firm)|"
                r"\bcomisionad[oa]s?\b|\bintegrantes\b", re.IGNORECASE),
     ["decisionOfficials", "dissentingOpinions",
      "dissentingAndConcurringOpinions"],
     "quiénes decidieron, tomados del registro"),
    (re.compile(r"\bmulta\w*\b.{0,40}\b(?:a\s+qui[ée]n|agente|impuso)|"
                r"\ba\s+qui[ée]n\s+se\s+(?:le\s+)?multó", re.IGNORECASE),
     ["agentFines"],
     "los agentes multados y sus montos, tomados del registro"),
]


def construir_requisitos(query: str, identidades: list[dict] | None) -> list[dict]:
    """
    Requisitos verificables de una pregunta.

    `identidades` son las menciones ya resueltas contra el acervo; de ahí sale
    el requisito de documento, que es el que el check léxico no podía ver.
    """
    req: list[dict] = []
    documentos: list[str] = []
    for i in (identidades or []):
        # Una mención ambigua no fija un documento: exige desambiguar antes.
        if not i.get("ambiguo") and i.get("candidatos"):
            documentos.append(i["candidatos"][0])

    for d in documentos:
        req.append({
            "tipo": "documento",
            "valor": d,
            "descripcion": f"evidencia del documento {d}",
            "obligatorio": True,
        })

    if _PIDE_MAYORIA.search(query or "") and not _PIDE_VOTO.search(query or ""):
        req.append({
            "tipo": "voz",
            "valor": MAYORIA,
            "descripcion": "la postura mayoritaria, no un voto individual",
            "obligatorio": True,
        })
    elif _PIDE_VOTO.search(query or ""):
        req.append({
            "tipo": "voz",
            "valor": VOTO_PARTICULAR,
            "descripcion": "un voto particular o concurrente identificado",
            "obligatorio": True,
        })

    # Campos del registro que la pregunta pide expresamente.
    #
    # H14 de la revisión final: la pregunta pide identificar el tribunal
    # colegiado y su expediente, y las tres corridas usaron sólo
    # `buscar_criterios`. Los datos estaban en el registro —
    # `relatedTccCaseFile: 565/2023`, `relatedCollegiateCourt: Primer Tribunal
    # Colegiado…`— y nadie los fue a buscar. Un identificador canónico correcto
    # no equivale a haber recuperado todos los campos pedidos.
    for patron, campos, desc in _CAMPOS_PEDIDOS:
        if patron.search(query or ""):
            req.append({
                "tipo": "campos_registro",
                "valor": campos,
                "descripcion": desc,
                "obligatorio": True,
            })

    if _COMPARA.search(query or "") and len(documentos) >= 2:
        req.append({
            "tipo": "comparacion",
            "valor": documentos,
            "descripcion": (
                "evidencia de LOS DOS documentos: "
                + " y ".join(documentos)
            ),
            "obligatorio": True,
        })

    return req


def verificar(requisitos: list[dict], docs: list[dict]) -> dict:
    """
    Comprueba cada requisito contra la evidencia recuperada.

    Devuelve `{cumple, componentes, faltantes}`. Un requisito de documento se
    cumple con identidad, no con vocabulario; uno de voz, con la voz
    clasificada del fragmento.
    """
    from core.fuentes import case_link_de

    presentes = {case_link_de(d) for d in (docs or []) if case_link_de(d)}
    voces = {clasificar_voz(d)["voz"] for d in (docs or [])}

    componentes: list[dict] = []
    for r in requisitos:
        if r["tipo"] == "documento":
            ok = r["valor"] in presentes
            detalle = (
                f"recuperado" if ok
                else f"NO se recuperó evidencia de {r['valor']}"
            )
        elif r["tipo"] == "comparacion":
            faltan = [d for d in r["valor"] if d not in presentes]
            ok = not faltan
            detalle = (
                "los dos lados presentes" if ok
                else "falta evidencia de " + ", ".join(faltan)
            )
        elif r["tipo"] == "voz":
            if r["valor"] == MAYORIA:
                # No hay marca positiva de mayoría: lo que se puede comprobar
                # es que NO toda la evidencia sea un voto individual.
                solo_votos = voces and voces.issubset(
                    {VOTO_PARTICULAR, "voto_concurrente"}
                )
                ok = not solo_votos
                detalle = (
                    "toda la evidencia es de votos individuales; no hay "
                    "postura mayoritaria identificada" if solo_votos
                    else "hay evidencia que no es voto individual"
                )
            else:
                ok = bool(voces & {VOTO_PARTICULAR, "voto_concurrente"})
                detalle = (
                    "voto identificado" if ok
                    else "no se recuperó ningún voto particular identificado"
                )
        elif r["tipo"] == "campos_registro":
            # Se cumple si ALGUNO de los campos pedidos llegó con valor en
            # algún documento recuperado. No basta tener el expediente
            # correcto: hay que haber traído el campo.
            traidos = [
                c for c in r["valor"]
                if any(d.get(c) not in (None, "", [], {}) for d in (docs or []))
            ]
            ok = bool(traidos)
            detalle = (
                "presentes: " + ", ".join(traidos) if ok
                else "ninguno de estos campos llegó: " + ", ".join(r["valor"])
                     + ". Están en el registro del expediente, no en los "
                       "criterios: hay que consultarlo con buscar_expedientes"
            )
        else:
            ok, detalle = True, "sin verificación definida"

        componentes.append({
            "tipo": r["tipo"],
            "descripcion": r["descripcion"],
            "cumple": ok,
            "detalle": detalle,
            "obligatorio": r.get("obligatorio", True),
        })

    faltantes = [
        c for c in componentes if c["obligatorio"] and not c["cumple"]
    ]
    return {
        "cumple": not faltantes,
        "componentes": componentes,
        "faltantes": [c["descripcion"] for c in faltantes],
    }
