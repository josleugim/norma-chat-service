"""Enlaces de FUENTES: expedientes a su ficha, criterios a su párrafo."""


# ═══════════════════════════════════════════════════════════════════════════
# Enlaces de FUENTES a la ficha del expediente (2-oct-2026)
# ═══════════════════════════════════════════════════════════════════════════

class TestEnlacesFuentes:
    """
    José Miguel pidió que cada expediente de FUENTES abra su ficha y cada
    criterio su párrafo. El enlace se arma desde el registro de citas, no desde
    lo que escribió el modelo.

    Se usa el `CitationRegistry` real: un doble que no respeta el contrato del
    original no prueba nada.
    """

    def _reg(self, *case_links):
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        for cl in case_links:
            reg.assign({"caseLink": cl, "id": cl}, "E")
        return reg

    def _enlazar(self, texto, reg):
        from core.enlaces_fuentes import enlazar_fuentes
        return enlazar_fuentes(texto, reg)

    def test_el_renglon_de_expediente_queda_enlazado(self):
        reg = self._reg("VCN-001-2017")
        texto = ("La multa fue X [E1].\n\nFUENTES\n"
                 "[E1] [RESOLUCIÓN] VCN-001-2017 | COFECE | "
                 "SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO | 18-05-2017")
        nuevo, resumen = self._enlazar(texto, reg)
        assert ("[E1] [RESOLUCIÓN] [VCN-001-2017]"
                "(/case-search?caseLink=VCN-001-2017&details=true) | COFECE"
                ) in nuevo
        assert resumen == {"enlazados": 1, "sin_enlazar": []}

    def test_el_cuerpo_no_se_toca(self):
        """Sólo FUENTES: un enlace a media frase no lo pidió nadie."""
        reg = self._reg("VCN-001-2017")
        texto = ("En VCN-001-2017 se sancionó [E1].\n\nFUENTES\n"
                 "[E1] VCN-001-2017 | COFECE")
        nuevo, _ = self._enlazar(texto, reg)
        assert nuevo.startswith("En VCN-001-2017 se sancionó [E1].")
        assert nuevo.count("/case-search") == 1

    def test_el_marcador_no_se_altera(self):
        """`[E1]` sigue resolviendo: la reparación y las referencias lo leen."""
        from core.validacion_salida import MARCADOR
        reg = self._reg("VCN-001-2017")
        nuevo, _ = self._enlazar("x [E1]\n\nFUENTES\n[E1] VCN-001-2017 | COFECE", reg)
        assert MARCADOR.findall(nuevo) == ["E1", "E1"]

    def test_el_identificador_sale_del_registro_no_del_renglon(self):
        """
        Si el modelo escribió otro expediente junto a `[E1]`, no se enlaza
        ese: el registro dice qué es `[E1]`, y el renglón no lo contiene.
        """
        reg = self._reg("VCN-004-2024")
        texto = "x [E1]\n\nFUENTES\n[E1] VCN-004-2022 | COFECE"
        nuevo, resumen = self._enlazar(texto, reg)
        assert "/case-search" not in nuevo
        assert resumen["sin_enlazar"] == [{
            "marker": "E1", "case_link": "VCN-004-2024",
            "motivo": "identificador_no_aparece_en_renglon"}]

    def test_no_enlaza_el_principal_dentro_del_cumplimiento(self):
        """`VCN-004-2022` es prefijo del acto de cumplimiento, otro documento."""
        reg = self._reg("VCN-004-2022")
        texto = "x [E1]\n\nFUENTES\n[E1] VCN-004-2022_2025_10_09 | COFECE"
        nuevo, resumen = self._enlazar(texto, reg)
        assert "/case-search" not in nuevo
        assert resumen["enlazados"] == 0

    def test_enlaza_el_identificador_al_final_del_renglon(self):
        reg = self._reg("VCN-001-2017")
        nuevo, resumen = self._enlazar("x [E1]\n\nFUENTES\n[E1] VCN-001-2017", reg)
        assert resumen["enlazados"] == 1
        assert nuevo.endswith("(/case-search?caseLink=VCN-001-2017&details=true)")

    def test_codifica_espacios_y_parentesis(self):
        """Un paréntesis sin codificar cierra el link de markdown antes de tiempo."""
        from core.enlaces_fuentes import ruta_expediente
        assert (ruta_expediente("CNT-002-2020 (Proplastic)")
                == "/case-search?caseLink=CNT-002-2020%20%28Proplastic%29&details=true")
        assert ruta_expediente("184_2018 1JD") == "/case-search?caseLink=184_2018%201JD&details=true"

    ANCHOR = ("En cumplimiento a lo dispuesto en el artículo 74, fracción I, "
              "de la Ley de Amparo, se precisan los actos reclamados")

    def _reg_criterio(self, metadata=None):
        """Como lo registra `_exec_buscar_criterios`: sin `anchor` en metadata."""
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        reg.assign({"id": "8471", "caseLink": "1251_2017_1JD",
                    "metadata": metadata or {"id_expediente": "1251_2017_1JD",
                                             "title": "Actos reclamados"}}, "C")
        return reg

    def test_criterio_enlaza_al_parrafo(self):
        """El formato que definió José Miguel el 5-oct, con la URL entre `<>`."""
        from core.enlaces_fuentes import enlazar_fuentes
        reg = self._reg_criterio()
        texto = ("x [C1]\n\nFUENTES\n"
                 "[C1] [SENTENCIA] 1251_2017_1JD | p. 3 | \"Actos reclamados\"")
        nuevo, resumen = enlazar_fuentes(texto, reg, ancla_de={"8471": self.ANCHOR}.get)
        assert resumen == {"enlazados": 1, "sin_enlazar": []}
        assert ("[C1] [SENTENCIA] [1251_2017_1JD](</digital-resolution?caseLink=1251_2017_1JD"
                "&anchor=En%20cumplimiento%20a%20lo%20dispuesto%20en%20el%20art%C3%ADculo%2074"
                "%2C%20fracci%C3%B3n%20I%2C%20de%20la%20Ley%20de%20Amparo%2C%20se%20precisan"
                "%20los%20actos%20reclamados&paragraphId=8471>) | p. 3") in nuevo

    def test_criterio_toma_el_anchor_de_su_metadata_si_lo_trae(self):
        """Los documentos de la caché pueden traerlo; no hace falta el mapa."""
        from core.enlaces_fuentes import enlazar_fuentes
        reg = self._reg_criterio({"id_expediente": "1251_2017_1JD", "anchor": self.ANCHOR})
        nuevo, resumen = enlazar_fuentes(
            "x\n\nFUENTES\n[C1] 1251_2017_1JD | p. 3", reg)
        assert resumen["enlazados"] == 1
        assert "&paragraphId=8471>)" in nuevo

    def test_criterio_sin_anchor_no_se_enlaza_a_otra_cosa(self):
        """
        Sin `anchor` no hay párrafo que subrayar. Enlazarlo a la ficha del
        expediente sería un enlace que no lleva a lo citado.
        """
        from core.enlaces_fuentes import enlazar_fuentes
        reg = self._reg_criterio()
        texto = "x\n\nFUENTES\n[C1] 1251_2017_1JD | p. 3"
        nuevo, resumen = enlazar_fuentes(texto, reg, ancla_de={}.get)
        assert nuevo == texto
        assert resumen["sin_enlazar"] == [{
            "marker": "C1", "case_link": "1251_2017_1JD",
            "motivo": "criterio_sin_anchor"}]

    def test_codifica_como_encodeURIComponent(self):
        """
        Como el frontend: deja `-_.!~*'()` y codifica todo lo demás, `/` y
        `,` incluidos. `quote` por defecto deja `/`; con `safe=""` codifica
        los paréntesis.
        """
        from core.enlaces_fuentes import ruta_parrafo
        r = ruta_parrafo("CNT-002-2020 (Proplastic)", "a/b, c (d)!", "1")
        assert r == ("/digital-resolution?caseLink=CNT-002-2020%20(Proplastic)"
                     "&anchor=a%2Fb%2C%20c%20(d)!&paragraphId=1")

    def test_la_cache_conserva_las_anclas_de_la_sesion(self):
        """Un turno que responde desde caché cita criterios de turnos anteriores."""
        from core.evidence_cache import EvidenceCache
        c = EvidenceCache()
        c.recordar_anclas("s1", {"8471": self.ANCHOR})
        c.recordar_anclas("s1", {})
        assert c.anclas("s1") == {"8471": self.ANCHOR}
        assert c.anclas("otra") == {}

    def test_sin_seccion_fuentes_no_hace_nada(self):
        reg = self._reg("VCN-001-2017")
        texto = "VCN-001-2017 [E1] sin sección de fuentes"
        assert self._enlazar(texto, reg) == (texto, {"enlazados": 0, "sin_enlazar": []})

    def test_encabezado_con_formato_markdown(self):
        reg = self._reg("VCN-001-2017")
        for enc in ("**FUENTES**", "### FUENTES", "FUENTES:", "**FUENTES:**"):
            nuevo, resumen = self._enlazar(f"x [E1]\n\n{enc}\n[E1] VCN-001-2017 | COFECE", reg)
            assert resumen["enlazados"] == 1, enc

    def test_es_idempotente(self):
        reg = self._reg("VCN-001-2017")
        texto = "x [E1]\n\nFUENTES\n[E1] VCN-001-2017 | COFECE"
        una, _ = self._enlazar(texto, reg)
        dos, _ = self._enlazar(una, reg)
        assert una == dos

    def test_el_evento_references_usa_la_misma_ruta(self):
        """El texto y el evento no pueden mandar al expediente por caminos distintos."""
        from core.citation_builder import CitationBuilder
        assert (CitationBuilder()._build_url("VCN-001-2017")
                == "/case-search?caseLink=VCN-001-2017&details=true")
        assert CitationBuilder()._build_url("") == ""

    def test_renglon_agrupado_enlaza_cada_marcador(self):
        """
        Visto en vivo el 2-oct (q12): el modelo agrupa las fuentes en un solo
        renglón. Cada marcador enlaza su expediente, no sólo el primero.
        """
        reg = self._reg("VCN-002-2024", "VCN-004-2022_2025_10_09")
        texto = ("x [E1] [E2]\n\nFUENTES\n- [E1]–[E2] [RESOLUCIÓN] VCN-002-2024 "
                 "a VCN-004-2022_2025_10_09 | COFECE")
        nuevo, resumen = self._enlazar(texto, reg)
        assert resumen == {"enlazados": 2, "sin_enlazar": []}
        assert "[VCN-002-2024](/case-search?caseLink=VCN-002-2024&details=true)" in nuevo
        assert ("[VCN-004-2022_2025_10_09](/case-search?caseLink="
                "VCN-004-2022_2025_10_09&details=true)") in nuevo

    def test_no_enlaza_dentro_de_un_enlace_ya_puesto(self):
        """
        El principal es prefijo del cumplimiento. Con los dos en el renglón,
        enlazar el cumplimiento primero no debe dejar que el principal se
        enlace dentro del texto o la URL del otro.
        """
        reg = self._reg("VCN-004-2022_2025_10_09", "VCN-004-2022")
        texto = ("x\n\nFUENTES\n[E1] VCN-004-2022_2025_10_09 | [E2] VCN-004-2022 | COFECE")
        nuevo, resumen = self._enlazar(texto, reg)
        assert resumen["enlazados"] == 2
        assert nuevo.count("/case-search") == 2
        assert "[VCN-004-2022](/case-search?caseLink=VCN-004-2022&details=true) | COFECE" in nuevo
