"""
Lector de una resolución completa ("chatear con la resolución").

Para un solo documento no se hace RAG: la resolución entera entra al contexto
(propuesta del 14-ago, §3.1). Lo que sí se controla por código es la cita:

- El modelo responde con cada afirmación remitida a una cita numerada que
  lleva **página y fragmento textual** (formato de texto, no JSON: el texto
  de una resolución trae comillas y tablas que rompían el JSON).
- El código comprueba que cada fragmento aparezca de verdad en esa página
  o sus contiguas, palabra por palabra y en orden. Tolera los encabezados de
  página intercalados y la puntuación; no tolera paráfrasis, una negación
  insertada ni una cifra cambiada. Una cita que
  no aparece **no se muestra como respaldo**: queda marcada y su número se
  retira del texto.

Es el mismo contrato del verificador semántico (§35.3): un modelo que inventa
una cita tiene que inventar también un texto que el código encuentre.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field

from core.costos import costo_estimado
from retrieval.resolucion_client import TESTADO, ResolucionDigital

SISTEMA = f"""Eres el lector de una resolución en materia de competencia económica \
de México. Respondes en español, sólo con lo que dice el documento que se te entrega.

Reglas:
1. Toda afirmación sobre el contenido de la resolución lleva una cita: [1], [2]…
   Cada cita tiene la página (la marca <<<PÁGINA N>>>) y un fragmento copiado
   **textualmente** del documento, de 8 a 40 palabras seguidas, sin cambiar,
   corregir ni abreviar con puntos suspensivos.
2. Si el documento no contiene lo que se pregunta, dilo con claridad y pon
   NO_CONSTA: sí. No completes con conocimiento general atribuyéndolo a la
   resolución.
3. {TESTADO} marca texto eliminado en la versión pública. Si la respuesta depende
   de un pasaje testado, di que está testado; no es lo mismo que "la resolución
   no lo dice".
4. Distingue quién habla: la mayoría del Pleno o del órgano, un voto particular
   o concurrente, los argumentos de las partes, o un acto citado. No atribuyas
   a la autoridad lo que dice una parte o un voto.
5. No menciones marcas de página, fragmentos ni campos: el usuario sólo ve la
   respuesta y las citas.

Formato de salida, exactamente así:
RESPUESTA:
<texto con [1], [2]…>
CITAS:
[1] p. 14 | <fragmento textual>
[2] p. 21 | <fragmento textual>
NO_CONSTA: no

