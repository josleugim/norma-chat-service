"""
Una cita de la respuesta anterior sigue resolviendo en el turno siguiente
(31-oct).

Humo `humo_2026-10-31_ficha_barrido`, t03 ("¿puedes confirmar que recuperaste
toda la información?"): la respuesta citó `[C14]` (la sentencia
153_2016_2TCC, citada en el turno previo), pero la caché sólo llevó al turno
nuevo 12 criterios en orden de recuperación. La frase se retiró y el usuario
vio "[AFIRMACIÓN RETIRADA: …]" a mitad del texto.

Dos cosas se fijan aquí: lo citado viaja con su mismo marcador (renumerar
haría que `[C2]` del historial apuntara a otro documento), y una retirada que
aun así ocurra se declara al final, no con un corchete en medio.
"""
from core.citations import CitationRegistry
from core.evidence_cache import EvidenceCache
from core.validacion_salida import validar_borrador


def _crit(i):
    return {"id": str(1000 + i), "text": f"texto {i}",
            "metadata": {"id_expediente": f"CNT-{i:03d}-2020", "title": f"Tema {i}"}}


class TestMarcadorPreferido:
    def test_conserva_el_marcador_si_esta_libre(self):
        r = CitationRegistry()
        assert r.assign(_crit(14), "C", preferido="C14") == "C14"
        assert r.resolve("C14")["id"] == "1014"

    def test_los_siguientes_no_chocan_con_el_reservado(self):
        r = CitationRegistry()
        r.assign(_crit(2), "C", preferido="C2")
        assert r.assign(_crit(5), "C") == "C3"   # el contador pasó del reservado
        r2 = CitationRegistry()
        r2.assign(_crit(1), "C")                 # C1
        r2.assign(_crit(9), "C", preferido="C2")
        assert r2.assign(_crit(3), "C") == "C3"  # salta el ocupado

    def test_un_preferido_ocupado_o_de_otro_tipo_no_se_usa(self):
        r = CitationRegistry()
        r.assign(_crit(1), "C", preferido="C1")
        assert r.assign(_crit(2), "C", preferido="C1") == "C2"
        assert r.assign(_crit(3), "C", preferido="E7") == "C3"


class TestLaCacheReusaLosMarcadores:
    def _cache_con_turno(self, citados):
        cache = EvidenceCache()
        criterios = [_crit(i) for i in range(1, 21)]
        marcadores = {c["id"]: f"C{i}" for i, c in enumerate(criterios, 1)}
        cache.update("s1", "pregunta", criterios, [], marcadores=marcadores, citados=citados)
        return cache

    def test_lo_citado_entra_aunque_pase_del_limite(self):
        cache = self._cache_con_turno(["C1", "C14"])
        reg = CitationRegistry()
        texto = cache.contexto_para_turno("s1", reg, limite=12)
        assert "[C14] CNT-014-2020" in texto
        assert reg.resolve("C14")["id"] == "1014"

    def test_ningun_marcador_cambia_de_documento(self):
        """Lo que hace segura la reutilización: el mismo número, el mismo documento."""
        cache = self._cache_con_turno(["C14"])
        reg = CitationRegistry()
        cache.contexto_para_turno("s1", reg, limite=12)
        for m in reg.markers():
            assert reg.resolve(m)["id"] == str(1000 + int(m[1:]))

    def test_un_seguimiento_que_cita_lo_anterior_ya_no_retira_nada(self):
        cache = self._cache_con_turno(["C14"])
        reg = CitationRegistry()
        cache.contexto_para_turno("s1", reg, limite=12)
        r = validar_borrador("La sentencia lo confirmó [C14].", reg)
        assert r["reparado"] is False

    def test_sin_marcadores_guardados_se_comporta_como_antes(self):
        cache = EvidenceCache()
        cache.update("s1", "q", [_crit(i) for i in range(1, 21)], [])
        reg = CitationRegistry()
        cache.contexto_para_turno("s1", reg, limite=12)
        assert len(reg.markers()) == 12


class TestLaRetiradaSeDeclaraAlFinal:
    class _Reg:
        def resolve(self, m): return {"id": m} if m == "C1" else None

    def test_sin_corchete_en_medio_y_con_nota_al_final(self):
        r = validar_borrador("Primero [C1]. Lo dijo el tribunal [C14]. Después [C1].", self._Reg())
        assert "AFIRMACIÓN RETIRADA" not in r["texto"]
        assert "tribunal" not in r["texto"]
        assert r["texto"].rstrip().endswith(
            "se omitió 1 afirmación porque su cita no correspondía a ninguna "
            "fuente de esta respuesta._")
        assert r["frases_sin_respaldo"] == ["Lo dijo el tribunal ."] or \
            any("tribunal" in f for f in r["frases_sin_respaldo"])

    def test_no_deja_viñetas_vacias(self):
        r = validar_borrador("Puntos:\n- Bien [C1].\n- Mal [C9].\n- Bien otra vez [C1].", self._Reg())
        assert "- \n" not in r["texto"] and not any(
            ln.strip() == "-" for ln in r["texto"].splitlines())
        assert "Mal" not in r["texto"]

    def test_varias_retiradas_en_plural(self):
        r = validar_borrador("Uno [C8]. Dos [C9].", self._Reg())
        assert "se omitieron 2 afirmaciones" in r["texto"]

    def test_la_nota_va_antes_de_fuentes(self):
        r = validar_borrador("Uno [C1]. Dos [C9].\n\nFUENTES\n[C1] CNT-001-2020\n[C9] X", self._Reg())
        cuerpo, _, fuentes = r["texto"].partition("FUENTES")
        assert "se omitió 1 afirmación" in cuerpo
        assert "[C9]" not in fuentes
