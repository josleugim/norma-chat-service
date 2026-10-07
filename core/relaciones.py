"""
Mapa de relaciones entre resoluciones: la historia procesal.

COFECE, revisión del 25-sep-2026 (§4.3): *"antes de utilizar una resolución
como fundamento de una respuesta, comprobar sus actuaciones posteriores
conocidas y advertirlas, aunque el usuario no pregunte por su estado actual"*.
Y el 6-oct, en vivo: el chat no encontró los amparos de VCN-001-2017 aunque las
dos sentencias traen `parent = VCN-001-2017`.

Los enlaces salen **de los campos que documenta la API**, nunca de la forma del
identificador. Quitar el sufijo a `VCN-004-2022_2025_10_09` sugiere un padre;
no lo prueba.

| Posterior → origen | Campos |
|---|---|
| Cumplimiento → resolución administrativa | `parent` |
| Juzgado de distrito → expediente administrativo | `parent` |
| Tribunal colegiado → sentencia del juzgado | `originAmparoCaseFiles`, `appealedJudgmentBody`, `appealedJudgmentDate` contra `judicialCaseFile`/`accumulatedCaseFiles`, `authority`, `judgmentDate` |
| SCJN → tribunal colegiado | `relatedTccCaseFile`, `relatedCollegiateCourt`, `relatedTccDecisionDate` contra `judicialCaseFile`, `authority`, `reviewResolutionDate` |

Un enlace tiene estado:

- `resuelto` — todos los datos del enlace coinciden con un solo documento.
- `resuelto_sin_numero` — órgano y fecha coinciden con un solo documento, pero
  ese documento no trae número de expediente para confirmarlo. Se usa y se
  declara.
- `externo` — el origen informado no está en el acervo consultable (SCJN
  480/2018 → TCC 82/2018).
- `pendiente` — no se pudo determinar: sin datos, o con más de un candidato.

Un enlace pendiente no se convierte en "no hay historia procesal".
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Campos que describen qué decidió una actuación. Se pasan tal cual; el modelo
# interpreta el efecto, el código no.
_CAMPOS_EFECTO = (
    "senseOfAmparo", "senseOfReview", "finalAmparoResult", "senseOfResolution",
    "scopeOfCompliance", "judgmentImplementation", "judicialDecisionEffects",
)
_CAMPOS_FECHA = (
    "amparoComplianceResolutionDate", "judgmentDate", "reviewResolutionDate",
    "resolutionDate",
)
_LIMITE_EFECTO = 500


def _dict(r: Any) -> dict:
    if isinstance(r, dict):
        return r
    return r.model_dump() if hasattr(r, "model_dump") else dict(r)


def _origen_parent(r: dict) -> str:
    p = r.get("parent")
    if isinstance(p, dict) and p.get("caseLink"):
        return str(p["caseLink"]).strip()
    return str(r.get("expediente_principal") or "").strip()


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).lower()


_ORDINALES = {
    "primer": "1", "primero": "1", "segundo": "2", "tercer": "3", "tercero": "3",
    "cuarto": "4", "quinto": "5", "sexto": "6", "septimo": "7", "octavo": "8",
}


def _organo_clave(s: Any) -> tuple | None:
    """
    El órgano reducido a (tipo, ordinal): ("juzgado", "2"), ("tribunal", "1").

    El nombre completo no sirve para comparar: el 7-oct, el registro de un
    juzgado decía "…Administrativa Especializada…" y el tribunal que lo
    revisa lo citaba como "…Administrativa Especializado…". Por esa letra,
    seis sentencias que sí están en el acervo quedaban como externas.
    """
    import unicodedata
    t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    if not t.strip():
        return None
    tipo = ("juzgado" if "juzgado" in t else
            "tribunal" if "tribunal" in t else
            "scjn" if "suprema" in t or "scjn" in t else None)
    ordinal = next((n for palabra, n in _ORDINALES.items()
                    if re.search(r"\b" + palabra + r"\b", t)), None)
    return (tipo, ordinal) if tipo else None


def _expedientes_de(r: dict) -> set[str]:
    """Números de expediente judicial que identifican al documento."""
    salida = set()
    if r.get("judicialCaseFile"):
        salida.add(_norm(r["judicialCaseFile"]))
    for a in r.get("accumulatedCaseFiles") or []:
        salida.add(_norm(a))
    return salida


def _tipo(r: dict) -> str:
    naturaleza = str(r.get("natureOfResolution") or "")
    if "cumplimiento" in naturaleza.lower():
        return naturaleza
    return str(r.get("typeOfProcedure") or "actuación")


def _fecha(r: dict) -> str | None:
    for c in _CAMPOS_FECHA:
        if r.get(c):
            return r[c]
    return None


@dataclass
class Enlace:
    posterior: str
    origen: str | None          # None si no se pudo determinar
    estado: str                 # resuelto | resuelto_sin_numero | externo | pendiente
    campos: list[str]           # campos que lo acreditan
    referencia: str = ""        # lo que dice el registro, para enlaces no resueltos
    motivo: str = ""

    def a_dict(self) -> dict:
        d = {"posterior": self.posterior, "origen": self.origen,
             "estado": self.estado, "acreditado_por": self.campos}
        if self.referencia:
            d["referencia_informada"] = self.referencia
        if self.motivo:
            d["motivo"] = self.motivo
        return d


@dataclass
class MapaDeRelaciones:
    registros: dict[str, dict]
    enlaces: list[Enlace] = field(default_factory=list)
    hacia_abajo: dict[str, list[Enlace]] = field(default_factory=lambda: defaultdict(list))
    hacia_arriba: dict[str, Enlace] = field(default_factory=dict)
    construido_en: float = field(default_factory=time.time)

    # ── Construcción ─────────────────────────────────────────

    @classmethod
    def desde_registros(cls, registros) -> "MapaDeRelaciones":
        por_cl = {}
        for r in registros:
            d = _dict(r)
            cl = str(d.get("caseLink") or "").strip()
            if cl:
                por_cl[cl] = d
        mapa = cls(registros=por_cl)
        for cl, r in por_cl.items():
            e = mapa._enlace_de(cl, r)
            if e is not None:
                mapa._agregar(e)
        return mapa

    def _agregar(self, e: Enlace) -> None:
        self.enlaces.append(e)
        self.hacia_arriba[e.posterior] = e
        if e.origen and e.estado != "externo":
            self.hacia_abajo[e.origen].append(e)

    def _enlace_de(self, cl: str, r: dict) -> Enlace | None:
        padre = _origen_parent(r)
        if padre:
            return Enlace(
                cl, padre,
                "resuelto" if padre in self.registros else "externo",
                ["parent"],
            )
        if r.get("originAmparoCaseFiles"):
            return self._resolver_judicial(
                cl, r,
                numeros={_norm(x) for x in r["originAmparoCaseFiles"]},
                organo=r.get("appealedJudgmentBody"),
                fecha=r.get("appealedJudgmentDate"),
                fecha_destino="judgmentDate",
                campos=["originAmparoCaseFiles", "appealedJudgmentBody",
                        "appealedJudgmentDate"],
            )
        if r.get("relatedTccCaseFile"):
            return self._resolver_judicial(
                cl, r,
                numeros={_norm(r["relatedTccCaseFile"])},
                organo=r.get("relatedCollegiateCourt"),
                fecha=r.get("relatedTccDecisionDate"),
                fecha_destino="reviewResolutionDate",
                campos=["relatedTccCaseFile", "relatedCollegiateCourt",
                        "relatedTccDecisionDate"],
            )
        if str(r.get("typeOfProcedure") or "").lower().startswith("amparo indirecto"):
            # Una sentencia de amparo indirecto siempre tiene acto de origen.
            # Si el registro no lo informa, es un hueco del dato, no ausencia
            # de relación.
            return Enlace(cl, None, "pendiente", [],
                          motivo="el registro no informa el expediente administrativo de origen")
        return None

    def _resolver_judicial(self, cl, r, numeros, organo, fecha, fecha_destino,
                           campos) -> Enlace:
        """
        Busca el documento de origen por número, órgano y fecha.

        El número de amparo sin órgano no identifica una sentencia: hay dos
        `275/2023`, de juzgados distintos. Y compartir expediente no permite
        escoger cualquiera de las dos sentencias de `278/2023`: la fecha decide.
        """
        referencia = (f"{', '.join(sorted(numeros))} · {organo or 's/órgano'} · "
                      f"{fecha or 's/fecha'}")
        candidatos = []
        for ocl, o in self.registros.items():
            if ocl == cl:
                continue
            if fecha and o.get(fecha_destino) != fecha:
                continue
            if organo and _organo_clave(o.get("authority")) != _organo_clave(organo):
                continue
            exp = _expedientes_de(o)
            if exp and not (exp & numeros):
                continue
            candidatos.append((ocl, bool(exp)))
        con_numero = [c for c, tiene in candidatos if tiene]
        sin_numero = [c for c, tiene in candidatos if not tiene]

        if len(con_numero) == 1:
            return Enlace(cl, con_numero[0], "resuelto", campos, referencia)
        if not con_numero and len(sin_numero) == 1 and organo and fecha:
            return Enlace(
                cl, sin_numero[0], "resuelto_sin_numero", campos, referencia,
                motivo=("coinciden órgano y fecha; el documento de origen no "
                        "trae número de expediente para confirmarlo"))
        if not candidatos:
            return Enlace(cl, None, "externo", campos, referencia,
                          motivo=(f"el origen informado ({referencia}) no está en el "
                                  "acervo: no se puede consultar ni citar. Dilo, sin hablar de bases "
                                  "ni registros."))
        return Enlace(cl, None, "pendiente", campos, referencia,
                      motivo=f"{len(candidatos)} documentos coinciden; no se elige uno")

    # ── Consulta ─────────────────────────────────────────────

    def posteriores(self, cl: str, profundidad: int = 4) -> list[dict]:
        """
        Toda la cadena hacia abajo: un expediente administrativo, sus amparos,
        las revisiones de esos amparos, lo que la SCJN dijo de esas revisiones,
        y los cumplimientos.
        """
        salida, vistos = [], {cl}

        def bajar(origen: str, nivel: int):
            if nivel > profundidad:
                return
            for e in sorted(self.hacia_abajo.get(origen, []), key=lambda x: x.posterior):
                if e.posterior in vistos:
                    continue
                vistos.add(e.posterior)
                salida.append(self._ficha(e, nivel))
                bajar(e.posterior, nivel + 1)

        bajar(cl, 1)
        return salida

    def antecedentes(self, cl: str, profundidad: int = 4) -> list[dict]:
        """La cadena hacia arriba, hasta el expediente administrativo."""
        salida, actual, vistos = [], cl, {cl}
        for _ in range(profundidad):
            e = self.hacia_arriba.get(actual)
            if e is None:
                break
            salida.append(e.a_dict())
            if not e.origen or e.origen in vistos or e.estado == "externo":
                break
            vistos.add(e.origen)
            actual = e.origen
        return salida

    def _ficha(self, e: Enlace, nivel: int) -> dict:
        from core.evidence_cache import recortar_sin_borrar_en_silencio
        r = self.registros.get(e.posterior, {})
        ficha = {
            "expediente": e.posterior,
            "deriva_de": e.origen,
            "nivel": nivel,
            "tipo": _tipo(r),
            "fecha_de_la_resolucion": _fecha(r),
            "enlace": e.estado,
        }
        if e.motivo:
            ficha["nota_del_enlace"] = e.motivo
        if "cumplimiento" in ficha["tipo"].lower():
            # 6-oct: con el amparo 1258/2017 y este cumplimiento a la vista, el
            # modelo escribió "en cumplimiento del amparo 1258/2017" aunque
            # ningún registro los enlaza, y los efectos ni coinciden. Una regla
            # general en el prompt no lo evitó; el dato explícito sí tiene que.
            ficha["sentencia_que_cumple"] = (
                "NO INFORMADA en la información disponible. No digas cuál amparo "
                "cumple ni que un amparo concreto lo motivó.")
        for c in _CAMPOS_EFECTO:
            v = r.get(c)
            if v in (None, "", [], {}):
                continue
            if isinstance(v, str) and len(v) > _LIMITE_EFECTO:
                v = recortar_sin_borrar_en_silencio(v, _LIMITE_EFECTO)
            ficha[c] = v
        return ficha

    def historia_de(self, cl: str) -> dict | None:
        """Lo que hay que advertir de un documento, o None si no hay nada."""
        posteriores = self.posteriores(cl)
        antecedentes = self.antecedentes(cl)
        if not posteriores and not antecedentes:
            return None
        h: dict = {}
        if antecedentes:
            h["deriva_de"] = antecedentes
        if posteriores:
            h["actuaciones_posteriores"] = posteriores
        # Un origen externo o pendiente es un límite que hay que decir. Pasó
        # el 6-oct con SCJN 480/2018: la respuesta citaba "deriva del 82/2018"
        # sin decir que esa sentencia no está, en 3 de 3 corridas.
        limites = [a for a in antecedentes if a.get("estado") in ("externo", "pendiente")]
        if limites:
            h["LIMITE"] = " ".join(
                f"{a['posterior']}: {a.get('motivo') or 'origen no determinado'}"
                for a in limites)
        cumplimientos = [p["expediente"] for p in posteriores
                         if "cumplimiento" in p["tipo"].lower()]
        amparos = [p["expediente"] for p in posteriores
                   if p["tipo"].lower().startswith("amparo indirecto")]
        if cumplimientos and amparos:
            # La inferencia se hace desde el amparo: "sólo éste concedió, así
            # que éste motivó el cumplimiento". El 6-oct, con la advertencia
            # sólo en la ficha del cumplimiento, el modelo la siguió haciendo
            # en 2 de 3 corridas. Va a nivel del documento, nombrando ambos.
            h["NO_ENLAZADO"] = (
                f"La información disponible no indica cuál de los amparos ({', '.join(amparos)}) "
                f"dio lugar a {', '.join(cumplimientos)}, ni a quién benefició "
                "cada amparo. Aunque sólo uno haya concedido, no lo afirmes "
                "('lo que llevó a', 'en cumplimiento de ese amparo'). Si hace "
                "falta, dilo una sola vez en la respuesta, no por cada "
                "documento.")
        return h

    def pendientes(self) -> list[dict]:
        return [e.a_dict() for e in self.enlaces
                if e.estado in ("pendiente", "resuelto_sin_numero")]

    def resumen(self) -> dict:
        c = defaultdict(int)
        for e in self.enlaces:
            c[e.estado] += 1
        return {"documentos": len(self.registros), "enlaces": dict(c)}


def resumen_historia_procesal(entradas: list[dict], texto: str, registry) -> dict | None:
    """
    Para la traza: de los documentos que la respuesta usó, qué actuaciones
    posteriores tenía el modelo a la vista y cuáles no mencionó.

    "Usó" = la respuesta cita su marcador o nombra su expediente. "Mencionó" =
    cita el marcador de la actuación o nombra su expediente. Es objetivo y no
    juzga si el aviso fue suficiente: eso lo adjudica una persona.
    """
    import re
    if not entradas:
        return None
    if any(e.get("estado") == "no_consultada" for e in entradas):
        return {"estado": "no_consultada"}
    texto = texto or ""
    citados = set(re.findall(r"\[([CE]\d+)\]", texto))
    citados_cl = {registry.case_link_of(m) for m in citados} if registry else set()

    def aparece(cl: str, ref: str | None) -> bool:
        return bool(ref and ref in citados) or cl in citados_cl or (
            re.search(rf"(?<![\w-]){re.escape(cl)}(?![\w-])", texto) is not None)

    usados, no_mencionados, sin_aviso, vistos = [], [], [], set()
    for e in entradas:
        doc = e.get("documento")
        if not doc or doc in vistos or not e.get("posteriores"):
            continue
        vistos.add(doc)
        if not aparece(doc, None):
            continue
        usados.append(doc)
        faltan = [p["expediente"] for p in e["posteriores"]
                  if not aparece(p["expediente"], p.get("ref"))]
        no_mencionados.extend({"documento": doc, "actuacion": x} for x in faltan)
        if len(faltan) == len(e["posteriores"]):
            sin_aviso.append(doc)
    # No toda actuación posterior es pertinente a la pregunta —un amparo
    # negado no cambia la sanción del notario—, así que el detalle no es un
    # defecto por sí mismo. El indicador es el caso inequívoco: un documento
    # usado con historia y NINGUNA actuación advertida.
    return {
        "documentos_con_historia": len(vistos),
        "usados_con_posteriores": usados,
        "documentos_sin_aviso": sin_aviso,
        "posteriores_no_mencionados": no_mencionados,
    }
