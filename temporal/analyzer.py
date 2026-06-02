"""
Analizador temporal: enriquece expedientes con plazos en días hábiles,
filtra por duración, y calcula estadísticas.
"""
from datetime import date
from typing import Optional

from temporal.holidays import HolidayCalendar


class TemporalAnalyzer:

    def __init__(self, calendar: HolidayCalendar):
        self.cal = calendar

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
