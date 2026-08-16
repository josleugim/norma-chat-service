"""
Analizador temporal: enriquece expedientes con plazos en días hábiles,
filtra por duración, y calcula estadísticas.
"""
from datetime import date
from typing import Optional

from temporal.holidays import HolidayCalendar


# Campos de fecha por tipo de procedimiento, según el modelo de datos que
# confirmó COFECE el 14-ago-2026. La distinción importa: en VCN la fecha de
# notificación NO APLICA por diseño —el equivalente es el acuerdo de inicio—,
# así que buscarla ahí no era un hueco de datos sino un error de modelado.
CAMPOS_FECHA = {
    "startAgreementDate": "Fecha de emisión del acuerdo de inicio (VCN)",
    "notificationDate": "Fecha de notificación (CNT)",
    "basicInfoRequestDate": "Fecha de requerimiento de información básica (CNT)",
    "admissionDate": "Fecha de admisión a trámite (CNT)",
    "additionalInfoRequestDate": "Fecha de requerimiento de información adicional (CNT)",
    "resolutionDate": "Fecha de resolución",
    "ResolutionIssueDate": "Fecha de emisión de la resolución",
}

# Para "¿cuánto tardó la autoridad en resolver?" sin más precisión.
DEFAULT_POR_PREFIJO = {
    "VCN": ("startAgreementDate", "resolutionDate"),
    "IO": ("startAgreementDate", "resolutionDate"),
    "CNT": ("notificationDate", "resolutionDate"),
}
DEFAULT_GENERICO = ("notificationDate", "resolutionDate")


def campos_por_defecto(case_link: str | None) -> tuple[str, str]:
    """Par de fechas por defecto según el tipo de expediente."""
    prefijo = (case_link or "").split("-")[0].upper()
    return DEFAULT_POR_PREFIJO.get(prefijo, DEFAULT_GENERICO)


