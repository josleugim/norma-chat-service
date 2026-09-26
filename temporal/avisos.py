"""
Acuerdos de suspensión que coinciden con un periodo, y cobertura del calendario.

Alcance aprobado por Imanol (COFECE) el 25-sep-2026, §8 de su revisión:

    "Contar días hábiles conforme al calendario general de la autoridad y
     mostrar por separado acuerdos de suspensión coincidentes, con fechas y
     enlaces, sin decidir ni descontar su aplicación al expediente."

Eso resolvió el bloqueo que arrastrábamos desde el 22-sep. Habíamos pedido que
alguien decidiera si las concentraciones estaban dentro de la excepción del
acuerdo CFCE-084-2020, porque sin esa respuesta siete de 34 VCN calculables no
tenían número defendible. La decisión fue mejor que la pregunta: **no hace falta
decidirlo**. Se cuenta con el calendario ordinario, se informa la coincidencia y
el usuario resuelve la aplicación.

## Las dos cosas que este módulo NO hace

**No decide si una suspensión aplica.** Cada aviso sale con
`aplicabilidad_al_expediente = "no_evaluada"`. La coincidencia es un cruce de
fechas programado, no un juicio jurídico, y confundirlos sería exactamente el
error que el alcance aprobado evita.

**No descuenta días por un aviso.** Los 160 días condicionados del catálogo no
son inhábiles generales. Tampoco lo son las cuatro fechas "hábiles sin
términos": si una de ellas además es inhábil general, se excluye por ese
fundamento y no por ser interrupción.

## Dos coberturas separadas, y por qué

    cobertura_calendario_general   si falta, la cifra de hábiles no se publica
                                   como comprobada
    cobertura_avisos               si falta, se dice que la revisión de
                                   suspensiones quedó incompleta, y la cifra
                                   sigue en pie

Textual de COFECE: *"Un pendiente de otro periodo no bloquea la operación ajena
a él."* Y la distinción sale de los datos, no de una lista nuestra: los
controles del catálogo que apuntan a un calendario (`CAL-*`) son cobertura
general; los que apuntan a acuerdos (`S*`, `CFCE-*`, `CNA-*`) son cobertura de
avisos.
"""
import csv
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Etiqueta que acompaña a toda cifra de esta métrica, incluidas las
# agregaciones. No es decorativa: sin ella, un promedio de días hábiles se lee
# como tiempo procesal efectivo, que es justo lo que esta cuenta no mide.
ETIQUETA_ALCANCE = "días hábiles según calendario general, sin ajustar suspensiones"

NO_EVALUADA = "no_evaluada"

# Un control pendiente que apunta a un calendario afecta el conteo; uno que
# apunta a un acuerdo afecta los avisos. La distinción se lee del propio dato.
_PREFIJOS_CALENDARIO = ("CAL-",)

# Estados de control que dejan un periodo sin confirmar.
_ABIERTOS = ("PENDIENTE", "NO_LOCALIZADO")


def _fecha(v) -> date | None:
    t = str(v or "").strip()[:10]
    if not t:
        return None
    for f in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            pass
    return None


def _intersecta(a_ini, a_fin, b_ini, b_fin) -> bool:
    """
    ¿Se solapan dos periodos? Un extremo abierto no significa infinito.

    El control C03 lo dice expresamente: *"Fin vacío NO significa suspensión
    indefinida."* Un periodo sin fin conocido se trata como abierto para
    detectar la coincidencia, pero el aviso declara que su rango está
    incompleto en vez de prolongarlo.
    """
    if a_fin and b_ini and b_ini > a_fin:
        return False
    if b_fin and a_ini and a_ini > b_fin:
        return False
    return True


