"""
Ejecuta una batería de preguntas contra el chat-service y deja una traza por
pregunta.

    # 1. levantar el servicio con la corrida identificada
    TRACING_ENABLED=true RUN_ID=run_2026-08-20_v1 RUN_LABEL="v1 baseline" \
    QUESTION_SET=pruebas_imanol_v1 \
    uvicorn main:app --port 8000

    # 2. correr la batería completa
    python run_question_set.py data/question_sets/pruebas_imanol_v1.json

    # 3. exportar
    python -m core.tracing.export traces/run_2026-08-20_v1

Opciones útiles:
    --only q01,q03      solo esas preguntas (arrastra sus turnos previos)
    --provider anthropic --model claude-sonnet-4-20250514
    --dry-run           imprime el plan sin llamar al servicio

REGLA DE LA RONDA: una vez iniciada la corrida, no se tocan prompt, tools ni
índice hasta terminarla. Si algo cambia a media corrida, las trazas lo marcan
como baseline_drift, pero la comparación v1 vs v1.1 ya quedó contaminada.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

DEFAULT_URL = "http://localhost:8000"


def load_set(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "questions" not in data:
        raise SystemExit(f"{path} no tiene 'questions'")
    return data


def resolve_order(questions: list[dict], only: set[str] | None) -> list[dict]:
    """
    Ordena las preguntas respetando dependencias: un turno de seguimiento
    necesita que su turno previo se haya ejecutado en la misma sesión.
    """
    by_id = {q["id"]: q for q in questions}
    selected: list[dict] = []
    seen: set[str] = set()

    def add(q: dict) -> None:
        if q["id"] in seen:
            return
        prev_id = q.get("turno_previo")
        if prev_id and prev_id in by_id:
            add(by_id[prev_id])
        seen.add(q["id"])
        selected.append(q)

    for q in questions:
        if only is None or q["id"] in only:
            add(q)
    return selected


async def ask(
    client: httpx.AsyncClient, url: str, question: dict, session_id: str,
    history: list[dict], provider: str, model: str, is_first: bool,
) -> dict:
    """Manda una pregunta y consume el SSE hasta `done`."""
    payload = {
        "session_id": session_id,
        "query": question["texto"],
        "provider": provider,
        "model": model,
        "chat_history": history,
        "is_first_message": is_first,
        "question_set_id": question["id"],
        "client": "test_harness",
    }

    text_parts: list[str] = []
    result = {"trace_id": None, "tools": 0, "refs": 0, "error": None}

    async with client.stream(
        "POST", f"{url}/api/chat/completions", json=payload, timeout=180.0
    ) as resp:
        resp.raise_for_status()
        event = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event == "token":
                    text_parts.append(data.get("text", ""))
                elif event == "references":
                    result["refs"] = len(data.get("items", []))
                elif event == "done":
                    result["trace_id"] = data.get("trace_id")
                    result["tools"] = data.get("tool_calls_count", 0)
                    result["exhausted"] = data.get("exhausted_tools", False)
                elif event == "error":
                    result["error"] = data.get("message")

    result["text"] = "".join(text_parts)
    return result


async def main_async(args: argparse.Namespace) -> int:
    qset = load_set(Path(args.question_set))
    only = set(args.only.split(",")) if args.only else None
    plan = resolve_order(qset["questions"], only)

    print(f"Set     : {qset['question_set_id']} v{qset.get('version', '?')}")
    print(f"Servicio: {args.url}")
    print(f"Modelo  : {args.provider}/{args.model}")
    print(f"Preguntas: {len(plan)}\n")

    if args.dry_run:
        for q in plan:
            prev = f"  (sigue a {q['turno_previo']})" if q.get("turno_previo") else ""
            print(f"  {q['id']}  {q['texto'][:70]}{prev}")
        return 0

    # Los turnos de seguimiento comparten sesión con su turno previo.
    sessions: dict[str, str] = {}
    histories: dict[str, list[dict]] = {}
    failures = 0

    async with httpx.AsyncClient() as client:
        for i, q in enumerate(plan, 1):
            prev_id = q.get("turno_previo")
            session_id = sessions.get(prev_id) or f"{qset['question_set_id']}_{q['id']}"
            sessions[q["id"]] = session_id
            history = histories.get(session_id, [])

            print(f"[{i}/{len(plan)}] {q['id']}  {q['texto'][:64]}")
            try:
                res = await ask(
                    client, args.url, q, session_id, history,
                    args.provider, args.model, is_first=not history,
                )
            except Exception as e:
                failures += 1
                print(f"          ERROR: {e}\n")
                continue

            if res["error"]:
                failures += 1
                print(f"          ERROR: {res['error']}")
            else:
                flags = " ⚠️ exhausted" if res.get("exhausted") else ""
                print(
                    f"          trace={res['trace_id']} tools={res['tools']} "
                    f"refs={res['refs']} chars={len(res['text'])}{flags}"
                )

            histories[session_id] = history + [
                {"role": "user", "content": q["texto"]},
                {"role": "assistant", "content": res["text"]},
            ]
            print()

            if args.delay:
                await asyncio.sleep(args.delay)

    print(f"Terminado. Fallos: {failures}/{len(plan)}")
    print("Exporta con: python -m core.tracing.export traces/<run_id>")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question_set")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--only", help="ids separados por coma")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="segundos entre preguntas (rate limiting)")
    parser.add_argument("--dry-run", action="store_true")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
