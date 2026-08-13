"""
Trazabilidad estructurada del agente Norma+.

Spec: NormaChat-Doc-Obs/docs/schema-trazas-v1.md
"""
from core.tracing.analysis import analyze_answer, count_format_markers
from core.tracing.collector import TraceCollector, new_trace_id
from core.tracing.heuristics import interpret
from core.tracing.manifest import RunManifestStore
from core.tracing.schema import (
    Answer, ContextBlock, Coverage, Decisions, Interpretation, Outcome,
    Request, RetrievalDoc, RetrievalStage, RunManifest, Step, Trace, Versions,
)
from core.tracing.sinks import JsonlFileSink, MultiSink, NullSink, TraceSink, build_sink
from core.tracing.versioning import build_versions, sha256_short

__all__ = [
    "Answer", "ContextBlock", "Coverage", "Decisions", "Interpretation",
    "JsonlFileSink", "MultiSink", "NullSink", "Outcome", "Request",
    "RetrievalDoc", "RetrievalStage", "RunManifest", "RunManifestStore",
    "Step", "Trace", "TraceCollector", "TraceSink", "Versions",
    "analyze_answer", "build_sink", "build_versions", "count_format_markers",
    "interpret", "new_trace_id", "sha256_short",
]
