"""
Uso por licencia y trazas que sobreviven al despliegue (6-oct-2026).

COFECE abre la beta: hace falta poder contar uso por usuario y revisar lo que
un usuario reporte. Las trazas vivían en el disco del contenedor, que se
reemplaza en cada despliegue.
"""
import json
import logging
import types


def _traza(**request):
    from core.tracing.schema import Trace, Request
    return Trace(trace_id="tr_x", conversation_id="s", turn_index=0, run_id="r",
                 timestamp_utc="2026-10-06T20:00:00Z",
                 request=Request(query="¿cuánto tarda la COFECE?", model="gpt-4.1", **request))


class TestCosto:

    def test_costo_por_modelo(self):
        from core.costos import costo_estimado
        # 19,356 de entrada y 582 de salida: la mediana medida con gpt-4.1.
        assert costo_estimado("gpt-4.1", 19356, 582) == round((19356 * 2 + 582 * 8) / 1e6, 6)
        assert costo_estimado("claude-sonnet-5-5", 1_000_000, 0) == 2.0

    def test_modelo_desconocido_no_inventa_cifra(self):
        from core.costos import costo_estimado
        assert costo_estimado("modelo-x", 1000, 1000) is None


class TestUsuarioYPlan:

    def test_la_peticion_los_acepta_opcionales(self):
        from models.schemas import ChatRequest
        r = ChatRequest(session_id="s", query="q")
        assert r.usuario_ref is None and r.plan is None
        r = ChatRequest(session_id="s", query="q", usuario_ref="u_9f2", plan="entrada")
        assert (r.usuario_ref, r.plan) == ("u_9f2", "entrada")

    def test_llegan_al_resumen_de_la_traza(self):
        s = _traza(usuario_ref="u_9f2", plan="superior").summary()
        assert (s["usuario_ref"], s["plan"], s["model"]) == ("u_9f2", "superior", "gpt-4.1")
        assert "cost_usd_estimate" in s

    def test_el_router_los_pasa_al_agente(self):
        import inspect
        import routers.chat as chat
        src = inspect.getsource(chat)
        assert "usuario_ref=request.usuario_ref" in src and "plan=request.plan" in src


class TestDestinos:

    def test_stdout_escribe_una_linea_json(self, caplog):
        from core.tracing.sinks import StdoutSummarySink
        with caplog.at_level(logging.INFO, logger="trazas"):
            StdoutSummarySink().write(_traza(usuario_ref="u_1"))
        linea = next(r.message for r in caplog.records if r.name == "trazas")
        assert linea.startswith("TRAZA ")
        assert json.loads(linea[len("TRAZA "):])["usuario_ref"] == "u_1"

    def test_s3_sube_la_traza_completa_con_clave_por_fecha(self):
        from core.tracing.sinks import S3Sink
        subidas = []
        cliente = types.SimpleNamespace(put_object=lambda **kw: subidas.append(kw))
        sink = S3Sink("norma-trazas", "trazas", cliente=cliente)
        sink.write(_traza())
        sink.flush()
        assert subidas[0]["Bucket"] == "norma-trazas"
        assert subidas[0]["Key"] == "trazas/2026/10/06/tr_x.json"
        assert json.loads(subidas[0]["Body"])["trace_id"] == "tr_x"

    def test_un_fallo_de_s3_no_revienta(self):
        from core.tracing.sinks import S3Sink
        def falla(**kw):
            raise RuntimeError("sin red")
        sink = S3Sink("b", cliente=types.SimpleNamespace(put_object=falla))
        sink.write(_traza())
        sink.flush()  # no levanta

    def _settings(self, **kw):
        base = dict(tracing_enabled=True, traces_dir="/tmp/norma-test-trazas",
                    run_id="r", tracing_full_text=False, trace_sinks="jsonl",
                    trace_s3_bucket="", trace_s3_prefix="trazas",
                    trace_s3_region="mx-central-1")
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_por_omision_sigue_siendo_jsonl(self):
        from core.tracing.sinks import build_sink, JsonlFileSink
        assert isinstance(build_sink(self._settings()), JsonlFileSink)

    def test_varios_destinos(self):
        from core.tracing.sinks import build_sink, MultiSink, StdoutSummarySink
        s = build_sink(self._settings(trace_sinks="jsonl,stdout"))
        assert isinstance(s, MultiSink)
        assert any(isinstance(x, StdoutSummarySink) for x in s.sinks)

    def test_s3_sin_bucket_se_omite_y_no_revienta(self):
        from core.tracing.sinks import build_sink, StdoutSummarySink
        s = build_sink(self._settings(trace_sinks="stdout,s3"))
        assert isinstance(s, StdoutSummarySink)
