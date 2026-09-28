"""
¿El pasaje citado sostiene lo que la respuesta afirma?

I5 de la revisión de COFECE (23-sep-2026), y el hueco que llevábamos semanas
reportando como abierto. Los controles deterministas cerraron la brecha
mecánica —identidad, voz, campos, marcadores— pero ninguno mira el significado:

    H16-C recibió COMPLETOS los criterios 3931 y 3930, que dicen que en una
    sucesión de actos la concentración debe notificarse antes de la aportación
    que rebasa umbrales. La respuesta los usó para sostener lo contrario: que
    cada aumento constituye una concentración independiente.

No fue recuperación, no fue truncamiento y no fue una cita inválida. El
marcador resolvía, el documento era correcto y el texto estaba entero. Es
inversión del contenido de la fuente, y sólo se ve leyendo.

## Las dos reglas que lo hacen algo más que una segunda opinión

**1. `supported` no se acepta sin localizador.** El verificador devuelve, por
afirmación, un fragmento textual de la evidencia que la sostiene. El código
comprueba que ese fragmento exista de verdad en el documento citado. Un modelo
que aprueba todo no puede colarse: tendría que inventar citas verificables, y
las inventadas se caen solas.

    "No aceptar `supported` sin soporte localizado." — COFECE, §1.9

**2. Empieza en evaluación, sin bloquear.** Una segunda lectura del modelo es
tan falible como la primera; encadenar la publicación a ella cambia un modo de
falla por otro. El resultado se registra aparte y **no aprueba requisitos ni
detiene la respuesta** hasta que su propia medición lo justifique.

    "Durante esa evaluación, su resultado se registra aparte: no aprueba
     requisitos ni bloquea automáticamente la publicación." — COFECE, §I5

## Cómo se mide si sirve

La prueba que propusimos —correrlo contra las 60 respuestas ya adjudicadas y
exigir cero PASS rechazados— **es insuficiente**, y COFECE tuvo razón en
señalarlo: un verificador que apruebe todo la pasa con honores. Hacen falta las
dos direcciones a la vez, con negativos reales:

    correctas rechazadas   →  falsos positivos, vuelven tímido al agente
    incorrectas aceptadas  →  falsos negativos, es no tener verificador

H16-A y H16-C son los negativos reales de esta entrega. `evaluar()` mide ambas
direcciones sobre un conjunto etiquetado.
"""
import json
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
NOT_DETERMINED = "not_determined"

# Cuántas afirmaciones se mandan a revisar. El verificador lee el borrador
# completo, pero el presupuesto de salida se acota para que una respuesta larga
# no dispare el costo.
MAX_AFIRMACIONES = 12
# Presupuesto total de evidencia que se manda al revisor, y mínimo por pieza.
#
# Antes era un corte fijo de 1,200 por evidencia, que con pocas piezas
# desperdiciaba espacio y con campos largos amputaba justo lo que había que
# comprobar. Se reparte, con un piso para que muchas evidencias no dejen a
# cada una en un fragmento inútil.
_PRESUPUESTO_EVIDENCIA = 24000
_MIN_POR_EVIDENCIA = 1200
# Tope por campo de un registro. Existe para que un campo largo no
# desplace a los demás fuera del corte, que es exactamente lo que hacía
# la URL firmada.
_MAX_CAMPO = 300