Usa "NO_CONSTA: sí" si el documento no contiene lo que se pregunta."""


def _palabras(texto: str) -> list[str]:
    """Palabras normalizadas. La puntuación y el marcado no cuentan."""
    t = unicodedata.normalize("NFKC", texto or "").lower()
    t = t.replace(TESTADO.lower(), " testado ")
    return re.findall(r"\w+", t)


# Una cita se acepta si **todas** sus palabras aparecen en el texto, en orden.
# Entre dos palabras de la cita el documento puede traer hasta `_SALTO_MAX`
# palabras de más (encabezados de página intercalados, llamadas de nota); la
# cita no puede traer ninguna que el documento no tenga.
#
# Medido el 17-oct sobre 61 citas reales y ~400 alteradas a propósito:
#   - tolerancia del 90% de palabras: aceptaba un "no" insertado en 61 de 61
#     y una cifra cambiada en 22 de 22;
#   - salto 25: "no" insertado aceptado 5 veces; salto 12: 1; salto 8: 0,
#     con las mismas 58 de 61 citas reales verificadas; salto 0: sólo 52.
_SALTO_MAX = 8
_BLOQUE_MIN = 4


def _contiene(fragmento: list[str], texto: list[str]) -> bool:
    if not fragmento:
        return False
    primero = fragmento[0]
    for inicio, palabra in enumerate(texto):
        if palabra != primero:
            continue
        j, k, salto = 1, inicio + 1, 0
        while j < len(fragmento) and k < len(texto) and salto <= _SALTO_MAX:
            if texto[k] == fragmento[j]:
                j, salto = j + 1, 0
            elif (j + 1 < len(fragmento)
                  and texto[k] == fragmento[j] + fragmento[j + 1]):
                # El OCR pegó dos palabras ("ahíindicados") y el modelo las
                # separó al copiar: es el mismo texto.
                j, salto = j + 2, 0
            else:
                salto += 1
            k += 1
        if j == len(fragmento):
            return True
    return False


def _tramos(fragmento: str) -> list[list[str]]:
    """Partes del fragmento separadas por puntos suspensivos. Cada una debe ser
    textual; las de menos de `_BLOQUE_MIN` palabras no prueban nada y se omiten."""
    partes = [_palabras(t) for t in re.split(r"\.\.\.|…", fragmento or "")]
    return [t for t in partes if len(t) >= _BLOQUE_MIN]


def verificar_cita(resolucion: ResolucionDigital, pagina, fragmento: str) -> dict:
    """¿Aparece el fragmento en la página que dice el modelo (o junto a ella)?"""
    tramos = _tramos(fragmento)
    if sum(len(t) for t in tramos) < 5:
        return {"verificada": False, "motivo": "fragmento demasiado corto"}
    try:
        p = int(pagina)
    except (TypeError, ValueError):
        return {"verificada": False, "motivo": "página inválida"}
    paginas = resolucion.paginas
    ventana = _palabras(" ".join(paginas.get(x, "") for x in (p - 1, p, p + 1)))
    corregidas = []
    for tramo in tramos:
        if _contiene(tramo, ventana):
            continue
        # Existe, pero no donde dice: se corrige la página, no se descarta.
        # Cada tramo entre puntos suspensivos se busca por separado: el modelo
        # junta a veces resolutivos de páginas distintas en una sola cita.
        q = next((q for q in sorted(paginas) if _contiene(
            tramo, _palabras(paginas[q] + " " + paginas.get(q + 1, "")))), None)
        if q is None:
            return {"verificada": False, "motivo": "el fragmento no aparece en la resolución"}
        corregidas.append(q)
    if not corregidas:
        return {"verificada": True, "pagina_encontrada": p}
    return {"verificada": True, "pagina_encontrada": corregidas[0] if len(tramos) == 1 else p,
            "nota": f"página corregida: el modelo dijo {p}, el texto está en "
                    f"{', '.join(map(str, sorted(set(corregidas))))}"}


# "p. 14", "pág. 14" o un rango "p. 183-184" (se toma la primera; la
# verificación ya mira las contiguas).
_RE_CITA = re.compile(
    r"^\s*\[(\d+)\]\s*p(?:ág(?:ina)?)?s?\.?\s*(\d+)(?:\s*[-–]\s*\d+)?\s*\|\s*(.+?)\s*$", re.I)


def _separar(texto: str) -> dict:
    """Parte la salida en respuesta, citas y no_consta. Nunca revienta."""
    texto = texto or ""
    m_resp = re.search(r"RESPUESTA:\s*(.*?)(?=^\s*CITAS:|^\s*NO_CONSTA:|\Z)", texto, re.S | re.M)
    m_citas = re.search(r"^\s*CITAS:\s*(.*?)(?=^\s*NO_CONSTA:|\Z)", texto, re.S | re.M)
    m_nc = re.search(r"^\s*NO_CONSTA:\s*(\S+)", texto, re.M)
    citas = []
    for linea in (m_citas.group(1).splitlines() if m_citas else []):
        m = _RE_CITA.match(linea)
        if m:
            citas.append({"n": int(m.group(1)), "pagina": int(m.group(2)),
                          "fragmento": m.group(3).strip(' "«»“”')})
    return {
        "respuesta": (m_resp.group(1) if m_resp else texto).strip(),
        "citas": citas,
        "no_consta": bool(m_nc and m_nc.group(1).lower().startswith("s")),
        # NO_CONSTA es opcional: el modelo a veces la omite cuando sí contestó.
        "formato_ok": bool(m_resp and m_citas),
    }


@dataclass
class LecturaResolucion:
    case_link: str
    pregunta: str
    respuesta: str
    citas: list[dict] = field(default_factory=list)
    no_consta: bool = False
    tokens_entrada: int = 0
    tokens_salida: int = 0
    costo_usd: float | None = None
    segundos: float = 0.0
    error: str | None = None
    retiradas: list[str] = field(default_factory=list)
    salida_cruda: str = ""

    @property
    def citas_verificadas(self) -> int:
        return sum(1 for c in self.citas if c.get("verificada"))

    def como_dict(self) -> dict:
        return {
            "case_link": self.case_link, "pregunta": self.pregunta,
            "respuesta": self.respuesta, "citas": self.citas,
            "no_consta": self.no_consta,
            "citas_total": len(self.citas), "citas_verificadas": self.citas_verificadas,
            "tokens_entrada": self.tokens_entrada, "tokens_salida": self.tokens_salida,
            "costo_usd": self.costo_usd, "segundos": self.segundos, "error": self.error,
            "retiradas": self.retiradas, "salida_cruda": self.salida_cruda,
        }


_RE_MARCA = re.compile(r"\[(\d+)\]")
_RE_ORACION = re.compile(r"(?<=[.;!?])\s+")


def retirar_sin_respaldo(respuesta: str, verificadas: set[int]) -> tuple[str, list[str]]:
    """
    Una oración cuyas citas no se verificaron se retira entera, no sólo su
    marca: un aviso junto a la frase deja al lector con la frase (§35.1).
    Si la oración tiene al menos una cita verificada se queda, sin las marcas
    malas. Devuelve el texto y las oraciones retiradas, para la traza.
    """
    retiradas, lineas = [], []
    for linea in respuesta.split("\n"):
        conservadas = []
        for oracion in _RE_ORACION.split(linea):
            marcas = {int(n) for n in _RE_MARCA.findall(oracion)}
            if marcas and not (marcas & verificadas):
                retiradas.append(oracion.strip())
                continue
            for n in marcas - verificadas:
                oracion = re.sub(rf"\s*\[{n}\]", "", oracion)
            conservadas.append(oracion)
        if conservadas or not linea.strip():
            lineas.append(" ".join(conservadas))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip(), retiradas


def _ficha_texto(ficha: dict | None) -> str:
    if not ficha:
        return ""
    lineas = [f"- {k}: {v}" for k, v in ficha.items() if v not in (None, "", [], {})]
    return ("Datos del registro del expediente (no son parte del texto de la "
            "resolución; si los usas, dilo):\n" + "\n".join(lineas) + "\n\n")


async def leer(adapter, model: str, resolucion: ResolucionDigital, pregunta: str,
               ficha: dict | None = None, max_tokens: int = 3000) -> LecturaResolucion:
    if not resolucion.disponible:
        return LecturaResolucion(
            case_link=resolucion.case_link, pregunta=pregunta, no_consta=True,
            respuesta=("No tengo el texto de esta resolución disponible para "
                       "leerlo. Eso no significa que no exista."),
            error=resolucion.motivo)
    aviso = ""
    if resolucion.posiblemente_incompleta:
        aviso = "ADVERTENCIA: el texto pudo llegar incompleto; dilo si afecta la respuesta.\n\n"
    usuario = (f"{aviso}{_ficha_texto(ficha)}Resolución {resolucion.case_link}, "
               f"texto completo:\n\n{resolucion.texto_con_paginas()}\n\n"
               f"Pregunta: {pregunta}")
    mensajes = [{"role": "system", "content": SISTEMA},
                {"role": "user", "content": usuario}]
    inicio = time.monotonic()
    r = await adapter.completion_with_tools(
        messages=mensajes, model=model, tools=[], temperature=0.1,
        max_tokens=max_tokens, solo_texto=True)
    segundos = round(time.monotonic() - inicio, 1)
    lectura = LecturaResolucion(
        case_link=resolucion.case_link, pregunta=pregunta, respuesta="",
        tokens_entrada=r.input_tokens, tokens_salida=r.output_tokens,
        costo_usd=costo_estimado(model, r.input_tokens, r.output_tokens),
        segundos=segundos)
    datos = _separar(r.content)
    if not datos["formato_ok"]:
        lectura.error = "la salida no siguió el formato"
    lectura.salida_cruda = r.content or ""
    citas, buenas = [], set()
    for c in datos.get("citas") or []:
        v = verificar_cita(resolucion, c.get("pagina"), c.get("fragmento", ""))
        citas.append({**c, **v})
        if v["verificada"]:
            buenas.add(c["n"])
    lectura.citas = citas
    lectura.no_consta = bool(datos.get("no_consta"))
    # Una marca sin entrada en CITAS cuenta como no verificada.
    lectura.respuesta, lectura.retiradas = retirar_sin_respaldo(datos.get("respuesta", ""), buenas)
    if not lectura.respuesta:
        lectura.respuesta = (
            "La resolución no contiene información sobre lo que preguntas."
            if lectura.no_consta else
            "No pude respaldar la respuesta con citas textuales de la resolución.")
    return lectura
