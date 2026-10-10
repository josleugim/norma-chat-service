"""
Servidor MCP de Norma+: el acervo dentro de Claude y ChatGPT ("@norma").

Expone las herramientas del agente —no el agente—: Claude o ChatGPT razonan y
redactan, y Norma+ pone los datos. Lo que el código calcula (cifras, plazos,
cobertura, historia procesal) llega igual que al chat de normaplus.ai; lo que
se pierde es el control sobre la redacción final. Ver
`NormaChat-Doc-Obs/docs/propuesta-norma-en-claude-chatgpt.md`.

Reglas de los directorios de Claude y ChatGPT que este módulo respeta:
- todas las herramientas son de sólo lectura y lo declaran;
- las descripciones dicen qué hace la herramienta, no cómo comportarse;
- errores con detalle; resultados de tamaño acotado.

**Fase 1: sin login.** Por eso el montaje está apagado por omisión
(`MCP_ENABLED=false`): no debe quedar expuesto en staging hasta que exista el
OAuth con las cuentas de Norma+.
"""
from __future__ import annotations

import logging
import time
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from agent.turn_state import TurnState
from mcp_server import limpieza

logger = logging.getLogger("mcp")

_agente = None
_base_web = "https://normaplus.ai"

# Topes de tamaño: los directorios piden respuestas acotadas.
MAX_CRITERIOS = 25
MAX_EXPEDIENTES = 50
MAX_HISTORIAS = 10

TipoExpediente = Literal["VCN", "IO", "CNT"]
Autoridad = Literal["CFC", "COFECE", "CNA"]

servidor = MCPServer(
    name="Norma+",
    title="Norma+",
    instructions=(
        "Norma+ es un acervo de resoluciones de las autoridades mexicanas de "
        "competencia económica (CFC, COFECE y CNA) y de las sentencias de los "
        "juzgados y tribunales especializados que las revisan. Cada resultado "
        "trae la URL de su ficha o de su párrafo en normaplus.ai. Las cifras "
        "—multas, plazos, promedios, conteos— las calcula Norma+ sobre el "
        "acervo, no se estiman. Tipos de expediente: VCN, concentración no "
        "notificada; CNT, concentración notificada; IO, omisión de notificar. "
        "Las resoluciones y sentencias forman cadenas procesales: una resolución "
        "puede ser impugnada en amparo, la sentencia de amparo revisada por un "
        "tribunal colegiado, y la autoridad puede emitir una nueva resolución en "
        "cumplimiento. El campo situacion_posterior, al inicio de una ficha, "
        "resume esas actuaciones. Cuando una revisión modificó o revocó el "
        "documento, el sentido y los efectos que muestra su ficha no son los "
        "vigentes, y las fechas y plazos de ese documento corresponden a una "
        "decisión que después cambió."
    ),
    website_url="https://normaplus.ai",
)


def configurar(agente, base_web: str) -> None:
    """Lo llama el arranque del servicio con el agente ya construido."""
    global _agente, _base_web
    _agente = agente
    _base_web = base_web or _base_web


def _lectura(titulo: str) -> ToolAnnotations:
    return ToolAnnotations(title=titulo, read_only_hint=True, destructive_hint=False,
                           idempotent_hint=True, open_world_hint=False)


def _agente_o_error():
    if _agente is None:
        raise ToolError("El servicio de Norma+ no terminó de iniciar. Intenta de nuevo en unos segundos.")
    return _agente


async def _ejecutar(nombre: str, coro):
    """Corre una herramienta del agente y convierte sus fallas en errores con detalle."""
    inicio = time.monotonic()
    try:
        resultado = await coro
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"MCP {nombre}: {type(e).__name__}: {e}")
        raise ToolError(
            f"La consulta al acervo de Norma+ falló ({type(e).__name__}). Un fallo "
            "no significa que no existan resultados; la consulta no se completó.")
    finally:
        logger.info(f"MCP {nombre}: {int((time.monotonic() - inicio) * 1000)} ms")
    if isinstance(resultado, dict) and resultado.get("error"):
        raise ToolError(f"La consulta no se pudo completar: {resultado['error']}")
    return resultado


def _con_situacion_posterior(ficha: dict, historias: dict) -> dict:
    """La ficha con el resumen de lo que pasó después, como primer campo."""
    resumen = (historias.get(ficha.get("expediente")) or {}).get("resumen")
    return {"situacion_posterior": resumen, **ficha} if resumen else ficha


