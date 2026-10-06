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
import queue
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
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


class StdoutSummarySink(TraceSink):
    """
    Una línea JSON por traza, con el resumen plano, al log del servicio.

    En ECS la salida estándar ya llega a CloudWatch: sin bucket ni permisos
    nuevos, cada interacción queda consultable por `trace_id`, usuario o plan
    aunque el contenedor se reemplace en el siguiente despliegue. El resumen
    lleva la pregunta y la respuesta; la traza completa —con lo recuperado—
    va a S3 si está configurado.
    """

    PREFIJO = "TRAZA "

    def __init__(self):
        self._log = logging.getLogger("trazas")

    def write(self, trace: Trace) -> None:
        try:
            self._log.info(self.PREFIJO + json.dumps(
                trace.summary(), ensure_ascii=False, default=str))
        except Exception as e:
            logger.error(f"No se pudo registrar el resumen de {trace.trace_id}: {e}")


class S3Sink(TraceSink):
    """
    La traza completa a S3: `s3://<bucket>/<prefijo>/AAAA/MM/DD/<trace_id>.json`.

    Las trazas vivían en el disco del contenedor y se perdían con cada
    despliegue: cuando un usuario reporte una respuesta mala, no habría con
    qué revisarla. El bucket va en mx-central-1, la misma región del servicio,
    para que las consultas de los usuarios no salgan de México por esta vía.

    La subida corre en un hilo aparte: la respuesta al usuario no espera a
    S3, y un fallo de S3 se registra sin tumbar nada.
    """

    def __init__(self, bucket: str, prefijo: str = "trazas", region: str = "mx-central-1",
                 cliente=None):
        if cliente is None:
            import boto3  # sólo se necesita si S3 está configurado
            cliente = boto3.client("s3", region_name=region)
        self.cliente = cliente
        self.bucket = bucket
        self.prefijo = prefijo.strip("/")
        self._cola: "queue.Queue" = queue.Queue(maxsize=1000)
        self._hilo = threading.Thread(target=self._trabajar, daemon=True)
        self._hilo.start()

    def clave(self, trace: Trace) -> str:
        try:
            fecha = datetime.fromisoformat(str(trace.timestamp_utc).replace("Z", "+00:00"))
        except ValueError:
            fecha = datetime.now(timezone.utc)
        return f"{self.prefijo}/{fecha:%Y/%m/%d}/{trace.trace_id}.json"

    def write(self, trace: Trace) -> None:
        try:
            cuerpo = json.dumps(trace.model_dump(mode="json"), ensure_ascii=False)
            self._cola.put_nowait((self.clave(trace), cuerpo))
        except queue.Full:
            logger.error(f"Cola de trazas a S3 llena: se pierde {trace.trace_id}")
        except Exception as e:
            logger.error(f"No se pudo encolar {trace.trace_id} para S3: {e}")

    def _trabajar(self) -> None:
        while True:
            clave, cuerpo = self._cola.get()
            try:
                self.cliente.put_object(
                    Bucket=self.bucket, Key=clave, Body=cuerpo.encode("utf-8"),
                    ContentType="application/json")
            except Exception as e:
                logger.error(f"No se pudo subir {clave} a S3: {e}")
            finally:
                self._cola.task_done()

    def flush(self) -> None:
        self._cola.join()


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
    """
    Construye los destinos según `TRACE_SINKS` (por omisión `jsonl`; p. ej.
    `jsonl,stdout,s3`). Un destino que no se puede crear se omite y se avisa;
    nunca revienta el arranque.
    """
    if not getattr(settings, "tracing_enabled", False):
        logger.info("Trazabilidad desactivada (TRACING_ENABLED=false)")
        return NullSink()
    nombres = [n.strip().lower() for n in
               str(getattr(settings, "trace_sinks", "jsonl") or "jsonl").split(",")
               if n.strip()]
    sinks: list[TraceSink] = []
    for nombre in nombres:
        try:
            if nombre == "jsonl":
                s = JsonlFileSink(
                    base_dir=settings.traces_dir,
                    run_id=settings.run_id,
                    full_text=getattr(settings, "tracing_full_text", False),
                )
                logger.info(f"Trazas → {s.run_dir}")
            elif nombre == "stdout":
                s = StdoutSummarySink()
                logger.info("Trazas → resumen a la salida estándar (CloudWatch)")
            elif nombre == "s3":
                bucket = getattr(settings, "trace_s3_bucket", "")
                if not bucket:
                    logger.error("TRACE_SINKS incluye s3 pero falta TRACE_S3_BUCKET")
                    continue
                s = S3Sink(bucket, getattr(settings, "trace_s3_prefix", "trazas"),
                           getattr(settings, "trace_s3_region", "mx-central-1"))
                logger.info(f"Trazas → s3://{bucket}/{s.prefijo}/")
            else:
                logger.error(f"Destino de trazas desconocido: {nombre}")
                continue
            sinks.append(s)
        except Exception as e:
            logger.error(f"No se pudo inicializar el destino de trazas {nombre}: {e}")
    if not sinks:
        return NullSink()
    return sinks[0] if len(sinks) == 1 else MultiSink(*sinks)
