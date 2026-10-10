"""
Resultados de las herramientas de Norma+ para un modelo que no es el nuestro.

El agente de normaplus.ai recibe sus resultados con señales e instrucciones
pensadas para él (`COMO_DEBES_DESCRIBIR_LA_COBERTURA`, "no lo afirmes",
marcadores `[E1]`). En un conector de Claude o ChatGPT eso no sirve y además
está prohibido: los directorios rechazan herramientas que le dicen al modelo
cómo comportarse. Aquí cada resultado se reduce a **datos**:

- campos con nombre en español, no los de la API (el modelo del usuario los
  copiaría: "la revisión del campo dissentingOpinions");
- URLs absolutas de normaplus.ai para cada expediente y cada párrafo;
- las advertencias, como hechos ("la información disponible no indica qué
  amparo motivó el cumplimiento"), no como órdenes.

Lo que sí se conserva intacto es lo que calcula el código: cifras, plazos,
cobertura, historia procesal.
"""
from __future__ import annotations

from core.enlaces_fuentes import ruta_expediente, ruta_parrafo
from core.fuentes import clasificar_fuente

# Nombre en español de cada campo del registro. Los que no están aquí no se
# exponen: un campo nuevo de la API no aparece en el conector con su nombre
# interno sin que alguien lo revise.
ETIQUETAS = {
    "caseLink": "expediente",
    "name": "nombre",
    "authority": "autoridad",
    "typeOfProcedure": "tipo_de_procedimiento",
    "natureOfResolution": "naturaleza_de_la_resolucion",
    "senseOfResolution": "sentido_de_la_resolucion",
    "economicAgents": "agentes_economicos",
    "notifyingParties": "partes_notificantes",
    "agentFines": "multas_por_agente",
    "relevantMarkets": "mercados_relevantes",
    "operationDescription": "descripcion_de_la_operacion",
    "applicableLaw": "ley_aplicable",
    "resource": "recurso_interpuesto",
    "dissentingOpinions": "votos_particulares",
    "dissentingAndConcurringOpinions": "votos_particulares_y_concurrentes",
    "decisionOfficials": "integrantes_que_resolvieron",
    "notificationDate": "fecha_de_notificacion",
    "startAgreementDate": "fecha_del_acuerdo_de_inicio",
    "basicInfoRequestDate": "fecha_de_requerimiento_de_informacion_basica",
    "additionalInfoRequestDate": "fecha_de_requerimiento_de_informacion_adicional",
    "admissionDate": "fecha_de_admision",
    "resolutionDate": "fecha_de_resolucion",
    "resolutionIssueDate": "fecha_de_emision_de_la_resolucion",
    "modifiedInitialResolutionDate": "fecha_de_la_resolucion_modificada",
    "amparoComplianceResolutionDate": "fecha_de_la_resolucion_en_cumplimiento",
    "amparoComplianceResolutionIssueDate": "fecha_de_emision_del_cumplimiento",
    "scopeOfCompliance": "alcance_del_cumplimiento",
    "judgmentImplementation": "como_se_cumplio",
    "judicialCaseFile": "expediente_judicial",
    "judicialBody": "organo_judicial",
    "accumulatedCaseFiles": "expedientes_acumulados",
    "originAdministrativeAuthority": "autoridad_de_origen",
    "originAdministrativeResolutionDate": "fecha_de_la_resolucion_reclamada",
    "claimedActs": "actos_reclamados",
    "challengedNorms": "normas_impugnadas",
    "complaintFilingDate": "fecha_de_presentacion_de_la_demanda",
    "complaintAdmissionDate": "fecha_de_admision_de_la_demanda",
    "expandedComplaintAdmissionDate": "fecha_de_admision_de_la_ampliacion",
    "judgmentDate": "fecha_de_la_sentencia",
    "senseOfAmparo": "sentido_del_amparo",
    "judicialDecisionEffects": "efectos_de_la_sentencia",
    "reviewResolutionDate": "fecha_de_la_resolucion_de_revision",
    "senseOfReview": "sentido_de_la_revision",
    "finalAmparoResult": "resultado_final_del_amparo",
    "originAmparoCaseFiles": "amparos_revisados",
    "appealedJudgmentBody": "organo_de_la_sentencia_recurrida",
    "appealedJudgmentDate": "fecha_de_la_sentencia_recurrida",
    "principalAppellants": "recurrentes",
    "adhesiveAppellants": "recurrentes_adhesivos",
    "relatedTccCaseFile": "expediente_del_tribunal_relacionado",
    "relatedCollegiateCourt": "tribunal_relacionado",
    "relatedTccDecisionDate": "fecha_de_la_decision_del_tribunal",
    "expediente_principal": "expediente_principal",
}