_INSTRUCCIONES = """Eres un revisor de respuestas jurídicas. NO redactas ni mejoras: sólo compruebas si la evidencia citada sostiene lo que la respuesta afirma.

Para cada afirmación sustantiva de la RESPUESTA devuelve un objeto con:
- "afirmacion": la frase, literal y completa.
- "marcadores": los marcadores que cita, por ejemplo ["C1"]. Lista vacía si no cita ninguno.
- "veredicto": uno de
    "supported"       el pasaje citado dice eso.
    "contradicted"    el pasaje citado dice algo incompatible.
    "not_determined"  el pasaje no alcanza para decidirlo.
- "localizador": SÓLO si el veredicto es "supported" o "contradicted". Copia TEXTUALMENTE entre 8 y 30 palabras del pasaje de la EVIDENCIA en que te apoyas. Debe aparecer palabra por palabra; no lo parafrasees ni lo reconstruyas de memoria.
- "motivo": una frase breve.

Reglas:
- Distingue tres cosas y no las mezcles: que la evidencia **respalde** la afirmación, que la **contradiga**, y que **no alcance** para decidirlo. Una afirmación más amplia que su fuente no queda por eso refutada: queda sin demostrar.
- Separa las proposiciones materiales de una frase y evalúa cada una. "La duración es un factor de graduación y es el único que se destaca" son dos: la primera puede estar respaldada y la segunda no. Antes de comparar, identifica a qué autoridad, asunto y alcance se refiere la afirmación.
- Una fuente que acredita "un factor" respalda su existencia y **no dice nada** sobre exclusividad: eso es "not_determined". Sólo es "contradicted" si hay evidencia de factores adicionales en el mismo ámbito, autoridad y cuestión.
- Si la evidencia sí justifica una exclusividad, acéptala aunque la fuente lo diga con otras palabras. No busques palabras concretas: compara significados. "Ninguna otra circunstancia incide" es una exclusividad igual que "el único factor".
- Una regla general presente en un antecedente no respalda su atribución a otro acto.
- Una conclusión que suprime una condición de la fuente ("una vez que cause ejecutoria") cambia el efecto jurídico: es "contradicted", porque afirma como incondicional algo que la evidencia condiciona.
- No uses evidencia de otra etapa, autoridad o asunto como refutación automática.
- Si no hay evidencia citada para una afirmación, es "not_determined" con localizador nulo.
- Cuando la evidencia sea un registro con renglones "campo: valor", cita el renglón completo tal cual, por ejemplo "relatedTccCaseFile: 565/2023". No lo parafrasees ni lo describas.
- No evalúes ortografía, estilo ni completitud de la respuesta. Sólo el respaldo.

Además, revisa los EJEMPLOS. Cuando la respuesta presenta un documento como caso de algo —"resolución donde X se trató como Y", "precedente de Z"— esa caracterización es una afirmación sobre el documento y hay que comprobarla. Devuelve en "ejemplos" un objeto por cada uno:
- "documento": el identificador.
- "propiedad_atribuida": lo que la respuesta dice que ese documento ejemplifica.
- "veredicto": "supported" si la evidencia demuestra esa propiedad; "contradicted" si la evidencia muestra lo opuesto o una figura distinta; "not_determined" si no alcanza.
- "localizador": igual que arriba, texto literal de la evidencia.
- "motivo": una frase.

Que el documento trate el mismo TEMA no basta: tiene que demostrar la propiedad que se le atribuye. Si la evidencia describe una sucesión de actos y la respuesta la presenta como ejemplo de actos independientes, es "contradicted" aunque las frases sueltas citen bien.

Por último, revisa la COBERTURA. Una respuesta puede tener todas sus frases respaldadas y aun así dejar fuera algo que la evidencia sí sostenía y la pregunta pedía. Devuelve en "cobertura" un objeto por cada componente del encargo:
- "componente": qué pide la pregunta, en tus palabras.
- "estado": uno de
    "cubierto"        el borrador lo responde y usa la evidencia que hay sobre él.
    "cubierto_parcial" el borrador lo responde, pero **quedó sin usar** evidencia disponible que aporta a ese mismo componente.
    "omitido"         el borrador no lo responde aunque hay evidencia que lo sostiene.
    "no_resuelto"     ninguna evidencia alcanza para responderlo.
- "evidencia_disponible": los marcadores que aportan a ese componente.
- "evidencia_sin_usar": los marcadores que aportan y el borrador NO recoge. Obligatorio si el estado es "cubierto_parcial".
- "motivo": una frase.

Recorre la evidencia completa antes de decidir. Si una pregunta pide "qué mecanismos contempla" y hay dos pasajes que describen mecanismos distintos, usar sólo uno es "cubierto_parcial", no "cubierto": la respuesta es correcta y está incompleta.

Un componente incompleto u omitido NO es una frase falsa. No lo declares por vocabulario ausente; declara lo que la evidencia aporta y la respuesta no recoge.

Devuelve EXCLUSIVAMENTE un JSON: {"afirmaciones": [...], "ejemplos": [...], "cobertura": [...]}. Sin texto alrededor."""