class TemporalAnalyzer:

    def __init__(self, calendar: HolidayCalendar):
        self.cal = calendar

    def compute_between_fields(
        self,
        records: list[dict],
        campo_inicio: str | None = None,
        campo_fin: str = "resolutionDate",
    ) -> list[dict]:
        """
        Calculadora general entre cualquier par de campos de fecha.

        Antes la herramienta estaba cableada a notificación → resolución, que
        no existe en VCN. Ahora el agente elige el par según la pregunta, y si
        no lo especifica se usa el default del tipo de expediente.

        Si falta alguna de las dos fechas **no se estima**: se devuelve el caso
        marcado como no calculable, con el nombre del campo que faltó.
        """
        salida = []
        for rec in records:
            case_link = rec.get("caseLink") or rec.get("id_expediente") or ""
            inicio = campo_inicio or campos_por_defecto(case_link)[0]
            autoridad = rec.get("authority") or rec.get("autoridad")

            d_ini = self._parse_date(rec.get(inicio))
            d_fin = self._parse_date(rec.get(campo_fin))

            entrada = {
                "case_link": case_link,
                "authority": autoridad,
                "campo_inicio": inicio,
                "campo_fin": campo_fin,
                "fecha_inicio": d_ini.isoformat() if d_ini else None,
                "fecha_fin": d_fin.isoformat() if d_fin else None,
            }

            if not d_ini or not d_fin:
                faltantes = [
                    c for c, d in ((inicio, d_ini), (campo_fin, d_fin)) if not d
                ]
                entrada.update({
                    "calculable": False,
                    "campos_faltantes": faltantes,
                    "dias_habiles": None,
                    "dias_naturales": None,
                })
            elif d_fin < d_ini:
                # Fecha final anterior a la inicial: el dato está mal en la
                # base, no es un plazo de cero días. Sin esta distinción,
                # 114 expedientes con fechas invertidas se colaban como
                # "resuelto el mismo día" y ganaban cualquier mínimo.
                entrada.update({
                    "calculable": False,
                    "anomalia": "fecha_fin_anterior_a_inicio",
                    "dias_habiles": None,
                    "dias_naturales": None,
                    "nota": (
                        f"{campo_fin} ({d_fin.isoformat()}) es anterior a "
                        f"{inicio} ({d_ini.isoformat()}). Dato inconsistente "
                        f"en la base; no se calcula el plazo."
                    ),
                })
            else:
                cubierto = (
                    self.cal.is_covered(d_ini, autoridad)
                    and self.cal.is_covered(d_fin, autoridad)
                )
                entrada.update({
                    "calculable": True,
                    "dias_habiles": self.cal.business_days_between(
                        d_ini, d_fin, autoridad
                    ),
                    "dias_naturales": (d_fin - d_ini).days,
                    "fuera_de_cobertura": not cubierto,
                })
            salida.append(entrada)
        return salida

    def enrich_with_plazos(
        self, records: list[dict], institucion: str | None = None,
    ) -> list[dict]:
        """
        Agrega campos calculados a cada expediente:
        - dias_habiles_notif_resol
        - dias_calendario_notif_resol
        - dias_habiles_admis_resol

        Soporta ambos formatos de campos:
        - API real: notificationDate, resolutionDate, admissionDate, authority (DD-MM-YYYY)
        - Schema interno: fecha_notificacion, fecha_resolucion, fecha_admision (ISO)
        """
        enriched = []
        for rec in records:
            entry = dict(rec)

            # Inferir institución (soportar ambos nombres de campo)
            inst = institucion or rec.get("authority") or rec.get("autoridad")

            # Extraer fechas (soportar ambos formatos)
            f_notif = self._parse_date(
                rec.get("notificationDate") or rec.get("fecha_notificacion")
            )
            f_resol = self._parse_date(
                rec.get("resolutionDate") or rec.get("fecha_resolucion")
            )
            f_admis = self._parse_date(
                rec.get("admissionDate") or rec.get("fecha_admision")
            )

            if f_notif and f_resol:
                entry["dias_habiles_notif_resol"] = \
                    self.cal.business_days_between(f_notif, f_resol, inst)
                entry["dias_calendario_notif_resol"] = (f_resol - f_notif).days

            if f_admis and f_resol:
                entry["dias_habiles_admis_resol"] = \
                    self.cal.business_days_between(f_admis, f_resol, inst)

            enriched.append(entry)
        return enriched

    def filter_by_plazo(
        self,
        records: list[dict],
        max_dias_habiles: int | None = None,
        min_dias_habiles: int | None = None,
        plazo_field: str = "dias_habiles_notif_resol",
    ) -> list[dict]:
        """Filtra expedientes por duración en días hábiles."""
        filtered = []
        for rec in records:
            dias = rec.get(plazo_field)
            if dias is None:
                continue
            if max_dias_habiles is not None and dias > max_dias_habiles:
                continue
            if min_dias_habiles is not None and dias < min_dias_habiles:
                continue
            filtered.append(rec)
        return filtered

    def compute_stats(
        self,
        records: list[dict],
        plazo_field: str = "dias_habiles_notif_resol",
    ) -> dict:
        """
        Estadísticas sobre plazos: promedio, mediana, min, max, percentiles.
        """
        valores = [
            r[plazo_field] for r in records
            if r.get(plazo_field) is not None
        ]
        if not valores:
            return {"count": 0}

        valores.sort()
        n = len(valores)
        return {
            "count": n,
            "promedio": round(sum(valores) / n, 1),
            "mediana": valores[n // 2],
            "minimo": valores[0],
            "maximo": valores[-1],
            "p25": valores[int(n * 0.25)],
            "p75": valores[int(n * 0.75)],
        }

    def _parse_date(self, val) -> Optional[date]:
        if val is None or str(val).strip() in ("", "nan", "None", "NaT", "null"):
            return None
        if isinstance(val, date):
            return val
        s = str(val).strip()
        # Try DD-MM-YYYY (formato de la API de José Miguel)
        parts = s.split("-")
        if len(parts) == 3 and len(parts[0]) == 2 and len(parts[2]) == 4:
            try:
                return date(int(parts[2]), int(parts[1]), int(parts[0]))
            except (ValueError, TypeError):
                pass
        # Try YYYY-MM-DD (ISO 8601)
        try:
            return date.fromisoformat(s[:10])
        except (ValueError, TypeError):
            return None