async def _historias(agente, expedientes: list[str]) -> dict:
    """La historia procesal de los expedientes, si el mapa está disponible."""
    mapa = await agente._relaciones()
    if mapa is None:
        return {"_nota": "No se pudo consultar la historia procesal en este momento."}
    salida = {}
    for cl in dict.fromkeys(c for c in expedientes if c):
        if len(salida) >= MAX_HISTORIAS:
            break
        h = limpieza.historia(mapa.historia_de(cl), _base_web, cl)
        if h:
            salida[cl] = h
    return salida


@servidor.tool(
    name="buscar_criterios",
    description=(
        "Busca, por significado, criterios y razonamientos en el texto de las "
        "resoluciones de competencia económica y de las sentencias que las "
        "revisan. Devuelve párrafos con su expediente, título, artículo, "
        "páginas, quién lo sostiene (resolución o voto particular), la URL al "
        "párrafo y la historia procesal de cada expediente."
    ),
    annotations=_lectura("Buscar criterios"),
)
async def buscar_criterios(consulta: str, expedientes: list[str] | None = None,
                           max_resultados: int = 10) -> dict:
    """
    consulta: lo que se busca, en lenguaje natural.
    expedientes: limita la búsqueda a estos expedientes (p. ej. "VCN-001-2017").
    max_resultados: entre 1 y 25.
    """
    ag = _agente_o_error()
    st = TurnState()
    st.query = consulta
    args = {"query": consulta, "top_k": max(1, min(int(max_resultados), MAX_CRITERIOS))}
    if expedientes:
        args["en_expedientes"] = expedientes
    docs = await _ejecutar("buscar_criterios", ag._exec_buscar_criterios(args, None, st))
    criterios = [limpieza.criterio(d, st.anclas_criterio.get(str(d.get("id"))), _base_web)
                 for d in (docs or []) if isinstance(d, dict)]
    return {
        "criterios": criterios,
        "nota_de_cobertura": (
            "Búsqueda por similitud: devuelve los párrafos más cercanos, no todos "
            "los que tratan el tema."),
        "historia_procesal": await _historias(ag, [c.get("expediente") for c in criterios]),
    }


@servidor.tool(
    name="buscar_expedientes",
    description=(
        "Busca expedientes del acervo por número, tipo, autoridad, sentido, "
        "años o texto libre (nombre del caso, agentes económicos, mercados). "
        "Devuelve la ficha de cada expediente —fechas, agentes, multas, "
        "sentido, votos— con su URL y su historia procesal."
    ),
    annotations=_lectura("Buscar expedientes"),
)
async def buscar_expedientes(
    texto: str | None = None,
    expediente: str | None = None,
    tipo_de_expediente: TipoExpediente | None = None,
    autoridad: Autoridad | None = None,
    sentido_de_la_resolucion: str | None = None,
    con_multas: bool | None = None,
    anio_desde: int | None = None,
    anio_hasta: int | None = None,
    max_resultados: int = 20,
) -> dict:
    """
    texto: búsqueda libre.
    expediente: número exacto, p. ej. "VCN-004-2024".
    tipo_de_expediente: VCN, IO o CNT.
    con_multas: true sólo con multa; false sólo sin multa.
    max_resultados: hasta 50.
    """
    ag = _agente_o_error()
    st = TurnState()
    args = {k: v for k, v in {
        "text_search": texto, "id_expediente": expediente,
        "prefijo_expediente": tipo_de_expediente, "autoridad": autoridad,
        "sentido_resolucion": sentido_de_la_resolucion, "has_multas": con_multas,
        "fecha_resolucion_desde": anio_desde, "fecha_resolucion_hasta": anio_hasta,
        "limit": max(1, min(int(max_resultados), MAX_EXPEDIENTES)),
    }.items() if v is not None}
    regs = await _ejecutar("buscar_expedientes", ag._exec_buscar_expedientes(args, None, st))
    fichas = [limpieza.expediente(r, _base_web) for r in (regs or []) if isinstance(r, dict)]
    total = getattr(ag.estadistica, "last_total", None)
    historias = await _historias(ag, [f.get("expediente") for f in fichas])
    fichas = [_con_situacion_posterior(f, historias) for f in fichas]
    salida = {"expedientes": fichas, "devueltos": len(fichas)}
    if isinstance(total, int):
        salida["total_que_cumple_los_filtros"] = total
    salida["historia_procesal"] = historias
    return salida