def _norm(t: str) -> str:
    """Normaliza para comparar: sin acentos, sin puntuación, espacios simples."""
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def _localizador_existe(localizador: str, evidencia_texto: str) -> bool:
    """
    ¿El fragmento que dice citar está de verdad en la evidencia?

    Es la comprobación que impide que el verificador apruebe por simpatía: un
    veredicto positivo exige señalar dónde, y el dónde se verifica contra el
    texto real. Se compara normalizado porque el modelo reacentúa y repuntúa al
    copiar, pero no se acepta parafraseo: se exige la secuencia de palabras.
    """
    loc, ev = _norm(localizador), _norm(evidencia_texto)
    if not loc or not ev:
        return False
    if loc in ev:
        return True
    # Tolerancia a un corte: que aparezca una ventana larga contigua.
    palabras = loc.split()
    if len(palabras) < 8:
        return False
    for n in (12, 10, 8):
        for i in range(0, len(palabras) - n + 1):
            if " ".join(palabras[i:i + n]) in ev:
                return True
    return False


def _existe_en_otra_evidencia(localizador: str, evidencia: dict,
                              marcadores: list) -> bool:
    """
    ¿El extracto es auténtico, pero de un documento que la afirmación no cita?

    Distinguirlo importa: un localizador que no aparece en ninguna parte es
    probablemente inventado; uno que aparece en OTRO documento es una
    atribución cruzada, que es el defecto de H16-A. Los dos invalidan el
    veredicto, pero no son el mismo hallazgo y no deben medirse juntos.
    """
    ajenos = [e for m, e in evidencia.items() if m not in marcadores]
    return any(
        _localizador_existe(localizador, e["texto"]) or
        _localizador_existe(localizador, e.get("anchor", ""))
        for e in ajenos
    )


