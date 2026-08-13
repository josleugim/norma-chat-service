"""
Manifiesto de corrida.

Convierte "congelar el baseline" en algo detectable en vez de una buena
intención: al arrancar la corrida se fijan las versiones; cada traza se compara
contra ellas y marca baseline_drift. Si alguien despliega a media corrida, o el
índice se reconstruye, lo sabemos en el momento y no tres semanas después al no
poder explicar un resultado.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.tracing.schema import RunManifest, Versions

logger = logging.getLogger(__name__)


class RunManifestStore:

    def __init__(self, base_dir: str, run_id: str):
        self.path = Path(base_dir) / run_id / "run_manifest.json"
        self.run_id = run_id
        self._lock = threading.Lock()
        self._manifest: Optional[RunManifest] = None

    def load_or_create(
        self, versions: Versions, label: str = "", question_set: Optional[str] = None
    ) -> RunManifest:
        """
        Si el manifiesto existe, lo carga (la corrida ya estaba en marcha).
        Si no, congela las versiones actuales como baseline.
        """
        with self._lock:
            if self._manifest is not None:
                return self._manifest
            if self.path.exists():
                try:
                    self._manifest = RunManifest.model_validate_json(
                        self.path.read_text(encoding="utf-8")
                    )
                    logger.info(
                        f"Corrida {self.run_id} retomada; baseline congelado el "
                        f"{self._manifest.started_at.isoformat()}"
                    )
                    return self._manifest
                except Exception as e:
                    logger.error(f"Manifiesto ilegible en {self.path}: {e}")

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._manifest = RunManifest(
                run_id=self.run_id,
                label=label or self.run_id,
                started_at=datetime.now(timezone.utc),
                question_set=question_set,
                frozen_versions=versions.fingerprint(),
            )
            self._persist()
            logger.info(f"Baseline congelado para la corrida {self.run_id}")
            return self._manifest

    @property
    def frozen_versions(self) -> dict:
        return self._manifest.frozen_versions if self._manifest else {}

    def record_trace(
        self, trace_id: str, drift: list[dict], model: str | None = None
    ) -> None:
        if self._manifest is None:
            return
        with self._lock:
            self._manifest.trace_count += 1
            if model and model not in self._manifest.models_observed:
                self._manifest.models_observed.append(model)
            if drift:
                self._manifest.drift_detected.append({
                    "trace_id": trace_id,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "fields": drift,
                })
            self._persist()

    def finish(self) -> None:
        if self._manifest is None:
            return
        with self._lock:
            self._manifest.finished_at = datetime.now(timezone.utc)
            self._persist()

    def _persist(self) -> None:
        try:
            self.path.write_text(
                json.dumps(
                    self._manifest.model_dump(mode="json"),
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"No se pudo escribir el manifiesto: {e}")