# Campos de efecto en la historia procesal, con su nombre en español.
_EFECTOS = {
    "senseOfAmparo": "sentido_del_amparo",
    "senseOfReview": "sentido_de_la_revision",
    "finalAmparoResult": "resultado_final_del_amparo",
    "senseOfResolution": "sentido_de_la_resolucion",
    "scopeOfCompliance": "alcance_del_cumplimiento",
    "judgmentImplementation": "como_se_cumplio",
    "judicialDecisionEffects": "efectos_de_la_sentencia",
}


def url_absoluta(base: str, ruta: str) -> str:
    return f"{base.rstrip('/')}{ruta}" if ruta else ""


def expediente(registro: dict, base: str) -> dict:
    """Un registro de la API como ficha en español, con su URL."""
    salida: dict = {}
    for campo, valor in registro.items():
        etiqueta = ETIQUETAS.get(campo)
        if etiqueta is None or valor in (None, "", [], {}):
            continue
        salida[etiqueta] = valor
    cl = registro.get("caseLink") or registro.get("expediente_principal")
    if registro.get("caseLink"):
        salida["tipo_de_fuente"] = clasificar_fuente(registro["caseLink"])
        salida["url"] = url_absoluta(base, ruta_expediente(registro["caseLink"]))
    return salida


def criterio(doc: dict, anchor: str | None, base: str) -> dict:
    """Un criterio (párrafo de una resolución o sentencia), con URL al párrafo."""
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    cl = meta.get("id_expediente") or doc.get("caseLink") or ""
    salida = {
        "expediente": cl,
        "tipo_de_fuente": clasificar_fuente(cl),
        "titulo": meta.get("title"),
        "articulo": meta.get("article"),
        "paginas": meta.get("paginas_parrafos"),
        "texto": doc.get("text"),
        "quien_lo_sostiene": _voz(doc),
        "url_expediente": url_absoluta(base, ruta_expediente(cl)),
    }
    if doc.get("autor_del_voto"):
        salida["autor_del_voto"] = doc["autor_del_voto"]
    if anchor and doc.get("id"):
        salida["url_parrafo"] = url_absoluta(base, ruta_parrafo(cl, anchor, str(doc["id"])))
    return {k: v for k, v in salida.items() if v not in (None, "", [], {})}


def _voz(doc: dict) -> str:
    voz = doc.get("voz") or ""
    return {
        "mayoria": "la resolución o sentencia (mayoría)",
        "voto_particular": "un voto particular",
        "voto_concurrente": "un voto concurrente",
    }.get(voz, "no identificado en el texto recuperado")


def historia(h: dict | None, base: str, documento: str | None = None) -> dict | None:
    """
    La historia procesal de un documento como datos.

    Las notas del mapa (`NO_ENLAZADO`, `LIMITE`) están escritas para nuestro
    agente, en imperativo. Aquí se reescriben como hechos.
    """
    if not h:
        return None
    salida: dict = {}
    deriva = []
    for a in h.get("deriva_de", []):
        item = {"documento": a.get("posterior"), "deriva_de": a.get("origen"),
                "estado_del_enlace": _estado(a.get("estado"))}
        if a.get("origen"):
            item["tipo_de_fuente_del_origen"] = clasificar_fuente(a["origen"])
            item["url_del_origen"] = url_absoluta(base, ruta_expediente(a["origen"]))
        if a.get("estado") == "externo":
            item["nota"] = (f"El origen informado ({a.get('referencia_informada', '')}) "
                            "no está en el acervo de Norma+.")
        elif a.get("estado") == "pendiente":
            item["nota"] = "El registro no informa de qué documento deriva."
        deriva.append(item)
    if deriva:
        salida["deriva_de"] = deriva

    posteriores = []
    for p in h.get("actuaciones_posteriores", []):
        item = {
            "expediente": p.get("expediente"),
            "deriva_de": p.get("deriva_de"),
            "tipo": p.get("tipo"),
            "tipo_de_fuente": clasificar_fuente(p.get("expediente") or ""),
            "fecha_de_la_resolucion": p.get("fecha_de_la_resolucion"),
            "estado_del_enlace": _estado(p.get("enlace")),
            "url": url_absoluta(base, ruta_expediente(p.get("expediente") or "")),
        }
        for campo, etiqueta in _EFECTOS.items():
            if p.get(campo) not in (None, "", [], {}):
                item[etiqueta] = p[campo]
        if "sentencia_que_cumple" in p:
            item["sentencia_de_amparo_que_cumple"] = "no informada"
        posteriores.append({k: v for k, v in item.items() if v not in (None, "")})
    if posteriores:
        salida["actuaciones_posteriores"] = posteriores
    if h.get("actuaciones_no_mostradas"):
        salida["actuaciones_no_mostradas"] = h["actuaciones_no_mostradas"]

    resumen = resumen_posterior(posteriores, documento)
    if resumen:
        salida = {"resumen": resumen, **salida}

    cumplimientos = [p["expediente"] for p in posteriores
                     if "cumplimiento" in str(p.get("tipo", "")).lower()]
    amparos = [p["expediente"] for p in posteriores
               if str(p.get("tipo", "")).lower().startswith("amparo indirecto")]
    if cumplimientos and amparos:
        salida["nota"] = (
            f"La información disponible no indica cuál de los amparos "
            f"({', '.join(amparos)}) dio lugar a {', '.join(cumplimientos)}, "
            "ni a quién benefició cada amparo.")
    return salida or None


