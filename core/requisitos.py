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
#
# Cada entrada declara **un dato pedido**, con la lista de campos que pueden
# probarlo. La distinción la fijó COFECE en I2 de su revisión del 23-sep:
#
#   "`AND` se aplica a los datos distintos efectivamente pedidos. `OR` se
#    reserva a fuentes alternativas que prueben EL MISMO dato."
#
# Antes esto era una sola lista plana por patrón, y `verificar` la aprobaba con
# cualquiera de sus campos. El efecto medido: en H14 bastaba `judicialBody`
# —el órgano que DICTA la resolución— para dar por satisfecha una pregunta
# sobre el tribunal relacionado y su expediente, que son otro papel y otros dos
# campos. H14 salía PASS en las tres repeticiones porque el modelo acertaba,
# no porque el control lo sostuviera.
#
# Por eso los patrones van separados por dato: una pregunta que sólo pide el
# tribunal no debe exigir además el expediente. Exigir de más devuelve al
# agente a abstenerse sobre datos que nadie pidió.
_CAMPOS_PEDIDOS = [
    # Sólo en forma INTERROGATIVA. "del Segundo Tribunal Colegiado" nombra el
    # órgano; "¿qué tribunal colegiado?" lo pregunta. La primera versión
    # disparaba con cualquier mención y exigía campos del registro a una
    # comparación que sólo estaba identificando su documento.
    (re.compile(r"\bqu[ée]\s+tribunal\b|\bcu[áa]l\s+tribunal\b|"
                r"\bde\s+qu[ée]\s+tribunal\b", re.IGNORECASE),
     "tribunal relacionado",
     ["relatedCollegiateCourt"],
     "el tribunal relacionado, tomado del registro"),
    # Va aparte del tribunal: "¿qué expediente?" y "¿qué tribunal?" son dos
    # datos. H14 pide los dos; una pregunta que pida uno no exige el otro.
    (re.compile(r"\bqu[ée]\s+expediente\b|\bcu[áa]l\s+expediente\b|"
                r"\bn[úu]mero\s+de\s+expediente\b", re.IGNORECASE),
     "expediente relacionado",
     ["relatedTccCaseFile"],
     "el expediente relacionado, tomado del registro"),
    # El órgano EMISOR es un papel distinto del relacionado, y por eso tiene
    # su propio patrón y su propio campo. Confundirlos fue el defecto de H17,
    # donde el Juzgado Tercero apareció como Tribunal Colegiado.
    (re.compile(r"\bqu[ée]\s+[óo]rgano\b|\bcu[áa]l\s+[óo]rgano\b|"
                r"\bde\s+qu[ée]\s+[óo]rgano\b|\bqui[ée]n\s+(?:dict[óo]|emiti[óo])\b",
                re.IGNORECASE),
     "órgano emisor",
     ["judicialBody", "authority"],
     "el órgano que dictó el documento, tomado del registro"),
    (re.compile(r"\bqui[ée]n(?:es)?\s+(?:vot[óo]|resolvi[óo]|firm)|"
                r"\bcomisionad[oa]s?\b|\bintegrantes\b", re.IGNORECASE),
     "quiénes decidieron",
     ["decisionOfficials"],
     "quiénes decidieron, tomados del registro"),
    # Los dos campos de disidencia SÍ son alternativas del mismo dato: el
    # servicio lo expone en uno u otro según el documento.
    (re.compile(r"\bvot[oó]\s+(?:particular|concurrente|en\s+contra|disidente)|"
                r"\bdiscrep\w+|\ben\s+contra\b", re.IGNORECASE),
     "votos disidentes",
     ["dissentingOpinions", "dissentingAndConcurringOpinions"],
     "los votos disidentes, tomados del registro"),
    (re.compile(r"\bmulta\w*\b.{0,40}\b(?:a\s+qui[ée]n|agente|impuso)|"
                r"\ba\s+qui[ée]n\s+se\s+(?:le\s+)?multó", re.IGNORECASE),
     "agentes multados",
     ["agentFines"],
     "los agentes multados y sus montos, tomados del registro"),
    # Qué resolvió una sentencia. H18: los datos estaban en
    # `judicialDecisionEffects` —"el Pleno de la Comisión Nacional
    # Antimonopolio deberá…"—, no en los criterios. El agente usó sólo
    # `buscar_criterios` y acabó confundiendo emisor con destinatario.
    #
    # Estos cuatro campos sí son alternativas: cada documento expresa el
    # sentido de lo resuelto en el que tenga poblado.
    (re.compile(r"\bqu[ée]\s+se\s+resolvi[óo]\b|\bqu[ée]\s+efectos?\b|"
                r"\bpuntos?\s+resolutivos?\b|\bqu[ée]\s+orden[óa]\b|"
                r"\bsentido\s+del?\s+(?:amparo|fallo|sentencia)\b",
                re.IGNORECASE),
     "qué se resolvió",
     ["judicialDecisionEffects", "senseOfAmparo", "scopeOfCompliance",
      "judgmentImplementation"],
     "qué se resolvió, tomado del registro"),
    # La autoridad que dictó el acto reclamado es OTRO dato, y confundirlo con
    # la obligada al cumplimiento fue exactamente el error de H18-B: COFECE
    # dictó la multa, la CNA quedó obligada a reindividualizarla.
    (re.compile(r"\bqu[ée]\s+autoridad\b|\bcu[áa]l\s+autoridad\b|"
                r"\bautoridad\s+(?:responsable|emisora)\b|"
                r"\bacto\s+reclamado\b", re.IGNORECASE),
     "autoridad del acto reclamado",
     ["originAdministrativeAuthority"],
     "la autoridad que dictó el acto reclamado, tomada del registro"),
]



