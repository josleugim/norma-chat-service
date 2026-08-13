"""
Destinos de escritura de trazas.

El destino definitivo lo decide el equipo (ver docs/solicitud-jose-miguel.md §2),
así que todo pasa por la interfaz `TraceSink`. Cambiar de JSONL a Postgres o a
una plataforma de observabilidad no debe tocar el agente.

Layout del sink de archivos:

    traces/
    └── <run_id>/
        ├── run_manifest.json
        ├── traces/<trace_id>.json    traza completa, con retrieval crudo
        └── run.jsonl                 una línea por traza, resumen plano
"""
import json
import logging
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from core.tracing.schema import Trace

logger = logging.getLogger(__name__)


class TraceSink(ABC):
    @abstractmethod
    def write(self, trace: Trace) -> None:
        ...

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.flush()


class NullSink(TraceSink):
    """Sink por defecto cuando la trazabilidad está apagada."""

    def write(self, trace: Trace) -> None:
        return


class JsonlFileSink(TraceSink):
    """
    Escribe la traza completa a un JSON por archivo y un resumen a run.jsonl.

    La escritura es síncrona pero está protegida por un lock y se invoca desde
    un `finally`, para que una traza se conserve también cuando la petición
    falla o el cliente corta la conexión — que son los casos más valiosos.
    """

    def __init__(self, base_dir: str, run_id: str, full_text: bool = False):
        self.run_dir = Path(base_dir) / run_id
        self.traces_dir = self.run_dir / "traces"
        self.jsonl_path = self.run_dir / "run.jsonl"
        self.full_text = full_text
        self._lock = threading.Lock()
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def write(self, trace: Trace) -> None:
        try:
            payload = trace.model_dump(mode="json")
            with self._lock:
                path = self.traces_dir / f"{trace.trace_id}.json"
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                with self.jsonl_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(trace.summary(), ensure_ascii=False) + "\n")
        except Exception as e:
            # Nunca dejar que un fallo de trazabilidad rompa una respuesta.
            logger.error(f"No se pudo escribir la traza {trace.trace_id}: {e}")


class MultiSink(TraceSink):
    def __init__(self, *sinks: TraceSink):
        self.sinks = [s for s in sinks if s is not None]

    def write(self, trace: Trace) -> None:
        for sink in self.sinks:
            sink.write(trace)

    def flush(self) -> None:
        for sink in self.sinks:
            sink.flush()


def build_sink(settings) -> TraceSink:
    """Construye el sink según configuración. Falla a NullSink, nunca revienta."""
    if not getattr(settings, "tracing_enabled", False):
        logger.info("Trazabilidad desactivada (TRACING_ENABLED=false)")
        return NullSink()
    try:
        sink = JsonlFileSink(
            base_dir=settings.traces_dir,
            run_id=settings.run_id,
            full_text=getattr(settings, "tracing_full_text", False),
        )
        logger.info(f"Trazas → {sink.run_dir}")
        return sink
    except Exception as e:
        logger.error(f"No se pudo inicializar el sink de trazas: {e}")
        return NullSink()