# Sentidos de una revisión o de una resolución posterior que cambian lo que
# dice el documento revisado.
_CAMBIA = ("modifica", "revoca", "deja insubsistente", "sobresee")


def resumen_posterior(posteriores: list[dict], documento: str | None = None) -> str | None:
    """
    Una oración con lo que pasó después del documento.

    Existe porque la lista de actuaciones posteriores, sola, no basta: en la
    primera medición del conector Claude calculó los plazos de una sentencia
    de amparo con su ficha y no mencionó que un tribunal colegiado la había
    modificado (H05, 0 de 3), aunque el dato venía en la historia procesal.
    Va como hecho, al principio de la ficha, no como instrucción.
    """
    if not posteriores:
        return None
    partes, cambia = [], False
    for p in posteriores:
        sentido = (p.get("sentido_de_la_revision") or p.get("sentido_del_amparo")
                   or p.get("sentido_de_la_resolucion") or [])
        sentido = [sentido] if isinstance(sentido, str) else list(sentido)
        texto = f"{p.get('expediente')} ({str(p.get('tipo') or 'actuación').lower()}"
        if p.get("fecha_de_la_resolucion"):
            texto += f", {p['fecha_de_la_resolucion']}"
        if p.get("deriva_de") and p["deriva_de"] != documento:
            texto += f", deriva de {p['deriva_de']}"
        texto += ")"
        if sentido:
            texto += f", sentido: {', '.join(map(str, sentido))}"
        if p.get("resultado_final_del_amparo"):
            texto += f"; resultado final del amparo: {', '.join(map(str, p['resultado_final_del_amparo']))}"
        partes.append(texto)
        # Sólo la revisión de este mismo documento lo modifica; la de un
        # amparo posterior en la cadena modifica ese amparo, no éste.
        directa = documento is None or p.get("deriva_de") == documento
        if directa and p.get("sentido_de_la_revision") and any(
                c in str(x).lower() for x in sentido for c in _CAMBIA):
            cambia = True
    resumen = f"Actuaciones posteriores a este documento: {'; '.join(partes)}."
    if cambia:
        resumen += (" Una revisión posterior modificó o revocó este documento: su "
                    "sentido y sus efectos, tal como aparecen en esta ficha, son los "
                    "anteriores a esa revisión.")
    return resumen


def _estado(estado: str | None) -> str | None:
    return {
        "resuelto": "acreditado por los registros",
        "resuelto_sin_numero": "acreditado por órgano y fecha; el documento no trae número de expediente",
        "externo": "el documento de origen no está en el acervo",
        "pendiente": "no determinado",
    }.get(estado or "", estado)


