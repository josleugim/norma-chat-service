"""
ZIP autocontenido de artifacts por corrida.

COFECE, adjudicación de v1.3, punto 6: cada corrida formal debe generar un ZIP
que permita revisar juntos "resultados + traces + prompts + tools + código
exacto de la corrida", siguiendo la misma lógica de los pipelines de
extracción.

La idea de fondo es que una traza sin el código que la produjo solo explica a
medias: para decir por qué el agente truncó una búsqueda hay que poder ver el
truncado. Así el diagnóstico deja de depender de que alguien tenga el repo en
el estado correcto.

Estructura:

    <run_id>/
    ├── manifest.json          versiones, censo del acervo, drift, commit
    ├── README.md              qué es cada cosa y cómo leerla
    ├── results/               resumen.jsonl + tabla.xlsx/csv + evaluacion.json
    ├── traces/                una traza completa por consulta
    ├── prompts/               los prompts efectivamente usados
    ├── agent_code/            código de routing, retrieval, contexto, citas
    ├── tools/                 definición y ejecución de las herramientas
    ├── retrieval_config/      top-k, thresholds, filtros, truncado
    └── errors_warnings/       errores, anomalías y drift, en texto legible

NUNCA se incluyen secretos: hay una lista explícita de exclusiones y un barrido
final que detecta claves aunque se cuelen por otra vía.
"""
import json
import logging
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Código relevante para explicar una respuesta, por área.
CODE_MAP = {
    "agent_code/routing_y_suficiencia.py": "core/sufficiency.py",
    "agent_code/agente.py": "agent/agent.py",
    "agent_code/estado_del_turno.py": "agent/turn_state.py",
    "agent_code/citas_registro.py": "core/citations.py",
    "agent_code/citas_referencias.py": "core/citation_builder.py",
    "agent_code/contexto_evidencia.py": "core/evidence_cache.py",
    "agent_code/agregaciones.py": "core/aggregation.py",
    "agent_code/interpretacion.py": "core/tracing/heuristics.py",
    "agent_code/diagnostico_etapas.py": "core/tracing/diagnose.py",
    "tools/definicion_herramientas.py": "agent/tools.py",
    "tools/calculo_plazos.py": "temporal/analyzer.py",
    "tools/calendario_dias_inhabiles.py": "temporal/holidays.py",
    "tools/cliente_expedientes.py": "retrieval/estadistica_client.py",
    "tools/cliente_criterios.py": "retrieval/criterios_client.py",
    "prompts/system_prompt.py": "prompts/system.py",
}

# Nunca deben entrar al paquete.
EXCLUIR = re.compile(r"(^|/)\.env|secret|credential|\.pem$|\.key$|token", re.I)

# Barrido final: patrones de credenciales reales.
PATRONES_SECRETO = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b\d+\.[0-9a-f]{64}\b"),   # SEARCH_API_KEY: {id}.{hex}
]


def build_artifacts(
    run_dir: Path, repo_root: Path | None = None, question_set: Path | None = None
) -> Path:
    """
    Arma el ZIP completo de una corrida. Devuelve la ruta del archivo.
    """
    run_dir = Path(run_dir)
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2])
    stage = run_dir / "_artifacts"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    manifest = _leer_json(run_dir / "run_manifest.json")
    filas = _leer_jsonl(run_dir / "run.jsonl")

    _manifest(stage, manifest, repo_root, filas)
    _results(stage, run_dir, filas, question_set)
    _traces(stage, run_dir)
    _codigo(stage, repo_root)
    _retrieval_config(stage, repo_root, manifest)
    _errores(stage, run_dir, manifest, filas)
    _readme(stage, manifest, filas)

    zip_path = run_dir / f"{run_dir.name}_artifacts.zip"
    fugas = _empaquetar(stage, zip_path, run_dir.name)
    shutil.rmtree(stage, ignore_errors=True)

    if fugas:
        # Preferible romper el paquete a publicar una credencial.
        zip_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Se detectaron posibles secretos en {fugas}; no se generó el ZIP."
        )
    return zip_path


# ── Secciones ───────────────────────────────────────────────

def _manifest(stage: Path, manifest: dict, repo: Path, filas: list[dict]) -> None:
    salida = dict(manifest)
    salida["generated_at"] = datetime.now(timezone.utc).isoformat()
    salida["git"] = _git_info(repo)
    salida["question_count_ejecutadas"] = len(filas)
    salida["schema_version"] = "1.0"
    _escribir(stage / "manifest.json", json.dumps(salida, ensure_ascii=False, indent=2))


