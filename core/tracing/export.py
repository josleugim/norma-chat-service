"""
Export de una corrida a CSV/XLSX.

  python -m core.tracing.export traces/run_2026-08-20_v1
  python -m core.tracing.export traces/run_2026-08-20_v1 --format csv

Una fila por pregunta, con las columnas que se miran primero al analizar:
cobertura truncada, tool de plazos, desajuste de scope, citas sin resolver,
contexto condensado. La idea es que la tabla se ordene por la columna que duele.
"""
import argparse
import json
import sys
from pathlib import Path

# Orden pensado para leerse de izquierda a derecha: primero qué se preguntó y
# qué contestó, luego dónde falló, y al final la telemetría.
COLUMNS = [
    "question_set_id", "query", "answer", "sources", "diagnosis_stage",
    "diagnosis_reason", "tools_used", "tools_expected_not_called",
    "tool_call_count", "coverage_strategy", "docs_retrieved",
    "coverage_truncated", "exhaustive_but_truncated", "deadline_tool_called",
    "plazo_cases", "plazo_inputs_missing", "plazo_out_of_coverage",
    "second_retrieval", "exhausted_tools", "context_condensed",
    "final_answer_path", "scope_expected", "scope_observed", "scope_mismatch",
    "citations_emitted", "citations_unresolved", "answer_chars",
    "baseline_drift", "status", "duration_ms", "tokens_input",
    "tokens_output", "trace_id",
]


def load_summaries(run_dir: Path) -> list[dict]:
    jsonl = run_dir / "run.jsonl"
    if not jsonl.exists():
        raise SystemExit(f"No existe {jsonl}")
    rows = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return _add_diagnosis(run_dir, rows)


def _add_diagnosis(run_dir: Path, rows: list[dict]) -> list[dict]:
    """
    Adjunta a cada fila la etapa del pipeline donde se rompió el flujo.

    Es el criterio de aceptación de COFECE: ante una respuesta incorrecta,
    poder decir si el error fue de pregunta/filtros, retrieval, selección de
    contexto, tool calling, generación o cita.
    """
    from core.tracing.diagnose import diagnose
    from core.tracing.schema import Trace

    for row in rows:
        path = run_dir / "traces" / f"{row['trace_id']}.json"
        row["diagnosis_stage"] = None
        row["diagnosis_reason"] = None
        if not path.exists():
            continue
        try:
            d = diagnose(Trace.model_validate_json(path.read_text(encoding="utf-8")))
            row["diagnosis_stage"] = d["stage"]
            row["diagnosis_reason"] = d["reason"]
        except Exception:
            # Un diagnóstico que falla no debe tumbar el export.
            continue
    return rows


def bundle_zip(run_dir: Path) -> Path:
    """
    Empaqueta la corrida completa: resumen, trazas y export.

    COFECE fue explícito en que necesita poder llevarse una corrida sin
    pedirle a nadie que busque logs en el servidor.
    """
    import zipfile

    out = run_dir / f"{run_dir.name}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file() and path != out:
                z.write(path, path.relative_to(run_dir.parent))
    return out


def export(run_dir: Path, fmt: str = "xlsx") -> Path:
    rows = load_summaries(run_dir)
    if not rows:
        raise SystemExit("La corrida no tiene trazas.")

    ordered = [{c: r.get(c) for c in COLUMNS} for r in rows]

    if fmt == "csv":
        import csv
        out = run_dir / "export.csv"
        with out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(ordered)
        return out

    import pandas as pd
    out = run_dir / "export.xlsx"
    pd.DataFrame(ordered).to_excel(out, index=False, sheet_name="corrida")
    return out


def print_report(rows: list[dict]) -> None:
    """Resumen en consola: los indicadores que importan de un vistazo."""
    n = len(rows)

    def pct(predicate) -> str:
        k = sum(1 for r in rows if predicate(r))
        return f"{k}/{n} ({100 * k // n if n else 0}%)"

    print(f"\nTrazas: {n}")
    print(f"  Cobertura truncada        : {pct(lambda r: r.get('coverage_truncated'))}")
    print(f"  Contexto condensado       : {pct(lambda r: r.get('context_condensed'))}")
    print(f"  Agotó tool calls          : {pct(lambda r: r.get('exhausted_tools'))}")
    print(f"  Desajuste de scope        : {pct(lambda r: r.get('scope_mismatch'))}")
    print(f"  Citas sin resolver        : {pct(lambda r: r.get('citations_unresolved'))}")
    print(f"  Sin retrieval             : {pct(lambda r: not r.get('tools_used'))}")
    print(f"  Plazos sin fecha de inicio: {pct(lambda r: r.get('plazo_inputs_missing'))}")
    print(f"  Plazos fuera de cobertura : {pct(lambda r: r.get('plazo_out_of_coverage'))}")
    print(f"  Exhaustiva pero truncada  : {pct(lambda r: r.get('exhaustive_but_truncated'))}")
    print(f"  Tool esperada no llamada  : {pct(lambda r: r.get('tools_expected_not_called'))}")
    print(f"  Drift del baseline        : {pct(lambda r: r.get('baseline_drift'))}")
    print(f"  Errores                   : {pct(lambda r: r.get('errors'))}")

    etapas: dict = {}
    for r in rows:
        etapa = r.get("diagnosis_stage")
        if etapa:
            etapas[etapa] = etapas.get(etapa, 0) + 1
    if etapas:
        print("\n  Etapa donde se rompió el flujo:")
        for etapa, n in sorted(etapas.items(), key=lambda kv: -kv[1]):
            print(f"    {etapa:<22} {n}")
        limpias = n_total - sum(etapas.values()) if (n_total := len(rows)) else 0
        print(f"    {'(sin anomalías)':<22} {limpias}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export de una corrida de trazas")
    parser.add_argument("run_dir", help="Directorio de la corrida (traces/<run_id>)")
    parser.add_argument("--format", choices=["xlsx", "csv"], default="xlsx")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--zip", action="store_true",
                        help="empaqueta la corrida completa en un ZIP")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    rows = load_summaries(run_dir)
    out = export(run_dir, args.format)
    print(f"Escrito: {out}")
    if args.zip:
        print(f"Escrito: {bundle_zip(run_dir)}")
    if not args.no_report:
        print_report(rows)


if __name__ == "__main__":
    sys.exit(main())