def _extraer_json(bruto: str) -> dict:
    """El modelo a veces envuelve el JSON en ``` o en prosa."""
    t = (bruto or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def texto_de_evidencia(doc: dict, campos_prioritarios=None) -> str:
    """
    El contenido citable de un documento, como texto que se pueda citar.

    Un criterio trae prosa y se usa tal cual. Un expediente son campos, y ahí
    estaba el defecto que destapó la banda del 23-sep: se serializaban como
    JSON crudo, el verificador no tenía una frase que copiar, y el contrato de
    localizador —"copia entre 8 y 30 palabras textuales"— rechazaba casi todo.

    Medido sobre 301 afirmaciones:

        evidencia de criterios (prosa)   20% marcadas sin soporte
        evidencia de registros (campos)  57%, con 22 de 27 localizadores fallidos

    No era que las respuestas sobre registros estuvieran mal sustentadas: era
    que no había nada citable. Renderizar `campo: valor` por renglón le da al
    verificador algo que copiar y al código algo que comprobar.
    """
    texto = (doc.get("content") or doc.get("text") or "").strip()
    if texto:
        return texto

    from models.schemas import NO_AL_PROMPT

    partes, omitidos = [], []
    for k, v in doc.items():
        if k in ("ref", "metadata", "tipo_fuente"):
            continue
        # La misma exclusión que ya se aplicaba al payload del agente desde
        # septiembre. No reusarla aquí fue el defecto: `resolutionFileUrl` son
        # ~1,500 caracteres de URL firmada, empezaba en el carácter 33 y con el
        # corte a 1,200 el revisor recibía tres campos —id, caseLink y la
        # URL— y ningún dato comprobable. Todos sus juicios sobre registros se
        # emitieron sin datos.
        if k in NO_AL_PROMPT:
            continue
        if v in (None, "", [], {}):
            continue
        valor = str(v)
        # Ningún campo puede acaparar el presupuesto. COFECE lo advirtió: subir
        # 1,200 a otra constante no resuelve nada, porque el orden de campos o
        # una descripción larga vuelve a desplazar el dato. Se acota por campo
        # y se declara lo recortado, en vez de perderlo al final del corte.
        if len(valor) > _MAX_CAMPO:
            omitidos.append(k)
            valor = valor[:_MAX_CAMPO] + f" […{len(str(v)) - _MAX_CAMPO} car omitidos]"
        partes.append(f"{k}: {valor}")

    if omitidos:
        partes.append(
            "[campos recortados por longitud: " + ", ".join(omitidos) + "]")

    # Primero los campos que la pregunta pide comprobar; después, los cortos.
    #
    # COFECE lo demostró sobre las tres H18: `judicialDecisionEffects` son 345
    # caracteres que contienen "una vez que cause ejecutoria", y con el orden de
    # declaración caían en la posición 1,587, fuera del corte. El revisor
    # marcaba la orden condicionada como no determinada porque **no la veía**, y
    # esa medición amputada nos llegaba como si fuera un juicio.
    #
    # Ordenar sólo por longitud no basta, y vale decir por qué: el campo que
    # importa **es largo**, así que quedaba igual de tarde. Lo que sí sirve es
    # lo que COFECE prescribió —"seleccionar evidencia por las proposiciones que
    # se verifican"— y el dato ya existe: los requisitos del turno nombran los
    # campos materiales. Para H18 nombran `judicialDecisionEffects`.
    #
    # Después de los prioritarios van los cortos: una fecha de 24 caracteres no
    # puede ser desplazada por una descripción de 309.
    prioridad = {str(c) for c in (campos_prioritarios or [])}

    def orden(linea: str):
        campo = linea.split(":", 1)[0]
        return (0 if campo in prioridad else 1, len(linea))

    return "\n".join(sorted(partes, key=orden))


def construir_evidencia(registry, docs, campos_prioritarios=None) -> dict:
    """
    `{marcador: {documento, texto}}` con lo que se citó en el turno.

    Sale del registro de citas, no de lo que el modelo diga haber usado: el
    verificador tiene que leer lo mismo que se recuperó.
    """
    from core.fuentes import case_link_de

    evidencia: dict[str, dict] = {}
    for d in (docs or []):
        if not isinstance(d, dict):
            continue
        ref = d.get("ref")
        if not ref:
            continue
        meta = d.get("metadata") or {}
        texto = texto_de_evidencia(d, campos_prioritarios)
        evidencia[ref] = {
            "documento": case_link_de(d) or "?",
            "texto": texto,
            "anchor": str((meta or {}).get("anchor") or ""),
        }
    return evidencia


async def verificar(
    pregunta: str,
    borrador: str,
    evidencia: dict,
    adapter,
    model: str,
    # 3000 y no 1500: con el default anterior, 13 de 60 respuestas de la
    # banda del 23-sep devolvieron JSON truncado. Un verificador que no
    # corre no es un verificador que aprueba.
    max_tokens: int = 3000,
) -> dict:
    """
    Revisa el borrador contra su evidencia. **No bloquea nada.**

    Devuelve `{ejecutado, afirmaciones, resumen, error}`. Cada afirmación lleva
    `veredicto`, `localizador` y `localizador_verificado`, que es la parte que
    comprueba el código y no el modelo.
    """
    if not borrador or not evidencia or adapter is None:
        return {"ejecutado": False, "afirmaciones": [], "resumen": {},
                "error": "sin borrador, sin evidencia o sin adaptador"}

    # El corte por evidencia, y lo que se hace cuando hay que cortar.
    #
    # COFECE lo demostró sobre las tres H18: en el registro E2,
    # `judicialDecisionEffects` empieza en el carácter 1,283 y "cause
    # ejecutoria" en el 1,320, y el corte a 1,200 los dejaba fuera. El revisor
    # marcaba la orden condicionada como no determinada porque **no la veía**,
    # y esa medición contaminada nos llegaba como si fuera un juicio.
    #
    # Quitar la URL firmada corrigió una causa; el corte global seguía siendo
    # otra. Dos cambios, no uno:
    #
    # 1. El presupuesto se reparte entre las evidencias que hay, en vez de
    #    cortar cada una por una constante. Con pocas evidencias, cada una
    #    viaja entera.
    # 2. **Todo recorte se declara.** Una insuficiencia del payload es una
    #    limitación de la evaluación, no un defecto de la respuesta, y
    #    confundirlas fue exactamente lo que pasó en H18.
    bloques, recortes = [], []
    presupuesto = max(
        _MIN_POR_EVIDENCIA,
        _PRESUPUESTO_EVIDENCIA // max(len(evidencia), 1),
    )
    for ref, e in evidencia.items():
        texto = e["texto"]
        if len(texto) > presupuesto:
            recortes.append({
                "ref": ref,
                "documento": e["documento"],
                "caracteres_totales": len(texto),
                "caracteres_enviados": presupuesto,
            })
            texto = texto[:presupuesto] + (
                f" […recortado: faltan {len(e['texto']) - presupuesto} "
                f"caracteres de esta evidencia]"
            )
        bloques.append(f"[{ref}] (documento {e['documento']})\n{texto}")
    contenido = (
        f"PREGUNTA:\n{pregunta}\n\n"
        f"EVIDENCIA CITABLE:\n" + "\n\n".join(bloques) + "\n\n"
        f"RESPUESTA A REVISAR:\n{borrador}"
    )

    from models.schemas import LLMMessage
    try:
        bruto = await adapter.quick_completion(
            messages=[
                LLMMessage(role="system", content=_INSTRUCCIONES),
                LLMMessage(role="user", content=contenido),
            ],
            model=model,
            max_tokens=max_tokens,
        )
    except Exception as e:  # nunca tumbar la respuesta por el verificador
        logger.warning(f"Verificador semántico falló: {type(e).__name__}: {e}")
        return {"ejecutado": False, "afirmaciones": [], "resumen": {},
                "error": f"{type(e).__name__}: {e}"}

    datos = _extraer_json(bruto)
    crudas = datos.get("afirmaciones")
    if not isinstance(crudas, list):
        return {"ejecutado": False, "afirmaciones": [], "resumen": {},
                "error": "el verificador no devolvió JSON utilizable"}

    afirmaciones = []
    for a in crudas[:MAX_AFIRMACIONES]:
        if not isinstance(a, dict):
            continue
        veredicto = str(a.get("veredicto") or NOT_DETERMINED)
        if veredicto not in (SUPPORTED, CONTRADICTED, NOT_DETERMINED):
            veredicto = NOT_DETERMINED
        marcadores = [str(m) for m in (a.get("marcadores") or [])
                      if isinstance(m, (str, int))]
        localizador = str(a.get("localizador") or "").strip()

        # El localizador tiene que existir en la evidencia QUE DICE CITAR.
        #
        # Antes, si ningún marcador resolvía, se buscaba en toda la evidencia
        # del turno (`candidatos = list(evidencia.values())`). Eso convertía
        # el control en un colador: un extracto auténtico de cualquier
        # documento validaba una afirmación atribuida a otro.
        #
        # Es el mecanismo detrás de los dos fallos que COFECE encontró:
        #   H16-A  "en VCN-005-2018 la COFECE sostuvo…" validado con el
        #          criterio 4035, que es de VCN-005-2024.
        #   H15-B  "el único factor" validado con un pasaje que dice
        #          "un factor".
        #
        # Un marcador que no resuelve es un problema de integridad, no una
        # invitación a buscar respaldo en otra parte.
        verificado = False
        integridad = None
        if localizador:
            invalidos = [m for m in marcadores if m not in evidencia]
            candidatos = [evidencia[m] for m in marcadores if m in evidencia]
            if invalidos and not candidatos:
                integridad = "referencia_invalida"
            elif not marcadores:
                integridad = "sin_referencia"
            else:
                verificado = any(
                    _localizador_existe(localizador, c["texto"]) or
                    _localizador_existe(localizador, c.get("anchor", ""))
                    for c in candidatos
                )
                if not verificado and _existe_en_otra_evidencia(
                        localizador, evidencia, marcadores):
                    # El extracto es auténtico pero de otro documento. No es
                    # "no lo encontré": es una atribución cruzada, y merece un
                    # estado propio para poder medirla.
                    integridad = "atribucion_no_acreditada"

        efectivo = veredicto
        if veredicto in (SUPPORTED, CONTRADICTED) and not verificado:
            # No se degrada a "contradicted": eso sería afirmar un problema
            # que tampoco se probó. Queda en indeterminado, que es lo honesto.
            efectivo = NOT_DETERMINED

        afirmaciones.append({
            "afirmacion": str(a.get("afirmacion") or "")[:400],
            "marcadores": marcadores,
            "veredicto_del_modelo": veredicto,
            "veredicto": efectivo,
            "localizador": localizador[:300],
            "localizador_verificado": verificado,
            "integridad": integridad,
            "motivo": str(a.get("motivo") or "")[:300],
        })

    # Ejemplos: el papel que la respuesta le asigna a cada documento.
    #
    # H16-C mostró que revisar afirmación por afirmación no basta. Sus frases
    # citaban bien —"la concentración debe notificarse antes de realizar la
    # aportación"— y el encabezado las presentaba como "resolución donde dos
    # aumentos se trataron como operaciones independientes", que es la figura
    # contraria. Cada oración fiel, la caracterización invertida.
    ejemplos = []
    for e in (datos.get("ejemplos") or [])[:MAX_AFIRMACIONES]:
        if not isinstance(e, dict):
            continue
        veredicto = str(e.get("veredicto") or NOT_DETERMINED)
        if veredicto not in (SUPPORTED, CONTRADICTED, NOT_DETERMINED):
            veredicto = NOT_DETERMINED
        localizador = str(e.get("localizador") or "").strip()
        documento = str(e.get("documento") or "").strip()

        # El ejemplo se valida contra SU documento, no contra cualquiera.
        #
        # Era el mismo colador que en las afirmaciones, y aquí pesa más: el
        # defecto de H16-A es precisamente atribuir a un expediente la
        # propiedad que demuestra otro. Buscar el extracto en toda la
        # evidencia aprobaba exactamente eso.
        propios = [
            c for m, c in evidencia.items()
            if documento and (m == documento or c.get("documento") == documento)
        ]
        integridad = None
        if not localizador:
            verificado = False
        elif not propios:
            verificado = False
            integridad = "documento_no_localizado"
        else:
            verificado = any(
                _localizador_existe(localizador, c["texto"]) or
                _localizador_existe(localizador, c.get("anchor", ""))
                for c in propios
            )
            if not verificado and any(
                    _localizador_existe(localizador, c["texto"])
                    for m, c in evidencia.items() if c not in propios):
                integridad = "atribucion_no_acreditada"

        efectivo = veredicto
        if veredicto in (SUPPORTED, CONTRADICTED) and not verificado:
            efectivo = NOT_DETERMINED
        ejemplos.append({
            "documento": str(e.get("documento") or "")[:80],
            "propiedad_atribuida": str(e.get("propiedad_atribuida") or "")[:300],
            "veredicto_del_modelo": veredicto,
            "veredicto": efectivo,
            "localizador": localizador[:300],
            "localizador_verificado": verificado,
            "integridad": integridad,
            "motivo": str(e.get("motivo") or "")[:300],
        })

    # Cobertura: qué pedía el encargo y quedó fuera teniendo evidencia.
    #
    # Es la medición que faltaba, y el par controlado de H08 del 26-sep mostró
    # por qué hace falta separarla del respaldo: con el criterio 4212 en un
    # contexto de ocho pasajes, la respuesta lo omitió **3 de 3 veces**, igual
    # que sin él. Todas sus frases estaban respaldadas; lo que faltaba era una
    # parte del encargo.
    #
    # COFECE: "Separar respaldo de completitud." Un componente omitido no es
    # una frase falsa, y contarlo como contradicción sería el error opuesto.
    cobertura = []
    for c in (datos.get("cobertura") or [])[:MAX_AFIRMACIONES]:
        if not isinstance(c, dict):
            continue
        estado = str(c.get("estado") or "no_resuelto")
        if estado not in ("cubierto", "cubierto_parcial", "omitido",
                          "no_resuelto"):
            estado = "no_resuelto"
        marcadores = [str(m) for m in (c.get("evidencia_disponible") or [])
                      if isinstance(m, (str, int))]
        sin_usar = [str(m) for m in (c.get("evidencia_sin_usar") or [])
                    if isinstance(m, (str, int)) and str(m) in evidencia]
        # Un hallazgo de cobertura sólo cuenta si la evidencia que se dice
        # disponible existe de verdad en el turno. Sin eso es una opinión sobre
        # lo que la respuesta "debería" decir.
        if estado == "omitido" and not [m for m in marcadores if m in evidencia]:
            estado = "no_resuelto"
        if estado == "cubierto_parcial" and not sin_usar:
            estado = "cubierto"
        cobertura.append({
            "componente": str(c.get("componente") or "")[:250],
            "estado": estado,
            "estado_del_modelo": str(c.get("estado") or ""),
            "evidencia_disponible": marcadores,
            "evidencia_sin_usar": sin_usar,
            "motivo": str(c.get("motivo") or "")[:250],
        })

    resumen = {
        "total": len(afirmaciones),
        "componentes_revisados": len(cobertura),
        "componentes_omitidos": sum(
            1 for c in cobertura if c["estado"] == "omitido"),
        "componentes_incompletos": sum(
            1 for c in cobertura if c["estado"] == "cubierto_parcial"),
        "ejemplos_total": len(ejemplos),
        "ejemplos_contradicted": sum(
            1 for e in ejemplos if e["veredicto"] == CONTRADICTED),
        SUPPORTED: sum(1 for a in afirmaciones if a["veredicto"] == SUPPORTED),
        CONTRADICTED: sum(1 for a in afirmaciones if a["veredicto"] == CONTRADICTED),
        NOT_DETERMINED: sum(1 for a in afirmaciones
                            if a["veredicto"] == NOT_DETERMINED),
        # Una afirmación sustantiva que CITA evidencia y no queda sostenida es
        # un hallazgo, no un empate. COFECE lo pide expresamente: el
        # verificador debe devolver "afirmaciones sin soporte" además de las
        # contradicciones.
        #
        # H16-C lo muestra: sus seis afirmaciones sobre independencia salieron
        # `not_determined` con el motivo correcto —"la evidencia no establece
        # que dos aumentos fueron tratados así"—. Contar sólo `contradicted`
        # habría dado el caso por limpio.
        "sin_soporte_citando": sum(
            1 for a in afirmaciones
            if a["veredicto"] == NOT_DETERMINED and a["marcadores"]),
        # Atribuciones cruzadas: el extracto es auténtico pero de otro
        # documento. Va aparte porque es un hallazgo distinto de un
        # localizador inventado, y es el defecto de H16-A.
        "atribucion_no_acreditada": sum(
            1 for a in afirmaciones
            if a.get("integridad") == "atribucion_no_acreditada"),
        "referencias_invalidas": sum(
            1 for a in afirmaciones
            if a.get("integridad") in ("referencia_invalida", "sin_referencia")),
        "localizadores_no_verificados": sum(
            1 for a in afirmaciones
            if a["veredicto_del_modelo"] in (SUPPORTED, CONTRADICTED)
            and not a["localizador_verificado"]
        ),
    }
    resumen["evidencias_recortadas"] = len(recortes)
    return {"ejecutado": True, "afirmaciones": afirmaciones,
            "ejemplos": ejemplos, "cobertura": cobertura,
            "evidencia_recortada": recortes,
            "resumen": resumen, "error": None}