def _results(
    stage: Path, run_dir: Path, filas: list[dict], question_set: Path | None
) -> None:
    d = stage / "results"
    d.mkdir(exist_ok=True)
    _escribir(
        d / "resumen.jsonl",
        "\n".join(json.dumps(f, ensure_ascii=False) for f in filas),
    )
    for nombre in ("export.xlsx", "export.csv"):
        origen = run_dir / nombre
        if origen.exists():
            shutil.copy2(origen, d / nombre.replace("export", "tabla"))
    if question_set and Path(question_set).exists():
        shutil.copy2(question_set, d / "bateria_de_preguntas.json")

    # Evaluación automática por pregunta, en formato legible.
    evaluacion = [
        {
            "id": f.get("question_set_id"),
            "pregunta": f.get("query"),
            "etapa_con_anomalia": f.get("diagnosis_stage"),
            "motivo": f.get("diagnosis_reason"),
            "tipo_consulta": f.get("query_type"),
            "cobertura_truncada": f.get("coverage_truncated"),
            "exhaustiva_pero_truncada": f.get("exhaustive_but_truncated"),
            "scope_mismatch": f.get("scope_mismatch"),
            "citas_sin_resolver": f.get("citations_unresolved"),
            "abstuvo": f.get("abstained"),
            "trace_id": f.get("trace_id"),
        }
        for f in filas
    ]
    _escribir(
        d / "evaluacion_automatica.json",
        json.dumps(evaluacion, ensure_ascii=False, indent=2),
    )


def _traces(stage: Path, run_dir: Path) -> None:
    origen = run_dir / "traces"
    if origen.exists():
        shutil.copytree(origen, stage / "traces")


def _codigo(stage: Path, repo: Path) -> None:
    for destino, origen in CODE_MAP.items():
        src = repo / origen
        if not src.exists():
            continue
        dst = stage / destino
        dst.parent.mkdir(parents=True, exist_ok=True)
        cabecera = (
            f"# Archivo del repo: {origen}\n"
            f"# Copiado tal cual para esta corrida.\n\n"
        )
        _escribir(dst, cabecera + src.read_text(encoding="utf-8"))

    # El prompt del sistema, ya resuelto en texto plano.
    try:
        import sys
        sys.path.insert(0, str(repo))
        from prompts.system import AGENT_SYSTEM_PROMPT
        _escribir(stage / "prompts" / "system_prompt.txt", AGENT_SYSTEM_PROMPT)
    except Exception as e:
        logger.warning(f"No se pudo volcar el prompt en texto: {e}")


def _retrieval_config(stage: Path, repo: Path, manifest: dict) -> None:
    d = stage / "retrieval_config"
    d.mkdir(exist_ok=True)
    versiones = manifest.get("frozen_versions", {})
    config = {
        "retrieval_defaults": versiones.get("retrieval_defaults", {}),
        "model_params": versiones.get("model_params", {}),
        "truncado_criterios_chars": 700,
        "max_tool_calls": versiones.get("model_params", {}).get("max_tool_calls"),
        "search_api_base_url": versiones.get("search_api_base_url"),
        "index_version": versiones.get("index_version"),
        "embeddings_model": versiones.get("embeddings_model"),
        "nota_reranker": (
            "No hay reranker en el pipeline. Las etapas registradas en cada "
            "traza son: candidates (lo que devolvió la API), after_ranking "
            "(tras filtro de distancia y orden por score) e in_context (lo que "
            "realmente entró al prompt, ya truncado)."
        ),
        "nota_filtros_api": (
            "La API de casos une sus filtros con OR, no los intersecta "
            "(verificado: CFC=2793, VCN=36, ambos=2829). Por eso el agente "
            "manda un solo filtro y acota localmente."
        ),
    }
    _escribir(d / "parametros.json", json.dumps(config, ensure_ascii=False, indent=2))


def _errores(stage: Path, run_dir: Path, manifest: dict, filas: list[dict]) -> None:
    d = stage / "errors_warnings"
    d.mkdir(exist_ok=True)
    lineas = ["# Errores, anomalías y advertencias de la corrida", ""]

    drift = manifest.get("drift_detected") or []
    lineas.append(
        f"## Baseline drift\n\n"
        + (
            "Ninguno: el entorno no cambió durante la corrida.\n"
            if not drift
            else f"⚠️ {len(drift)} trazas con drift. La comparación puede no ser "
            f"válida:\n\n```json\n{json.dumps(drift, ensure_ascii=False, indent=2)}\n```\n"
        )
    )

    con_error = [f for f in filas if f.get("errors")]
    lineas.append(
        f"## Turnos con error\n\n"
        + (
            "Ninguno.\n" if not con_error
            else "\n".join(f"- {f['question_set_id']}: {f['trace_id']}" for f in con_error) + "\n"
        )
    )

    # Anomalías de datos detectadas dentro de las trazas.
    anomalias: dict[str, set] = {}
    for path in sorted((run_dir / "traces").glob("*.json")):
        try:
            traza = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        qid = traza.get("request", {}).get("question_set_id", "?")
        for paso in traza.get("steps", []):
            comp = paso.get("computation") or {}
            for caso in comp.get("per_case", []):
                if caso.get("anomalia"):
                    anomalias.setdefault(caso["anomalia"], set()).add(
                        caso.get("case_link") or qid
                    )
                if caso.get("out_of_coverage"):
                    anomalias.setdefault("fecha_fuera_del_calendario", set()).add(
                        caso.get("case_link") or qid
                    )

    censo = manifest.get("corpus_census", {})
    if censo.get("errors"):
        lineas.append(
            "## ⚠️ Censo del acervo incompleto\n\n"
            f"El conteo de expedientes por tipo falló para: "
            f"{', '.join(e.split(':')[0] for e in censo['errors'])}. "
            f"Los totales de este manifiesto NO están completos, así que la "
            f"comparación de cobertura con otra corrida puede ser engañosa.\n"
        )

    lineas.append("## Anomalías de datos\n")
    if not anomalias:
        lineas.append("Ninguna detectada.\n")
    else:
        for nombre, casos in sorted(anomalias.items()):
            ejemplos = ", ".join(sorted(c for c in casos if c)[:10])
            lineas.append(f"- **{nombre}**: {len(casos)} casos. Ejemplos: {ejemplos}\n")

    truncadas = [f for f in filas if f.get("exhaustive_but_truncated")]
    lineas.append("## Consultas exhaustivas resueltas sobre universo truncado\n")
    lineas.append(
        "Ninguna.\n" if not truncadas
        else "\n".join(f"- {f['question_set_id']}: {f['query'][:90]}" for f in truncadas) + "\n"
    )

    _escribir(d / "resumen.md", "\n".join(lineas))