# Pedir uno o más documentos que DEMUESTREN algo.
#
# H16-A, el FAIL crítico del 25-sep: la pregunta pide "una resolución VCN en la
# que dos aumentos de capital se hayan tratado como operaciones
# independientes", y la respuesta presentó VCN-005-2018 apoyándose en un
# criterio de VCN-005-2024. El documento existía y el pasaje era auténtico; la
# atribución no.
#
# La traza tenía `requisitos=[]`: no había ninguna defensa que exigiera
# acreditar la propiedad del ejemplo.
#
# El patrón es estructural y cubre los tres frentes abiertos a la vez, que es
# la señal de que es el mecanismo y no un parche por pregunta:
#
#   H16  "Busca una resolución VCN en la que…"        1 resolución
#   H08  "Busca una resolución VCN que lo explique"   1 resolución
#   H17  "Muéstrame dos sentencias… que lo expliquen" 2 sentencias
#
# COFECE es explícito en que esto no puede activarse por una palabra del
# dominio —"no una regla que se active por la palabra «independiente»"— ni
# codificarse por número de pregunta. Lo que se detecta es la petición de
# ejemplares, su cantidad y su tipo documental; **qué demuestra el pasaje lo
# juzga el modelo**, y el código sólo comprueba que el documento traiga
# evidencia propia.
_PIDE_EJEMPLARES = re.compile(
    r"\b(?:busca|búscame|buscame|mu[ée]strame|ens[ée]ñame|encuentra|"
    r"identifica|dame|cita|se[ñn]ala)\b[^.?!]{0,40}?"
    r"\b(?P<cantidad>un|una|dos|tres|cuatro|cinco)\b\s+"
    r"(?P<tipo>resoluci[óo]n(?:es)?|sentencias?|criterios?|precedentes?|"
    r"expedientes?|casos?|asuntos?)\b",
    re.IGNORECASE,
)
_CARDINALES = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}

