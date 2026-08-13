"""
Catálogo de días inhábiles para cálculo de plazos de competencia
económica en México (COFECE / CNA).

Carga desde un archivo XLSX con las columnas:
  Año, Fecha, Día semana, Institución, Tipo,
  Fuente / publicación, Fecha DOF, Acuerdo / fundamento, etc.

El archivo ya incluye fines de semana como registros, por lo que
is_business_day() solo necesita verificar si la fecha está en el set.
"""
import hashlib
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class HolidayCalendar:

    def __init__(self, holidays_path: str = "data/dias_inhabiles.xlsx"):
        # Todas las fechas inhábiles (ambas instituciones)
        self.all_holidays: set[date] = set()
        # Inhábiles por institución
        self.cofece_holidays: set[date] = set()
        self.cna_holidays: set[date] = set()
        # Metadata por fecha: {date: [{tipo, institucion, fundamento}]}
        self.metadata: dict[date, list[dict]] = {}
        # Trazabilidad: hash del catálogo cargado, para versionar la corrida
        self.source_sha256: str | None = None

        self._load(holidays_path)

    def _load(self, path: str):
        file_path = Path(path)
        if file_path.exists():
            try:
                self.source_sha256 = hashlib.sha256(
                    file_path.read_bytes()
                ).hexdigest()[:12]
            except OSError as e:
                logger.warning(f"No se pudo hashear {path}: {e}")
        if not file_path.exists():
            logger.warning(
                f"Archivo de días inhábiles no encontrado: {path}. "
                "Los cálculos usarán solo fines de semana."
            )
            return

        try:
            import pandas as pd
            df = pd.read_excel(file_path)
        except ImportError:
            logger.error(
                "pandas y openpyxl son necesarios para leer el XLSX. "
                "Instala con: pip install pandas openpyxl"
            )
            return
        except Exception as e:
            logger.error(f"Error leyendo {path}: {e}")
            return

        count = 0
        for _, row in df.iterrows():
            try:
                fecha_val = row["Fecha"]
                if hasattr(fecha_val, "date"):
                    d = fecha_val.date()
                elif isinstance(fecha_val, date):
                    d = fecha_val
                else:
                    d = date.fromisoformat(str(fecha_val)[:10])

                institucion = str(row.get("Institución", "")).strip().upper()
                tipo = str(row.get("Tipo", "")).strip()
                fundamento = str(row.get("Acuerdo / fundamento", ""))

                self.all_holidays.add(d)

                if institucion == "COFECE":
                    self.cofece_holidays.add(d)
                elif institucion == "CNA":
                    self.cna_holidays.add(d)

                # Guardar metadata
                if d not in self.metadata:
                    self.metadata[d] = []
                self.metadata[d].append({
                    "institucion": institucion,
                    "tipo": tipo,
                    "fundamento": fundamento[:200],
                })

                count += 1
            except (ValueError, KeyError) as e:
                logger.warning(f"Fila inválida en XLSX: {e}")

        logger.info(
            f"Cargados {count} días inhábiles desde {path} "
            f"(COFECE: {len(self.cofece_holidays)}, CNA: {len(self.cna_holidays)}, "
            f"rango: {min(self.all_holidays)} a {max(self.all_holidays)})"
        )

    def is_business_day(
        self, d: date, institucion: str | None = None
    ) -> bool:
        """
        True si es día hábil.

        Args:
            d: Fecha a verificar
            institucion: "COFECE" o "CNA" para calendario específico.
                         None usa el calendario combinado (ambas).
        """
        if institucion:
            inst = institucion.strip().upper()
            if inst == "COFECE":
                return d not in self.cofece_holidays
            elif inst == "CNA":
                return d not in self.cna_holidays

        # Calendario combinado: si está en el set de CUALQUIER
        # institución, es inhábil. Para fechas fuera del rango del
        # archivo, fallback a fines de semana.
        if d in self.all_holidays:
            return False

        # Fallback para fechas fuera del rango del catálogo
        if self.all_holidays and (d < min(self.all_holidays) or d > max(self.all_holidays)):
            return d.weekday() < 5  # solo fines de semana

        return True

    def business_days_between(
        self, start: date, end: date,
        institucion: str | None = None,
    ) -> int:
        """
        Cuenta días hábiles entre dos fechas.
        Excluye ambos extremos (convención de plazos procesales mexicanos).
        """
        if start >= end:
            return 0
        count = 0
        current = start + timedelta(days=1)
        while current < end:
            if self.is_business_day(current, institucion):
                count += 1
            current += timedelta(days=1)
        return count

    def add_business_days(
        self, start: date, days: int,
        institucion: str | None = None,
    ) -> date:
        """Suma N días hábiles a partir de una fecha."""
        current = start
        added = 0
        while added < days:
            current += timedelta(days=1)
            if self.is_business_day(current, institucion):
                added += 1
        return current

    def get_holiday_info(self, d: date) -> list[dict] | None:
        """Retorna metadata del día inhábil, o None si es hábil."""
        return self.metadata.get(d)

    # ── Cobertura del catálogo (para trazabilidad) ──────────────

    def _holidays_for(self, institucion: str | None) -> set[date]:
        if institucion:
            inst = institucion.strip().upper()
            if inst == "COFECE":
                return self.cofece_holidays
            if inst == "CNA":
                return self.cna_holidays
        return self.all_holidays

    def coverage_ranges(self) -> dict[str, list[str]]:
        """
        Rango de fechas cubierto por el catálogo, por institución.

        Importa porque la cobertura es desigual (COFECE y CNA no cubren los
        mismos años) y fuera de rango el cálculo de días hábiles degrada.
        """
        ranges: dict[str, list[str]] = {}
        for name, dates in (
            ("COFECE", self.cofece_holidays),
            ("CNA", self.cna_holidays),
            ("ALL", self.all_holidays),
        ):
            if dates:
                ranges[name] = [min(dates).isoformat(), max(dates).isoformat()]
        return ranges

    def is_covered(self, d: date, institucion: str | None = None) -> bool:
        """
        True si la fecha cae dentro del rango del catálogo para esa institución.

        Fuera de rango, `is_business_day` no tiene datos y los fines de semana
        pueden contarse como hábiles: el plazo resultante no es confiable.
        """
        dates = self._holidays_for(institucion)
        if not dates:
            return False
        return min(dates) <= d <= max(dates)