def _readme(stage: Path, manifest: dict, filas: list[dict]) -> None:
    versiones = manifest.get("frozen_versions", {})
    censo = manifest.get("corpus_census", {})
    contenido = f"""# Corrida {manifest.get('run_id', '?')}

{manifest.get('label', '')} · {len(filas)} preguntas · generado el {datetime.now(timezone.utc).date().isoformat()}

Paquete autocontenido: resultados, trazas, prompts, herramientas y el código
exacto con el que corrió. La idea es poder diagnosticar sin tener que
reconstruir el estado del repo.

## Qué hay aquí

| Carpeta | Qué contiene |
|---|---|
| `manifest.json` | Versiones congeladas, commit, censo del acervo, drift |
| `results/` | Resumen por pregunta, tabla para analizar y evaluación automática |
| `traces/` | Una traza completa por consulta, con el retrieval crudo |
| `prompts/` | El prompt del sistema tal como se usó |
| `agent_code/` | Routing, suficiencia, agente, citas, agregaciones |
| `tools/` | Definición de herramientas, cálculo de plazos, clientes de datos |
| `retrieval_config/` | top-k, thresholds, truncado y notas del pipeline |
| `errors_warnings/` | Errores, anomalías de datos y drift, en texto legible |

## Baseline de esta corrida

- **Commit del agente:** `{versiones.get('agent_git_sha', '?')}`
- **Prompt:** `{versiones.get('prompt_sha256', '?')}`
- **Herramientas:** `{versiones.get('tools_sha256', '?')}`
- **Calendario de días inhábiles:** `{versiones.get('holidays_sha256', '?')}`
- **Acervo al arrancar:** {censo.get('total', '?')} expedientes {censo.get('by_prefix', {})}

## Por dónde empezar

1. `results/tabla.xlsx` — una fila por pregunta. Las columnas `diagnosis_stage`
   y `diagnosis_reason` dicen en qué etapa se rompió el flujo.
2. Para una respuesta concreta, abre `traces/<trace_id>.json`: trae la
   interpretación, cada búsqueda con sus filtros, las tres etapas de retrieval,
   las herramientas con sus parámetros y resultados, y el registro de citas.
3. `errors_warnings/resumen.md` — anomalías de datos y cobertura.

## Limitaciones que conviene tener presentes

- **No se puede versionar el índice ni el modelo de embeddings**: la API no los
  expone. Cada traza lo declara en `versions.unknown`. Si el índice cambió
  durante la corrida, no lo detectaríamos; el censo del acervo en el manifiesto
  es un sustituto parcial.
- **No hay reranker** en el pipeline. Ver `retrieval_config/parametros.json`.
- El paquete **no incluye credenciales** por diseño.
"""
    _escribir(stage / "README.md", contenido)


# ── Utilidades ──────────────────────────────────────────────

def _git_info(repo: Path) -> dict:
    def corre(*args) -> str:
        try:
            return subprocess.run(
                args, cwd=repo, capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except Exception:
            return ""

    return {
        "commit": corre("git", "rev-parse", "HEAD"),
        "commit_corto": corre("git", "rev-parse", "--short", "HEAD"),
        "rama": corre("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "ultimo_mensaje": corre("git", "log", "-1", "--format=%s"),
        "arbol_limpio": corre("git", "status", "--porcelain") == "",
    }


def _empaquetar(stage: Path, zip_path: Path, prefijo: str) -> list[str]:
    """Comprime y barre en busca de secretos. Devuelve los archivos sospechosos."""
    fugas = []
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(stage.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(stage).as_posix()
            if EXCLUIR.search(rel):
                continue
            if _tiene_secreto(path):
                fugas.append(rel)
                continue
            z.write(path, f"{prefijo}/{rel}")
    return fugas


def _tiene_secreto(path: Path) -> bool:
    try:
        texto = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return any(p.search(texto) for p in PATRONES_SECRETO)


def _escribir(path: Path, contenido: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contenido, encoding="utf-8")


def _leer_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _leer_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