# Qué clase de documento satisface la petición. Una sentencia no es una
# resolución de la autoridad de competencia: fue el defecto de q10 en
# septiembre y aquí decide si H17 se cumple.
_TIPO_ESPERADO = {
    "resolucion": "resolucion", "resoluciones": "resolucion",
    "sentencia": "sentencia", "sentencias": "sentencia",
}


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

    m = _PIDE_EJEMPLARES.search(query or "")
    if m:
        import unicodedata as _ud
        crudo = m.group("tipo").lower()
        base = "".join(c for c in _ud.normalize("NFD", crudo)
                       if _ud.category(c) != "Mn")
        cantidad = _CARDINALES.get(m.group("cantidad").lower(), 1)
        # La propiedad que deben demostrar es lo que sigue a la mención, en
        # las palabras del usuario. No se interpreta aquí: viaja al modelo y
        # al verificador para que la juzguen contra el pasaje.
        propiedad = (query[m.end():].strip(" ,;:")[:220] or "").strip()
        req.append({
            "tipo": "ejemplo",
            "valor": cantidad,
            "tipo_documento": _TIPO_ESPERADO.get(base),
            "propiedad": propiedad,
            "descripcion": (
                f"{cantidad} {crudo} con evidencia propia que demuestre: "
                f"{propiedad[:90]}"
            ),
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
    # Un requisito por DATO pedido, no uno por patrón con todos sus campos
    # dentro. Dos datos distintos se comprueban por separado; sólo las fuentes
    # alternativas del mismo dato comparten requisito.
    for patron, papel, campos, desc in _CAMPOS_PEDIDOS:
        if patron.search(query or ""):
            req.append({
                "tipo": "campos_registro",
                "papel": papel,
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
        elif r["tipo"] == "ejemplo":
            # Cuántos documentos DISTINTOS traen evidencia propia del tipo
            # pedido. Lo que el código puede afirmar es la procedencia; que el
            # pasaje demuestre la propiedad lo juzgan el modelo y la revisión
            # semántica, y por eso el detalle lo dice expresamente.
            from core.fuentes import clasificar_fuente
            docs_con_evidencia = {}
            for d in (docs or []):
                cl = case_link_de(d)
                if not cl:
                    continue
                texto = (d.get("content") or d.get("text") or "").strip()
                if not texto:
                    continue          # un registro no demuestra una propiedad
                docs_con_evidencia.setdefault(cl, clasificar_fuente(cl))
            esperado = r.get("tipo_documento")
            if esperado:
                aptos = [c for c, t in docs_con_evidencia.items() if t == esperado]
            else:
                aptos = list(docs_con_evidencia)
            ok = len(aptos) >= r["valor"]
            # La razón concreta, porque es el texto que lee el modelo y una
            # razón equivocada lo manda a buscar lo que no falta.
            if ok:
                motivo = ("El código comprueba la procedencia, no que el "
                          "pasaje demuestre la propiedad pedida: eso lo tienes "
                          "que sostener tú con el texto.")
            elif not docs_con_evidencia:
                motivo = ("No hay ningún documento con criterio propio "
                          "recuperado. Un registro de expediente no demuestra "
                          "una propiedad: hace falta el texto.")
            elif esperado and not aptos:
                otros = sorted(set(docs_con_evidencia.values()))
                motivo = (f"Los documentos con evidencia son de tipo "
                          f"{', '.join(otros)}, y se pidió {esperado}. "
                          f"Una sentencia no es una resolución de la autoridad "
                          f"de competencia.")
            else:
                motivo = (f"Sólo {len(aptos)} documento(s) distinto(s) tienen "
                          f"evidencia propia. Dos fragmentos del mismo "
                          f"documento cuentan como uno.")
            detalle = (
                f"{len(aptos)} de {r['valor']} con evidencia propia"
                + (f" ({esperado})" if esperado else "") + ". " + motivo
            )

        elif r["tipo"] == "campos_registro":
            # Cada requisito es UN dato; sus campos son fuentes alternativas
            # que prueban ese mismo dato, así que basta uno de ellos. Lo que
            # ya no ocurre es aprobar un dato con el campo de otro: eso vive
            # ahora en requisitos separados y se exigen todos.
            traidos = [
                c for c in r["valor"]
                if any(d.get(c) not in (None, "", [], {}) for d in (docs or []))
            ]
            ok = bool(traidos)
            papel = r.get("papel", "el dato pedido")
            detalle = (
                f"{papel}: presente en " + ", ".join(traidos) if ok
                else f"falta {papel}. Ninguno de estos campos llegó con valor: "
                     + ", ".join(r["valor"])
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
