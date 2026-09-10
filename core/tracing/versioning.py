"""
Cálculo de las versiones que hacen reproducible una corrida.

Principio: los hashes se CALCULAN, no se etiquetan a mano. Si el prompt cambia
una coma, el hash cambia y la corrida deja de ser comparable con la anterior.
Lo que no podamos versionar se declara en `Versions.unknown` — nunca null
silencioso.
"""
import hashlib
import json
import logging
import os
from typing import Any, Optional

from core.tracing.schema import Versions

logger = logging.getLogger(__name__)

AGENT_SEMVER = "1.0.0"
PROMPT_ID = "agent_system"
PROMPT_SEMVER = "v1"


def sha256_short(value: str | bytes, length: int = 12) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()[:length]


def sha256_full(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def prompt_hash() -> str:
    from prompts.system import AGENT_SYSTEM_PROMPT
    return sha256_short(AGENT_SYSTEM_PROMPT)


def tools_hash() -> str:
    from agent.tools import TOOLS
    return sha256_short(json.dumps(TOOLS, sort_keys=True, ensure_ascii=False))


def build_versions(
    settings,
    provider: str,
    model: str,
    calendar=None,
) -> Versions:
    """
    Arma el bloque `versions` de una traza.

    `calendar` es el HolidayCalendar ya cargado; de él salen el hash del XLSX
    y la cobertura por institución, que es lo que permite saber si un plazo se
    calculó dentro o fuera del rango del catálogo.
    """
    unknown: list[str] = []

    holidays_sha: Optional[str] = None
    holidays_coverage: dict[str, list[str]] = {}
    if calendar is not None:
        holidays_sha = getattr(calendar, "source_sha256", None)
        try:
            holidays_coverage = calendar.coverage_ranges()
        except AttributeError:
            holidays_coverage = {}
    if not holidays_sha:
        unknown.append("holidays_sha256")

    # Pendientes del lado de la API de datos. Se declaran explícitamente
    # para dejar constancia de que la reproducibilidad es parcial.
    index_version = os.getenv("SEARCH_INDEX_VERSION") or None
    index_snapshot_at = os.getenv("SEARCH_INDEX_SNAPSHOT_AT") or None
    embeddings_model = os.getenv("SEARCH_EMBEDDINGS_MODEL") or None
    for name, value in (
        ("index_version", index_version),
        ("index_snapshot_at", index_snapshot_at),
        ("embeddings_model", embeddings_model),
    ):
        if not value:
            unknown.append(name)

    return Versions(
        agent_git_sha=os.getenv("GIT_SHA", "unknown"),
        agent_semver=AGENT_SEMVER,
        prompt_id=PROMPT_ID,
        prompt_semver=PROMPT_SEMVER,
        prompt_sha256=prompt_hash(),
        tools_sha256=tools_hash(),
        provider=provider,
        model=model,
        model_params={
            "temperature": getattr(settings, "agent_default_temperature", None),
            "max_tokens": getattr(settings, "agent_max_tokens", None),
            "max_tool_calls": getattr(settings, "agent_max_tool_calls", None),
        },
        retrieval_defaults={
            "criterios_top_k": getattr(settings, "criterios_default_top_k", None),
            "criterios_min_score": getattr(settings, "criterios_min_score", None),
            "criterios_max_distance": 0.7,  # default de CriteriosSearchClient.search
            "estadistica_limit": getattr(settings, "estadistica_default_limit", None),
        },
        search_api_base_url=getattr(settings, "search_api_base_url", ""),
        index_version=index_version,
        index_snapshot_at=index_snapshot_at,
        embeddings_model=embeddings_model,
        holidays_sha256=holidays_sha,
        holidays_coverage=holidays_coverage,
        unknown=unknown,
    )


def diff_fingerprints(frozen: dict[str, Any], current: dict[str, Any]) -> list[dict]:
    """Diferencias entre el manifiesto congelado y las versiones de esta traza."""
    drift = []
    for key in sorted(set(frozen) | set(current)):
        before, after = frozen.get(key), current.get(key)
        if before != after:
            drift.append({"field": key, "frozen": before, "current": after})
    return drift
