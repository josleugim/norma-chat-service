"""
Universo consultable restringido a una lista cerrada de expedientes.

Por qué existe. El protocolo del holdout de COFECE (paso 01, 20-sep-2026) es
explícito:

    "Configurar el entorno para que las consultas estructuradas, búsquedas de
    criterios y agregaciones usen sólo los documentos de universo_63.csv. No
    sustituir este alcance por una instrucción añadida a cada pregunta ni por
    un filtro exclusivo del prefijo VCN."

Las dos prohibiciones importan y descartan los dos atajos obvios:

- **No por prompt.** Meter "limítate a estos 63" en cada pregunta haría que el
  alcance dependiera de que el modelo obedezca, y el propio holdout mide si
  obedece. Sería medir el termómetro con el termómetro.
- **No por prefijo.** `caseLink=VCN` deja fuera las 25 sentencias judiciales,
  que sí están en el universo, y deja dentro cualquier VCN futuro que no esté
  en la lista.

Qué hace en vez de eso. El universo es cerrado y pequeño —63 documentos—, así
que se carga **una vez al arrancar** y las consultas estructuradas se resuelven
contra esa copia en memoria. Tres consecuencias que valen más que el ahorro de
peticiones:

1. **Los totales son exactos.** `meta.total` de la API describe el acervo
   entero, no nuestro universo; con la lista cargada, el denominador de
   cualquier agregación es un hecho y no una inferencia.
2. **El truncamiento deja de ser posible.** No hay `limit` contra el que topar,
   así que desaparece la clase de falla que perseguimos desde agosto: afirmar
   cobertura completa sobre una muestra.
3. **El acervo queda congelado durante la corrida**, que es justo lo que pide
   el baseline congelado. Si la API cambia a media batería, esta corrida no se
   entera y la comparación sobrevive.

Sobre los criterios: **no hacen falta filtros**. Medido contra staging el
20-sep-2026 con ~3,000 fragmentos de diez consultas distintas, el índice de
`/paragraphs/vector-search` contiene exactamente 62 de los 63 documentos y
**cero fuera del universo**. El que falta es `VCN-001-2017_2019_03_14`, cuyo
dedup viene vacío —el mismo que COFECE marcó en amarillo al mandar el listado—.
El alcance de criterios ya se cumple por datos; aun así se verifica al arrancar
en vez de darse por bueno.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class UniversoRestringido:
    """Lista blanca de expedientes consultables, cargada de un archivo."""

    def __init__(self, case_links, etiqueta: str = ""):
        self.case_links = {str(c).strip() for c in case_links if str(c).strip()}
        self.etiqueta = etiqueta
        # Comparación tolerante a mayúsculas: los identificadores judiciales
        # mezclan estilos (`565_2023_1TCC_2025_04_24`) y un desajuste de caja
        # descartaría un documento del universo en silencio.
        self._upper = {c.upper() for c in self.case_links}

        # Emisor de cada documento, cargado una vez al arrancar desde el mismo
        # barrido que hace el censo. Un criterio no trae el órgano que lo
        # dictó, así que el agente lo infería: en H17 no identificó al Juzgado
        # Tercero como emisor del criterio del 43/2021, y en H18 confundió a
        # COFECE —que dictó el acto— con CNA, que era la destinataria del
        # cumplimiento.
        #
        # `authority` lo dice con precisión y cubre 57 de los 63; para los seis
        # restantes queda vacío y el agente lo sabrá en vez de suponerlo.
        self.emisores: dict[str, str] = {}

    def cargar_emisores(self, registros) -> int:
        """Guarda el emisor de cada documento del universo. Devuelve cuántos."""
        for r in registros:
            d = r.model_dump() if hasattr(r, "model_dump") else dict(r)
            cl = d.get("caseLink")
            if cl in self:
                emisor = d.get("authority") or d.get("judicialBody")
                if emisor:
                    self.emisores[str(cl)] = str(emisor)
        return len(self.emisores)

    def emisor_de(self, case_link) -> str | None:
        return self.emisores.get(str(case_link or "").strip())

    def __len__(self) -> int:
        return len(self.case_links)

    def __contains__(self, case_link) -> bool:
        return str(case_link or "").strip().upper() in self._upper

    def filtrar(self, registros, get=lambda r: getattr(r, "caseLink", None)):
        """Deja sólo los registros del universo. No reordena."""
        return [r for r in registros if get(r) in self]

    @classmethod
    def desde_archivo(cls, ruta: str | Path) -> "UniversoRestringido":
        """
        Carga la lista. Un archivo ausente o ilegible **revienta**: arrancar
        sin restricción cuando se pidió restricción produciría una corrida que
        parece válida y mide otro universo.
        """
        p = Path(ruta)
        datos = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(datos, dict):
            datos = datos.get("case_links") or datos.get("universo") or []
        links = [
            d.get("case_link") if isinstance(d, dict) else d
            for d in datos
        ]
        u = cls(links, etiqueta=p.name)
        if not u.case_links:
            raise ValueError(f"{p} no contiene ningún case_link")
        logger.info(f"Universo restringido: {len(u)} expedientes desde {p.name}")
        return u