class CatalogoAvisos:
    """Acuerdos de suspensión y controles de cobertura del calendario."""

    def __init__(self, suspensiones: list[dict], fuentes: dict,
                 controles: list[dict], etiqueta: str = ""):
        self.suspensiones = suspensiones
        self.fuentes = fuentes
        self.controles = controles
        self.etiqueta = etiqueta

    @classmethod
    def desde_directorio(cls, ruta) -> "CatalogoAvisos":
        """
        Carga el catálogo. Un directorio ausente devuelve un catálogo vacío
        **marcado como no cargado**, no un catálogo sin coincidencias: son
        cosas distintas y confundirlas produciría "no hubo suspensiones" a
        partir de un error de lectura.
        """
        p = Path(ruta)
        if not p.is_dir():
            logger.warning(f"Catálogo de avisos no disponible en {p}")
            return cls([], {}, [], etiqueta="")

        def leer(nombre):
            f = p / nombre
            if not f.exists():
                return []
            with f.open(encoding="utf-8-sig") as fh:
                return list(csv.DictReader(fh))

        fuentes = {
            r.get("fuente_id"): r for r in leer("fuentes.csv")
            if r.get("fuente_id")
        }
        c = cls(leer("suspensiones.csv"), fuentes, leer("controles.csv"),
                etiqueta=p.name)
        logger.info(
            f"Catálogo de avisos: {len(c.suspensiones)} acuerdos, "
            f"{len(c.fuentes)} fuentes, {len(c.controles)} controles"
        )
        return c

    @property
    def cargado(self) -> bool:
        return bool(self.suspensiones)

    # ── Avisos ──────────────────────────────────────────────────

    def avisos_para(self, ini: date, fin: date,
                    autoridad: str | None = None) -> list[dict]:
        """
        Acuerdos cuyo periodo intersecta la ventana. Sin juicio de aplicación.

        No se filtra por `typeOfProcedure`, `applicableLaw` ni por el prefijo
        del expediente: COFECE es explícito en que nada de eso puede usarse
        como regla automática de excepción.
        """
        salida = []
        for s in self.suspensiones:
            s_ini, s_fin = _fecha(s.get("inicio_incluido")), _fecha(s.get("fin_incluido"))
            if not s_ini and not s_fin:
                continue
            if not _intersecta(ini, fin, s_ini, s_fin):
                continue
            aut = (s.get("autoridad") or "").strip()
            if autoridad and aut and aut.upper() != str(autoridad).upper():
                continue
            fuente = self.fuentes.get(s.get("fuente_id")) or {}
            salida.append({
                "id": s.get("id_regla"),
                "autoridad": aut or None,
                "periodo_inicio": s_ini.isoformat() if s_ini else None,
                "periodo_fin": s_fin.isoformat() if s_fin else None,
                "rango_incompleto": not (s_ini and s_fin),
                "efecto": s.get("efecto"),
                "ambito_documentado": s.get("ambito"),
                "acuerdo": s.get("fuente_id"),
                "titulo": fuente.get("titulo") or s.get("notas"),
                "publicacion_dof": s.get("fecha_publicacion_DOF"),
                "url": s.get("url_oficial") or fuente.get("url_oficial"),
                "estado_verificacion": s.get("estado_verificacion"),
                # Lo más importante de la ficha.
                "aplicabilidad_al_expediente": NO_EVALUADA,
            })
        # Sin duplicar el mismo acuerdo, conservando todos los distintos.
        vistos, unicos = set(), []
        for a in salida:
            clave = (a["id"], a["periodo_inicio"], a["periodo_fin"])
            if clave in vistos:
                continue
            vistos.add(clave)
            unicos.append(a)
        return sorted(unicos, key=lambda a: (a["periodo_inicio"] or "", a["id"] or ""))

    # ── Coberturas ──────────────────────────────────────────────

    def _controles_abiertos(self, ini: date, fin: date, calendario: bool):
        out = []
        for c in self.controles:
            if (c.get("estado") or "").strip().upper() not in _ABIERTOS:
                continue
            reglas = (c.get("fuentes_o_reglas") or "")
            es_cal = any(reglas.startswith(p) or f";{p}" in reglas
                         for p in _PREFIJOS_CALENDARIO)
            if es_cal != calendario:
                continue
            c_ini, c_fin = _fecha(c.get("inicio_referencia")), _fecha(c.get("fin_referencia"))
            if not c_ini and not c_fin:
                continue          # control por expediente, no por periodo
            if not _intersecta(ini, fin, c_ini, c_fin):
                continue
            out.append({
                "id": c.get("id_control"),
                "tema": c.get("tema"),
                "estado": c.get("estado"),
                "prioridad": c.get("prioridad"),
                "periodo": [c_ini.isoformat() if c_ini else None,
                            c_fin.isoformat() if c_fin else None],
                "tratamiento": c.get("tratamiento_para_Norma"),
                "url": c.get("url_oficial"),
            })
        return out

    def cobertura_calendario_general(self, ini: date, fin: date) -> dict:
        """
        ¿Está confirmado el calendario ordinario para TODA la ventana?

        Sustituye el criterio anterior de `is_covered`, que comparaba la fecha
        contra el mínimo y el máximo del catálogo. COFECE lo señaló: *"Un hueco
        entre dos extremos cubiertos también es falta de cobertura."* Aquí el
        hueco se lee de los controles del propio catálogo —2018/principios de
        2019 y el anual 2022 siguen sin revalidar—, no de la ausencia de fechas,
        que sólo significa que ese día fue hábil.
        """
        faltantes = self._controles_abiertos(ini, fin, calendario=True)
        return {
            "completa": not faltantes and self.cargado,
            "catalogo_cargado": self.cargado,
            "periodos_sin_confirmar": faltantes,
        }

    def acuerdos_sin_periodo(self) -> list[dict]:
        """
        Acuerdos del catálogo cuyo periodo no se pudo normalizar.

        No se les puede calcular coincidencia —no se sabe dónde caen— y
        saltarlos en silencio sería afirmar una lista completa que no lo es.
        Salen como limitación de la cobertura de avisos, con su estado.

        Hoy son S11 y S12. COFECE lo anticipa: *"Un acuerdo relevante cuyo
        rango esté incompleto genera un aviso de esa limitación, sin inventar su
        fin ni prolongarlo indefinidamente."*
        """
        return [
            {
                "id": s.get("id_regla"),
                "autoridad": (s.get("autoridad") or "").strip() or None,
                "acuerdo": s.get("fuente_id"),
                "estado_verificacion": s.get("estado_verificacion"),
                "url": s.get("url_oficial"),
                "motivo": "su periodo no está normalizado: no se puede "
                          "determinar si coincide con esta ventana",
            }
            for s in self.suspensiones
            if not _fecha(s.get("inicio_incluido"))
            and not _fecha(s.get("fin_incluido"))
        ]

    def cobertura_avisos(self, ini: date, fin: date) -> dict:
        """
        ¿Está completa la revisión de suspensiones para esta ventana?

        Si no, la cifra de hábiles **sigue en pie**: lo que queda incompleto es
        la lista de acuerdos, no el conteo ordinario.
        """
        faltantes = self._controles_abiertos(ini, fin, calendario=False)
        sin_periodo = self.acuerdos_sin_periodo()
        return {
            "completa": not faltantes and not sin_periodo and self.cargado,
            "catalogo_cargado": self.cargado,
            "limitaciones": faltantes,
            "acuerdos_sin_periodo": sin_periodo,
        }