def agregacion(r: dict, base: str) -> dict:
    """El resultado de un cálculo sobre el acervo, sin instrucciones al modelo."""
    salida = {
        "operacion": r.get("operacion"),
        "metrica": r.get("metrica"),
        "resultado": r.get("resultado"),
        "mediana": r.get("mediana"),
        "asuntos_analizados": r.get("procesados"),
        "asuntos_con_dato": r.get("con_valor"),
        "asuntos_sin_dato": r.get("sin_valor"),
        "recorrio_el_universo_completo": r.get("cobertura_completa"),
        "fecha_inicial_usada": _nombre_de_fecha(r.get("campo_inicio")),
        "fecha_final_usada": _nombre_de_fecha(r.get("campo_fin")),
    }
    descripcion = r.get("COMO_DEBES_DESCRIBIR_LA_COBERTURA")
    if descripcion:
        # Lo que sigue a "USA EXACTAMENTE…" es una instrucción a nuestro
        # agente; lo anterior es la descripción factual del cálculo.
        salida["descripcion_del_calculo"] = descripcion.split(" USA EXACTAMENTE")[0].replace(
            " OJO: el cálculo mezcla", " El cálculo mezcla").split(" No lo atribuyas")[0].strip()
    if r.get("ganadores"):
        salida["expedientes_del_resultado"] = [
            {k: v for k, v in {
                "expediente": g.get("case_link"),
                "valor": g.get("valor"),
                "url": url_absoluta(base, ruta_expediente(g.get("case_link") or "")),
            }.items() if v not in (None, "")}
            for g in r["ganadores"]
        ]
    if r.get("ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS"):
        x = r["ACTOS_DE_CUMPLIMIENTO_EXCLUIDOS"]
        salida["resoluciones_en_cumplimiento_excluidas"] = {
            "cantidad": x.get("count"),
            "actos": x.get("actos"),
            "motivo": "Son resoluciones en cumplimiento de amparo de asuntos que ya "
                      "cuentan por su resolución original; se excluyen para no contar "
                      "el mismo asunto dos veces.",
        }
    if r.get("PLAZO_HASTA_RESOLUCION_FINAL"):
        salida["plazo_hasta_la_resolucion_final"] = {
            "asuntos": r["PLAZO_HASTA_RESOLUCION_FINAL"].get("asuntos"),
            "nota": "En estos asuntos el plazo corre hasta la resolución en cumplimiento "
                    "de amparo e incluye el tiempo del litigio.",
        }
    if r.get("AUTORIDADES_EN_EL_CALCULO"):
        salida["autoridades_en_el_calculo"] = r["AUTORIDADES_EN_EL_CALCULO"]
    if r.get("EXPEDIENTES_CON_FECHAS_INCONSISTENTES"):
        x = r["EXPEDIENTES_CON_FECHAS_INCONSISTENTES"]
        salida["expedientes_con_fechas_inconsistentes"] = {
            "cantidad": x.get("count"), "ejemplos": x.get("ejemplos"),
            "nota": "La fecha final es anterior a la inicial; se excluyeron del cálculo."}
    if r.get("fuera_de_cobertura_del_calendario"):
        salida["fuera_de_cobertura_del_calendario"] = {
            "cantidad": r["fuera_de_cobertura_del_calendario"],
            "nota": "Sus fechas caen fuera del calendario de días inhábiles disponible; "
                    "su conteo de días hábiles es aproximado."}
    for clave, nombre in (("ADVERTENCIA_VALORES_AMBIGUOS", "montos_ambiguos_excluidos"),
                          ("ADVERTENCIA_CONFIDENCIALES", "montos_no_publicados")):
        if r.get(clave):
            salida[nombre] = r[clave].split(" Si el usuario")[0].split(" Dilo")[0]
    if r.get("ADVERTENCIA_COBERTURA_PARCIAL"):
        salida["cobertura_parcial"] = r["ADVERTENCIA_COBERTURA_PARCIAL"].split(" Preséntalo")[0]
    return {k: v for k, v in salida.items() if v not in (None, "", [], {})}


def _nombre_de_fecha(campo: str | None) -> str | None:
    """El campo de fecha con su nombre en español, nunca el de la API."""
    if not campo:
        return None
    if campo == "default por tipo":
        return "acuerdo de inicio (VCN, IO) o notificación (CNT), según el tipo"
    return ETIQUETAS.get(campo, "otra fecha del registro").replace("fecha_de_", "").replace("_", " ")


def plazo(r: dict) -> dict:
    """Un cómputo entre dos fechas."""
    alcance = r.get("ALCANCE_DE_LA_CIFRA") or {}
    salida = {
        "fecha_inicio": r.get("fecha_inicio"),
        "fecha_fin": r.get("fecha_fin"),
        "dias_habiles": r.get("dias_habiles"),
        "dias_naturales": r.get("dias_naturales"),
        "calendario": r.get("institucion"),
        "convencion": r.get("convencion"),
        "dentro_de_cobertura_del_calendario": r.get("dentro_de_cobertura_del_calendario"),
        "que_mide": alcance.get("denominacion"),
        "ajusta_suspensiones": alcance.get("ajusta_suspensiones"),
    }
    acuerdos = r.get("ACUERDOS_DE_SUSPENSION_COINCIDENTES")
    if acuerdos:
        # Sólo los acuerdos; la "regla" que los acompaña es para nuestro agente.
        salida["acuerdos_de_suspension_que_coinciden"] = {
            "acuerdos": acuerdos.get("acuerdos") if isinstance(acuerdos, dict) else acuerdos,
            "nota": "Coinciden en fechas con el periodo calculado; no se evaluó si "
                    "aplican al expediente.",
        }
    return {k: v for k, v in salida.items() if v not in (None, "", [], {})}
