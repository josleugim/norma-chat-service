"""
Compara dos corridas.

    python -m core.tracing.compare traces/run_v1 traces/run_v1_1

Lo primero que revisa NO son los indicadores, sino si las dos corridas son
comparables: si el acervo creció entre una y otra, una mejora en exhaustividad
puede venir de documentos nuevos y no de un fix. El acervo se está cargando
mientras probamos, así que esto no es una precaución teórica.
"""
import argparse
import json
import sys
from pathlib import Path

from core.tracing.census import diff_census
from core.tracing.versioning import diff_fingerprints

INDICADORES = [
    ("coverage_truncated", "Cobertura truncada"),
    ("scope_mismatch", "Desajuste de scope"),
    ("context_condensed", "Contexto condensado"),
    ("exhausted_tools", "Agotó tool calls"),
    ("citations_unresolved", "Citas sin resolver"),
    ("plazo_inputs_missing", "Plazos sin fecha de inicio"),
    ("plazo_out_of_coverage", "Plazos fuera de cobertura"),
    ("errors", "Errores"),
]


def load(run_dir: Path) -> tuple[dict, dict]:
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    rows = {}
    for line in (run_dir / "run.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r.get("question_set_id") or r["trace_id"]] = r
    return manifest, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara dos corridas de trazas")
    parser.add_argument("antes")
    parser.add_argument("despues")
    args = parser.parse_args()

    m_a, r_a = load(Path(args.antes))
    m_b, r_b = load(Path(args.despues))

    print(f"ANTES  : {m_a['run_id']}  ({m_a.get('label','')})  {len(r_a)} preguntas")
    print(f"DESPUÉS: {m_b['run_id']}  ({m_b.get('label','')})  {len(r_b)} preguntas")

    # ── ¿Son comparables? ───────────────────────────────────
    print("\n── Comparabilidad ──")

    censo = diff_census(m_a.get("corpus_census", {}), m_b.get("corpus_census", {}))
    if censo:
        print("  ⚠️  EL ACERVO CAMBIÓ entre las dos corridas:")
        for c in censo:
            print(f"      {c['field']}: {c['before']} → {c['after']}")
        print("      Las preguntas de exhaustividad NO son comparables directamente.")
    elif not m_a.get("corpus_census") or not m_b.get("corpus_census"):
        print("  ⚠️  Alguna corrida no tiene censo del acervo; no se puede verificar.")
    else:
        print("  ✓ Acervo estable entre corridas.")

    entorno = diff_fingerprints(
        m_a.get("frozen_versions", {}), m_b.get("frozen_versions", {})
    )
    if entorno:
        print("  Cambios de entorno (esperados si esto es v1 → v1.1):")
        for c in entorno:
            print(f"      {c['field']}: {c['frozen']} → {c['current']}")
    else:
        print("  ✓ Entorno idéntico (¿seguro que se aplicó algún cambio?)")

    for m, name in ((m_a, "antes"), (m_b, "después")):
        if m.get("drift_detected"):
            print(f"  ⚠️  La corrida '{name}' tuvo drift a media ronda: "
                  f"{len(m['drift_detected'])} trazas afectadas.")

    # ── Indicadores ─────────────────────────────────────────
    print("\n── Indicadores ──")
    comunes = sorted(set(r_a) & set(r_b))
    print(f"  Preguntas en ambas corridas: {len(comunes)}")

    for campo, etiqueta in INDICADORES:
        a = sum(1 for q in comunes if r_a[q].get(campo))
        b = sum(1 for q in comunes if r_b[q].get(campo))
        if a == b == 0:
            continue
        flecha = "→" if a == b else ("↓ mejora" if b < a else "↑ empeora")
        print(f"  {etiqueta:<28} {a:>3} → {b:>3}   {flecha}")

    # ── Cambios por pregunta ────────────────────────────────
    print("\n── Cambios por pregunta ──")
    hubo = False
    for q in comunes:
        cambios = [
            f"{etiqueta}: {bool(r_a[q].get(c))}→{bool(r_b[q].get(c))}"
            for c, etiqueta in INDICADORES
            if bool(r_a[q].get(c)) != bool(r_b[q].get(c))
        ]
        if cambios:
            hubo = True
            print(f"  {q}: " + "; ".join(cambios))
    if not hubo:
        print("  Sin cambios en los indicadores.")

    solo_a, solo_b = set(r_a) - set(r_b), set(r_b) - set(r_a)
    if solo_a or solo_b:
        print(f"\n  Solo en antes: {sorted(solo_a)}")
        print(f"  Solo en después: {sorted(solo_b)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
