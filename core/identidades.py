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

# Acto derivado de un expediente administrativo: el principal más la fecha en
# que se dictó. `VCN-004-2022_2025_10_09` es la resolución en cumplimiento de
# amparo de VCN-004-2022, dictada el 9 de octubre de 2025.
_ADMIN_CON_ACTO = re.compile(
    r"^(?P<principal>[A-Z]{2,5}-\d{3}-\d{4})_(?P<a>\d{4})_(?P<m>\d{2})_(?P<d>\d{2})$"
)

_MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12",
}
# "9 de octubre de 2025", "09-10-2025", "2025-10-09".
_FECHA_TEXTO = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})\b", re.IGNORECASE
)
_FECHA_NUM = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")

# Pide el acto derivado, no el principal.
_PIDE_CUMPLIMIENTO = re.compile(
    r"cumplimiento\s+de\s+amparo|resoluci[óo]n\s+de\s+cumplimiento|"
    r"en\s+cumplimiento|acatamiento",
    re.IGNORECASE,
)

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
        # Actos derivados por expediente principal: VCN-004-2022 →
        # [{case_link: VCN-004-2022_2025_10_09, fecha: 2025-10-09}]
        self.actos_de: dict[str, list[dict]] = defaultdict(list)
        for cl in case_links:
            cl = str(cl or "").strip()
            if not cl:
                continue
            self.conocidos.add(cl)
            p = partes_de(cl)
            if p:
                self.por_numero_anio[(p["numero"], p["anio"])].append(p)
            m = _ADMIN_CON_ACTO.match(cl)
            if m:
                self.actos_de[m.group("principal")].append({
                    "case_link": cl,
                    "fecha": f"{m.group('a')}-{m.group('m')}-{m.group('d')}",
                })

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

        salida.extend(self._resolver_actos(texto))
        return salida

    def _resolver_actos(self, texto: str) -> list[dict]:
        """
        Expedientes administrativos nombrados en el texto, apuntando al ACTO
        pedido cuando lo hay.

        H10 del diagnóstico del 22-sep: la pregunta pide el cumplimiento de
        amparo del 9 de octubre de 2025 de VCN-004-2022, y el agente buscaba
        sobre `VCN-004-2022`. Como el filtro de la API hace substring y no
        igualdad —verificado: pedir el principal devuelve 16 criterios suyos
        MÁS los 14 del cumplimiento—, la respuesta mezclaba la fórmula de
        incremento del acto original con lo que se preguntaba del cumplimiento.

        Resolver el acto a su identificador completo hace el filtro exclusivo
        y evita la mezcla en origen.
        """
        fecha = self._fecha_de(texto)
        pide_cumplimiento = bool(_PIDE_CUMPLIMIENTO.search(texto))
        salida: list[dict] = []

        for cl in sorted(self.conocidos):
            if "_" in cl or not re.search(rf"\b{re.escape(cl)}\b", texto):
                continue  # los derivados se nombran por su principal
            actos = self.actos_de.get(cl, [])
            if not actos:
                continue

            elegidos = [a for a in actos if fecha and a["fecha"] == fecha]
            if not elegidos and pide_cumplimiento:
                # Pide el cumplimiento sin dar fecha: si hay uno solo, es ése.
                elegidos = actos if len(actos) == 1 else []
            if not elegidos:
                continue

            salida.append({
                "mencion": cl + (f" ({fecha})" if fecha else " (cumplimiento)"),
                "candidatos": [a["case_link"] for a in elegidos],
                "ambiguo": len(elegidos) > 1,
                "organo_pedido": None,
                "acto_de": cl,
            })
        return salida

    @staticmethod
    def _fecha_de(texto: str) -> str | None:
        m = _FECHA_TEXTO.search(texto or "")
        if m:
            mes = _MESES.get(m.group(2).lower())
            if mes:
                return f"{m.group(3)}-{mes}-{int(m.group(1)):02d}"
        m = _FECHA_NUM.search(texto or "")
        if m:
            return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        return None

    def existe(self, case_link: str) -> bool:
        return str(case_link or "").strip() in self.conocidos
