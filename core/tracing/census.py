"""
Censo del acervo al inicio de cada corrida.

Por qué existe: el acervo se está cargando mientras probamos, así que el
universo es un blanco móvil. Si el corpus crece entre dos corridas, las
preguntas de exhaustividad dejan de ser comparables y no hay forma de saber si
una mejora vino de un fix nuestro o de documentos nuevos. No es hipotético:
entre el 18-ago y el 7-sep-2026 el acervo pasó de 4,632 a 4,662 expedientes,
los VCN de 36 a 46, y aparecieron 18 documentos judiciales que antes no
existían.

Eso lo resolvería `index_version` del lado de la API, que hoy no se expone
(ver docs/solicitud-jose-miguel.md). Mientras tanto, cada corrida se toma su
propia foto del universo y la guarda en el manifiesto. Con eso, comparar dos
corridas es comparar también sus censos: si difieren, lo sabemos antes de
sacar conclusiones.

Cómo se toma. Antes era una consulta por prefijo leyendo `meta.total`.
`agent-search` no expone `meta.total`, así que ahora se trae el universo
completo en una sola petición —4,662 registros, 2.1 MB, ~3 s— y se cuenta
localmente. Sale más barato que las siete consultas anteriores y el conteo es
exacto en vez de depender de un campo que ya no existe.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Prefijos que importan para las preguntas de exhaustividad de la batería.
PREFIXES = ("VCN", "IO", "CNT", "DE", "RA", "CON")

# Campos que el agente necesita y que la API venía mandando. Se vigilan porque
# un campo que desaparece del endpoint no produce ningún error: el registro se
# parsea igual, el campo queda en None, y el agente concluye "no hay dato"
# cuando lo que pasó es que dejó de llegar.
#
# No es hipotético. Al migrar a `/cases/agent-search` el 7-sep-2026 se perdió
# `startAgreementDate` —el campo que abre el plazo en VCN e IO— y el agente
# empezó a responder "no se puede calcular el plazo" sobre expedientes cuya
# fecha sí existe: el propio frontend de normaplus.ai la muestra.
CAMPOS_VIGILADOS = (
    "startAgreementDate", "notificationDate", "admissionDate",
    "basicInfoRequestDate", "additionalInfoRequestDate", "resolutionDate",
    "senseOfResolution", "agentFines", "authority",
)


def _prefijo_de(case_link: str) -> str:
    """
    Prefijo de un expediente. Los documentos judiciales que aparecieron en
    sep-2026 no lo usan (`1244_2017_2JD`, `480_2018_2SCJN`), así que se
    agrupan aparte en vez de inventarles uno.
    """
    link = (case_link or "").strip().upper()
    if "-" not in link:
        return "JUDICIAL/OTRO"
    prefijo = link.split("-", 1)[0]
    return prefijo if prefijo.isalpha() else "JUDICIAL/OTRO"


async def take_census(estadistica_client, prefixes: tuple[str, ...] = PREFIXES) -> dict:
    """
    Cuenta expedientes por prefijo sobre el universo completo. Nunca lanza: si
    algo falla, devuelve lo que alcanzó a medir y lo deja anotado.
    """
    census: dict = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "source": "conteo local sobre el universo de /cases/agent-search",
        "by_prefix": {},
        "total": None,
        "errors": [],
    }

    try:
        registros, total, completo = await estadistica_client.fetch_universe()
    except Exception as e:
        census["errors"].append(f"universo: {type(e).__name__}: {e}")
        census["completo"] = False
        return census

    if not registros:
        census["errors"].append("universo: la API no devolvió registros")
        census["completo"] = False
        return census

    conteos: dict[str, int] = {}
    for r in registros:
        link = getattr(r, "caseLink", None) or ""
        conteos[_prefijo_de(link)] = conteos.get(_prefijo_de(link), 0) + 1

    census["total"] = len(registros)

    # Campos que la API trae con dato y el modelo no declara.
    #
    # `CAMPOS_VIGILADOS` es una lista fija: detecta que un campo conocido
    # DESAPAREZCA, no que uno nuevo nunca se haya declarado. Ese punto ciego
    # costó el holdout del 21-sep: la API devolvía 52 campos, `ExpedienteRecord`
    # declaraba 19, y el agente afirmaba que no existían datos que sí estaban en
    # 57 de los 63 documentos.
    #
    # Esto lo mide sin lista fija: compara lo que llega contra lo que el modelo
    # sabe recibir. Si la API agrega un campo mañana, se entera sola.
    try:
        from models.schemas import ExpedienteRecord
        declarados = set(ExpedienteRecord.model_fields)
        crudos = getattr(estadistica_client, "ultimo_payload_crudo", None) or []
        no_declarados: dict[str, int] = {}
        for item in crudos:
            if not isinstance(item, dict):
                continue
            for k, v in item.items():
                if k not in declarados and v not in (None, "", [], {}):
                    no_declarados[k] = no_declarados.get(k, 0) + 1
        if no_declarados:
            census["campos_no_declarados"] = dict(
                sorted(no_declarados.items(), key=lambda kv: -kv[1])
            )
    except Exception as e:  # nunca tumbar el censo por esta medición
        census["errors"].append(f"campos_no_declarados: {type(e).__name__}: {e}")

    # Los identificadores vistos, para poder decir CUÁLES faltan cuando hay un
    # universo declarado. Con 63 documentos cabe en el manifiesto; con el
    # acervo completo no, así que sólo se guarda cuando es una lista corta.
    vistos = sorted({getattr(r, "caseLink", None) or "" for r in registros})
    if len(vistos) <= 200:
        census["case_links_vistos"] = vistos

    # Cobertura de campos: cuáles llegan con valor, cuáles llegan siempre
    # vacíos, y cuáles la API directamente ya no incluye en su respuesta.
    presentes: dict[str, int] = {}
    for r in registros:
        crudo = r.model_dump() if hasattr(r, "model_dump") else dict(r)
        for campo in CAMPOS_VIGILADOS:
            if crudo.get(campo):
                presentes[campo] = presentes.get(campo, 0) + 1
    census["field_coverage"] = {c: presentes.get(c, 0) for c in CAMPOS_VIGILADOS}
    ausentes = [c for c in CAMPOS_VIGILADOS if presentes.get(c, 0) == 0]
    # Va en su propia clave, no en `errors`: el censo SÍ contó todo, lo que
    # falla es la respuesta de la API. Mezclarlos marcaría el censo como
    # incompleto y ensuciaría la comparación entre corridas.
    if ausentes:
        census["campos_ausentes"] = ausentes
        census["avisos"] = [
            "la API no devolvió ningún valor para: " + ", ".join(ausentes)
            + ". Un campo que desaparece no da error: el agente responde "
              "'no hay dato' sobre información que sí existe."
        ]
    # Los prefijos esperados se reportan siempre, aunque den cero: que un
    # prefijo desaparezca del acervo es justo el cambio que hay que ver.
    for prefijo in prefixes:
        census["by_prefix"][prefijo] = conteos.get(prefijo, 0)
    # Y lo que no esperábamos también, para que un tipo documental nuevo no
    # entre en silencio.
    for prefijo, n in sorted(conteos.items()):
        if prefijo not in census["by_prefix"]:
            census["by_prefix"][prefijo] = n

    if not completo:
        census["errors"].append(
            "la API devolvió exactamente el tope pedido: el censo pudo "
            "quedar cortado"
        )

    census["completo"] = not census["errors"]
    return census


def diff_census(before: dict, after: dict) -> list[dict]:
    """Diferencias entre el censo de dos corridas. Vacío = corpus estable."""
    if not before or not after:
        return []

    changes = []
    if before.get("total") != after.get("total"):
        changes.append({
            "field": "total",
            "before": before.get("total"),
            "after": after.get("total"),
        })

    keys = set(before.get("by_prefix", {})) | set(after.get("by_prefix", {}))
    for key in sorted(keys):
        b = before.get("by_prefix", {}).get(key)
        a = after.get("by_prefix", {}).get(key)
        if b != a:
            changes.append({"field": f"prefix:{key}", "before": b, "after": a})
    return changes
