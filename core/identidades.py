"""
Resolver un número natural de expediente a la identidad real del acervo.

C02 del diagnóstico de COFECE (21-sep-2026). Cuando la pregunta dice
"el amparo en revisión 677/2024", ese texto llegaba intacto como `searchData`
y devolvía cero en ocho de nueve corridas. En la novena el modelo eligió por su
cuenta `id_expediente: "677_2024_1SCJN"` y lo encontró. **El acierto dependía
de que el modelo adivinara el identificador interno.**

Lo que hace este módulo es lo que faltaba: traducir cómo se nombra un asunto en
lenguaje jurídico —número/año, órgano, a veces la fecha del acto— a los
identificadores que existen de verdad en el universo consultable.

Principios que el diagnóstico pide explícitamente y que aquí se respetan:

- **No fabricar identificadores por concatenación.** Sólo se devuelve lo que
  existe en el universo cargado. Si `677/2024` no está, no se inventa
  `677_2024_1SCJN`: se devuelve vacío y quien pregunte sabrá que no se resolvió.
- **No fusionar asuntos que comparten número.** `275/2023` existe en el Juzgado
  Primero y en el Tercero. Son dos asuntos distintos y se devuelven los dos,
  para que el paso siguiente desambigüe por órgano o pregunte, en vez de elegir
  uno en silencio.
- **Conservar los actos de un mismo expediente por separado.** `278_2023_1JD`
  tiene resoluciones de 2024 y de 2025; degradar una al expediente base pierde
  justo lo que la pregunta distingue.
- **Una búsqueda léxica vacía no significa que el documento no exista**, si su
  identidad todavía no se resolvió.
"""
import re
from collections import defaultdict

# `1259-1260_2017_2JD`, `565_2023_1TCC_2025_04_24`, `480_2018_2SCJN`.
# El número puede traer acumulados con guion; el sufijo de fecha es opcional.
_JUDICIAL = re.compile(
    r"^(?P<numero>[\d\-]+)_(?P<anio>\d{4})_(?P<organo>\d*[A-Z]{2,4})"
    r"(?:_(?P<acto>[\d_\-]+))?$"
)

# Cómo se escribe en una pregunta: "677/2024", "amparo 275/2023",
# "1259-1260/2017". La barra es la convención jurídica; el guion bajo es la
# interna.
_NATURAL = re.compile(r"\b(?P<numero>\d[\d\-]*)\s*/\s*(?P<anio>\d{4})\b")

# Menciones de órgano en lenguaje natural → marca del identificador.
_ORGANOS = [
    (r"\bsuprema corte\b|\bscjn\b|\b(primera|segunda)\s+sala\b", "SCJN"),
    (r"\btribunal(?:es)?\s+colegiado\b|\bcolegiado\b|\btcc\b", "TCC"),
    (r"\bjuzgado\b|\bjuez\b|\bdistrito\b|\bjd\b", "JD"),
]

_ORDINALES = [
    (r"\bprimer[oa]?\b|\b1[oº°]?\b", "1"),
    (r"\bsegund[oa]\b|\b2[oº°]?\b", "2"),
    (r"\btercer[oa]?\b|\b3[oº°]?\b", "3"),
    (r"\bcuart[oa]\b|\b4[oº°]?\b", "4"),
]


def partes_de(case_link: str) -> dict | None:
    """Descompone un identificador judicial. None si no lo es."""
    m = _JUDICIAL.match((case_link or "").strip())
    if not m:
        return None
    d = m.groupdict()
    organo = d["organo"]
    marca = re.sub(r"^\d+", "", organo)
    ordinal = re.match(r"^(\d+)", organo)
    return {
        "case_link": case_link,
        "numero": d["numero"],
        "anio": d["anio"],
        "organo": organo,
        "marca_organo": marca,
        "ordinal_organo": ordinal.group(1) if ordinal else None,
        "acto": d.get("acto"),
    }


class ResolutorDeIdentidades:
    """
    Traduce menciones naturales a identificadores reales del universo.

    Se construye desde la lista de expedientes cargada, así que nunca puede
    devolver algo que no exista.
    """

    def __init__(self, case_links):
        self.por_numero_anio: dict[tuple, list[dict]] = defaultdict(list)
        self.conocidos: set[str] = set()
        for cl in case_links:
            cl = str(cl or "").strip()
            if not cl:
                continue
            self.conocidos.add(cl)
            p = partes_de(cl)
            if p:
                self.por_numero_anio[(p["numero"], p["anio"])].append(p)

    def resolver(self, texto: str) -> list[dict]:
        """
        Candidatos para las menciones naturales que aparezcan en el texto.

        Devuelve **todos** los que empatan número/año, filtrados por órgano
        sólo si el texto lo menciona. Varios candidatos no es un fallo: es la
        información de que hace falta desambiguar.
        """
        texto = texto or ""
        marca = next(
            (m for patron, m in _ORGANOS if re.search(patron, texto, re.I)), None
        )
        ordinal = next(
            (o for patron, o in _ORDINALES if re.search(patron, texto, re.I)), None
        )

        salida: list[dict] = []
        for m in _NATURAL.finditer(texto):
            clave = (m.group("numero"), m.group("anio"))
            candidatos = list(self.por_numero_anio.get(clave, []))
            if marca:
                candidatos = [c for c in candidatos if c["marca_organo"] == marca]
            if ordinal and len(candidatos) > 1:
                por_ordinal = [
                    c for c in candidatos if c["ordinal_organo"] == ordinal
                ]
                if por_ordinal:
                    candidatos = por_ordinal
            if candidatos:
                salida.append({
                    "mencion": m.group(0),
                    "candidatos": [c["case_link"] for c in candidatos],
                    "ambiguo": len(candidatos) > 1,
                    "organo_pedido": marca,
                })
        return salida

    def existe(self, case_link: str) -> bool:
        return str(case_link or "").strip() in self.conocidos