@servidor.tool(
    name="ver_expediente",
    description=(
        "Devuelve la ficha completa de un expediente por su número, con su URL "
        "y su historia procesal: de qué deriva y qué actuaciones posteriores "
        "tuvo (amparos, revisiones, resoluciones en cumplimiento)."
    ),
    annotations=_lectura("Ver expediente"),
)
async def ver_expediente(expediente: str) -> dict:
    """expediente: número exacto, p. ej. "VCN-001-2017" o "275_2023_1JD"."""
    ag = _agente_o_error()
    st = TurnState()
    regs = await _ejecutar("ver_expediente", ag._exec_buscar_expedientes(
        {"id_expediente": expediente, "limit": 5}, None, st))
    exacto = [r for r in (regs or []) if isinstance(r, dict) and r.get("caseLink") == expediente]
    if not exacto:
        raise ToolError(f"No hay un expediente con el número exacto {expediente!r} en el acervo de Norma+.")
    historias = await _historias(ag, [expediente])
    return {
        "expediente": _con_situacion_posterior(limpieza.expediente(exacto[0], _base_web), historias),
        "historia_procesal": historias.get(expediente),
    }


@servidor.tool(
    name="historia_procesal",
    description=(
        "Devuelve la cadena procesal de un expediente: la resolución de la "
        "autoridad, los amparos en juzgados especializados, las revisiones en "
        "tribunales colegiados, la Suprema Corte y las resoluciones en "
        "cumplimiento, con lo que decidió cada una y su URL."
    ),
    annotations=_lectura("Historia procesal"),
)
async def historia_procesal(expediente: str) -> dict:
    """expediente: número exacto del documento del que se quiere la historia."""
    ag = _agente_o_error()
    historias = await _historias(ag, [expediente])
    if "_nota" in historias:
        raise ToolError(historias["_nota"])
    h = historias.get(expediente)
    if not h:
        return {"expediente": expediente,
                "resultado": "No hay actuaciones relacionadas registradas en el acervo de Norma+."}
    return {"expediente": expediente, **h}


@servidor.tool(
    name="calcular_estadistica",
    description=(
        "Calcula sobre el acervo completo, sin muestrear: máximo, mínimo, "
        "promedio, suma o conteo de multas o de plazos (días hábiles o "
        "naturales). Cada asunto cuenta una vez; las resoluciones en "
        "cumplimiento de amparo se excluyen salvo que se pida contar el plazo "
        "hasta la resolución final. Devuelve el resultado, el denominador "
        "real, los expedientes que lo determinan y las exclusiones."
    ),
    annotations=_lectura("Calcular estadística"),
)
async def calcular_estadistica(
    operacion: Literal["max", "min", "promedio", "suma", "conteo"],
    metrica: Literal["multa", "dias_habiles", "dias_naturales"],
    tipo_de_expediente: TipoExpediente | None = None,
    autoridad: Autoridad | None = None,
    sentido_de_la_resolucion: str | None = None,
    hasta_la_resolucion_final: bool = False,
) -> dict:
    """
    hasta_la_resolucion_final: sólo para plazos; true para que, en los asuntos
    con resolución en cumplimiento de amparo, el plazo corra hasta esa
    resolución.
    """
    ag = _agente_o_error()
    st = TurnState()
    args = {k: v for k, v in {
        "operacion": operacion, "metrica": metrica,
        "prefijo_expediente": tipo_de_expediente, "autoridad": autoridad,
        "sentido_resolucion": sentido_de_la_resolucion,
        "hasta_resolucion_final": hasta_la_resolucion_final or None,
    }.items() if v is not None}
    r = await _ejecutar("calcular_estadistica", ag._exec_agregar_expedientes(args, None, st))
    return limpieza.agregacion(r, _base_web)


@servidor.tool(
    name="dias_entre_fechas",
    description=(
        "Cuenta los días hábiles y naturales entre dos fechas con el "
        "calendario oficial de días inhábiles de la COFECE o de la CNA. "
        "Excluye el día inicial e incluye el final. Informa si hay acuerdos "
        "de suspensión que coinciden con el periodo."
    ),
    annotations=_lectura("Días entre fechas"),
)
async def dias_entre_fechas(fecha_inicio: str, fecha_fin: str,
                            calendario: Literal["COFECE", "CNA"] = "COFECE") -> dict:
    """fecha_inicio, fecha_fin: AAAA-MM-DD o DD-MM-AAAA."""
    ag = _agente_o_error()
    st = TurnState()
    r = await _ejecutar("dias_entre_fechas", ag._exec_calcular_plazos({
        "fecha_inicio_explicita": fecha_inicio, "fecha_fin_explicita": fecha_fin,
        "institucion": calendario}, None, st))
    return limpieza.plazo(r)
