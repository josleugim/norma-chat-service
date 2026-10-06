"""Enlaces de las citas: en el texto y en FUENTES."""


class TestEnlacesFuentes:
    """
    José Miguel definió las rutas (2 y 5-oct). Imanol pidió (5-oct) que los
    marcadores del texto también sean enlace, y que en FUENTES el enlace sea
    todo el renglón: el renglón es la referencia exacta, no sólo el caso.

    Se usa el `CitationRegistry` real: un doble que no respeta el contrato del
    original no prueba nada.
    """

    URL_E = "/case-search?caseLink=VCN-001-2017&details=true"
    ANCHOR = ("En cumplimiento a lo dispuesto en el artículo 74, fracción I, "
              "de la Ley de Amparo, se precisan los actos reclamados")

    def _reg(self, *case_links):
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        for cl in case_links:
            reg.assign({"caseLink": cl, "id": cl}, "E")
        return reg

    def _reg_criterio(self, metadata=None):
        """Como lo registra `_exec_buscar_criterios`: sin `anchor` en metadata."""
        from core.citations import CitationRegistry
        reg = CitationRegistry()
        reg.assign({"id": "8471", "caseLink": "1251_2017_1JD",
                    "metadata": metadata or {"id_expediente": "1251_2017_1JD",
                                             "title": "Actos reclamados"}}, "C")
        return reg

    def _enlazar(self, texto, reg, ancla_de=None):
        from core.enlaces_fuentes import enlazar_fuentes
        return enlazar_fuentes(texto, reg, ancla_de)

    # ── FUENTES: el renglón completo ─────────────────────────

    def test_el_renglon_de_expediente_es_un_solo_enlace(self):
        reg = self._reg("VCN-001-2017")
        texto = ("x\n\nFUENTES\n"
                 "[E1] [RESOLUCIÓN] VCN-001-2017 | COFECE | "
                 "SANCIÓN/ACREDITACIÓN DEL INCUMPLIMIENTO | 18-05-2017")
        nuevo, resumen = self._enlazar(texto, reg)
        assert nuevo.endswith(
            "\n[[E1] [RESOLUCIÓN] VCN-001-2017 | COFECE | SANCIÓN/ACREDITACIÓN "
            f"DEL INCUMPLIMIENTO | 18-05-2017]({self.URL_E})")
        assert resumen == {"en_texto": 0, "en_fuentes": 1, "sin_enlazar": [],
                           "etiquetas_sin_cita_retiradas": []}

    def test_la_vinieta_queda_fuera_del_enlace(self):
        reg = self._reg("VCN-001-2017")
        nuevo, _ = self._enlazar("x\n\nFUENTES\n- [E1] VCN-001-2017 | COFECE  ", reg)
        assert nuevo.endswith(f"\n- [[E1] VCN-001-2017 | COFECE]({self.URL_E})  ")

    def test_criterio_enlaza_al_parrafo(self):
        """El formato que definió José Miguel el 5-oct, con la URL entre `<>`."""
        reg = self._reg_criterio()
        texto = "x\n\nFUENTES\n[C1] [SENTENCIA] 1251_2017_1JD | p. 3 | \"Actos reclamados\""
        nuevo, resumen = self._enlazar(texto, reg, {"8471": self.ANCHOR}.get)
        assert resumen == {"en_texto": 0, "en_fuentes": 1, "sin_enlazar": [],
                           "etiquetas_sin_cita_retiradas": []}
        assert nuevo.endswith(
            "\n[[C1] [SENTENCIA] 1251_2017_1JD | p. 3 | \"Actos reclamados\"]"
            "(</digital-resolution?caseLink=1251_2017_1JD"
            "&anchor=En%20cumplimiento%20a%20lo%20dispuesto%20en%20el%20art%C3%ADculo%2074"
            "%2C%20fracci%C3%B3n%20I%2C%20de%20la%20Ley%20de%20Amparo%2C%20se%20precisan"
            "%20los%20actos%20reclamados&paragraphId=8471>)")

    def test_el_destino_sale_del_registro_no_del_renglon(self):
        """
        Si el modelo escribió otro expediente junto a `[E1]`, no se enlaza: el
        texto del enlace diría un expediente y llevaría a otro.
        """
        reg = self._reg("VCN-004-2024")
        texto = "x\n\nFUENTES\n[E1] VCN-004-2022 | COFECE"
        nuevo, resumen = self._enlazar(texto, reg)
        assert nuevo == texto
        assert resumen["sin_enlazar"] == [{
            "marker": "E1", "case_link": "VCN-004-2024", "donde": "fuentes",
            "motivo": "identificador_no_aparece_en_renglon"}]

    def test_el_principal_no_cuenta_dentro_del_cumplimiento(self):
        """`VCN-004-2022` es prefijo del acto de cumplimiento, otro documento."""
        reg = self._reg("VCN-004-2022")
        texto = "x\n\nFUENTES\n[E1] VCN-004-2022_2025_10_09 | COFECE"
        nuevo, resumen = self._enlazar(texto, reg)
        assert nuevo == texto
        assert resumen["en_fuentes"] == 0

    def test_renglon_agrupado_enlaza_cada_marcador(self):
        """
        Visto en vivo el 2-oct (q12): el modelo agrupa las fuentes en un solo
        renglón. Un enlace sobre todo el renglón llevaría a uno solo; cada
        marcador enlaza su identificador.
        """
        reg = self._reg("VCN-002-2024", "VCN-004-2022_2025_10_09")
        texto = ("x\n\nFUENTES\n- [E1]–[E2] [RESOLUCIÓN] VCN-002-2024 "
                 "a VCN-004-2022_2025_10_09 | COFECE")
        nuevo, resumen = self._enlazar(texto, reg)
        assert resumen == {"en_texto": 0, "en_fuentes": 2, "sin_enlazar": [],
                           "etiquetas_sin_cita_retiradas": []}
        assert "[VCN-002-2024](/case-search?caseLink=VCN-002-2024&details=true)" in nuevo
        assert ("[VCN-004-2022_2025_10_09](/case-search?caseLink="
                "VCN-004-2022_2025_10_09&details=true)") in nuevo

    def test_renglon_agrupado_no_enlaza_dentro_de_otro_enlace(self):
        """Con el cumplimiento ya enlazado, el principal no se enlaza en su URL."""
        reg = self._reg("VCN-004-2022_2025_10_09", "VCN-004-2022")
        texto = "x\n\nFUENTES\n[E1] VCN-004-2022_2025_10_09 | [E2] VCN-004-2022 | COFECE"
        nuevo, resumen = self._enlazar(texto, reg)
        assert resumen["en_fuentes"] == 2
        assert nuevo.count("/case-search") == 2
        assert "[VCN-004-2022](/case-search?caseLink=VCN-004-2022&details=true) | COFECE" in nuevo

    def test_encabezado_con_formato_markdown(self):
        reg = self._reg("VCN-001-2017")
        for enc in ("**FUENTES**", "### FUENTES", "FUENTES:", "**FUENTES:**"):
            _, resumen = self._enlazar(f"x\n\n{enc}\n[E1] VCN-001-2017 | COFECE", reg)
            assert resumen["en_fuentes"] == 1, enc

    # ── El texto: cada marcador ──────────────────────────────

    def test_los_marcadores_del_texto_son_enlaces(self):
        reg = self._reg("VCN-001-2017")
        nuevo, resumen = self._enlazar(
            "La multa fue de $365,200 [E1].\n\nFUENTES\n[E1] VCN-001-2017", reg)
        assert nuevo.startswith(f"La multa fue de $365,200 [[E1]]({self.URL_E}).")
        assert resumen["en_texto"] == 1

    def test_marcador_de_criterio_en_el_texto_lleva_al_parrafo(self):
        reg = self._reg_criterio()
        nuevo, _ = self._enlazar("Los actos se precisan así [C1].", reg,
                                 {"8471": self.ANCHOR}.get)
        assert nuevo.startswith("Los actos se precisan así [[C1]](</digital-resolution?")
        assert nuevo.endswith("&paragraphId=8471>).")

    def test_sin_seccion_fuentes_tambien_enlaza_el_texto(self):
        reg = self._reg("VCN-001-2017")
        nuevo, resumen = self._enlazar("Dato [E1]", reg)
        assert nuevo == f"Dato [[E1]]({self.URL_E})"
        assert resumen["en_fuentes"] == 0

    def test_el_identificador_suelto_en_el_texto_no_se_toca(self):
        """Sólo el marcador: un enlace a media frase no lo pidió nadie."""
        reg = self._reg("VCN-001-2017")
        nuevo, _ = self._enlazar("En VCN-001-2017 se sancionó [E1].", reg)
        assert nuevo.startswith("En VCN-001-2017 se sancionó [[E1]](")

    def test_marcadores_contiguos(self):
        reg = self._reg("VCN-001-2017", "VCN-002-2017")
        nuevo, resumen = self._enlazar("Dato [E1][E2].", reg)
        assert resumen["en_texto"] == 2
        assert "[[E1]](/case-search?caseLink=VCN-001-2017&details=true)" \
               "[[E2]](/case-search?caseLink=VCN-002-2017&details=true)." in nuevo

    def test_marcador_que_no_resuelve_queda_como_estaba(self):
        reg = self._reg("VCN-001-2017")
        nuevo, resumen = self._enlazar("Dato [E9].", reg)
        assert nuevo == "Dato [E9]."
        assert resumen["sin_enlazar"] == [{
            "marker": "E9", "donde": "texto", "motivo": "sin_expediente_en_registro"}]

    # ── Lo que no debe romperse ──────────────────────────────

    def test_los_marcadores_siguen_legibles_para_quien_los_lee_despues(self):
        """
        El constructor de referencias, la validación y el análisis de la traza
        buscan `[C1]`/`[E1]` literal. Con los corchetes sin escapar, siguen
        encontrando exactamente los mismos.
        """
        from core.validacion_salida import MARCADOR
        reg = self._reg("VCN-001-2017", "VCN-002-2017")
        texto = "a [E1] b [E2]\n\nFUENTES\n[E1] VCN-001-2017\n[E2] VCN-002-2017"
        nuevo, _ = self._enlazar(texto, reg)
        assert MARCADOR.findall(nuevo) == MARCADOR.findall(texto)

    def test_es_idempotente(self):
        reg = self._reg("VCN-001-2017")
        texto = "x [E1]\n\nFUENTES\n[E1] VCN-001-2017 | COFECE"
        una, _ = self._enlazar(texto, reg)
        dos, resumen = self._enlazar(una, reg)
        assert una == dos
        assert resumen == {"en_texto": 0, "en_fuentes": 0, "sin_enlazar": [],
                           "etiquetas_sin_cita_retiradas": []}

    def test_criterio_sin_anchor_no_se_enlaza_a_otra_cosa(self):
        """
        Sin `anchor` no hay párrafo que subrayar. Enlazarlo a la ficha del
        expediente sería un enlace que no lleva a lo citado.
        """
        reg = self._reg_criterio()
        texto = "Dato [C1]\n\nFUENTES\n[C1] 1251_2017_1JD | p. 3"
        nuevo, resumen = self._enlazar(texto, reg, {}.get)
        assert nuevo == texto
        assert [s["motivo"] for s in resumen["sin_enlazar"]] == [
            "criterio_sin_anchor", "criterio_sin_anchor"]

    def test_criterio_toma_el_anchor_de_su_metadata_si_lo_trae(self):
        """Los documentos de la caché pueden traerlo; no hace falta el mapa."""
        reg = self._reg_criterio({"id_expediente": "1251_2017_1JD", "anchor": self.ANCHOR})
        nuevo, resumen = self._enlazar("x\n\nFUENTES\n[C1] 1251_2017_1JD | p. 3", reg)
        assert resumen["en_fuentes"] == 1
        assert nuevo.endswith("&paragraphId=8471>)")

    # ── Rutas ────────────────────────────────────────────────

    def test_codifica_espacios_y_parentesis_en_la_ruta_de_expediente(self):
        """Un paréntesis sin codificar cierra el link de markdown antes de tiempo."""
        from core.enlaces_fuentes import ruta_expediente
        assert (ruta_expediente("CNT-002-2020 (Proplastic)")
                == "/case-search?caseLink=CNT-002-2020%20%28Proplastic%29&details=true")
        assert ruta_expediente("184_2018 1JD") == "/case-search?caseLink=184_2018%201JD&details=true"

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

    def test_el_evento_references_usa_la_misma_ruta(self):
        """El texto y el evento no pueden mandar al expediente por caminos distintos."""
        from core.citation_builder import CitationBuilder
        assert CitationBuilder()._build_url("VCN-001-2017") == self.URL_E
        assert CitationBuilder()._build_url("") == ""

    def test_la_cache_conserva_las_anclas_de_la_sesion(self):
        """Un turno que responde desde caché cita criterios de turnos anteriores."""
        from core.evidence_cache import EvidenceCache
        c = EvidenceCache()
        c.recordar_anclas("s1", {"8471": self.ANCHOR})
        c.recordar_anclas("s1", {})
        assert c.anclas("s1") == {"8471": self.ANCHOR}
        assert c.anclas("otra") == {}

    # ── Renglones que parecen fuente sin serlo ───────────────

    def test_etiqueta_de_tipo_sin_marcador_queda_como_nota(self):
        """
        q01, 5-oct: "- [RESOLUCIÓN] Cálculo agregado sobre 32 expedientes".
        Un cálculo no es una resolución; sin marcador no hay documento detrás.
        """
        reg = self._reg("VCN-001-2017")
        texto = "x\n\nFUENTES\n- [RESOLUCIÓN] Cálculo agregado sobre 32 expedientes VCN"
        nuevo, resumen = self._enlazar(texto, reg)
        assert nuevo.endswith("\n- Cálculo agregado sobre 32 expedientes VCN")
        assert resumen["etiquetas_sin_cita_retiradas"] == [
            "- [RESOLUCIÓN] Cálculo agregado sobre 32 expedientes VCN"]

    def test_el_caso_de_q20(self):
        reg = self._reg()
        nuevo, _ = self._enlazar(
            "x\n\nFUENTES\n- [RESOLUCIÓN] CFC | Total de resoluciones emitidas: 2,792", reg)
        assert nuevo.endswith("\n- CFC | Total de resoluciones emitidas: 2,792")

    def test_la_etiqueta_con_marcador_se_conserva(self):
        reg = self._reg("VCN-001-2017")
        nuevo, resumen = self._enlazar("x\n\nFUENTES\n[E1] [RESOLUCIÓN] VCN-001-2017", reg)
        assert "[RESOLUCIÓN]" in nuevo
        assert resumen["etiquetas_sin_cita_retiradas"] == []

    def test_la_nota_sin_etiqueta_no_se_toca(self):
        reg = self._reg()
        texto = "x\n\nFUENTES\nNo se citan expedientes individuales: es un agregado."
        assert self._enlazar(texto, reg)[0] == texto

    def test_fuera_de_fuentes_la_etiqueta_no_se_toca(self):
        """En el cuerpo puede ser legítimo hablar de una [SENTENCIA]."""
        reg = self._reg()
        texto = "Una [SENTENCIA] del juzgado lo sostuvo."
        assert self._enlazar(texto, reg)[0] == texto

    def test_el_prompt_dice_que_un_calculo_no_es_fuente(self):
        from prompts.system import AGENT_SYSTEM_PROMPT
        assert "Un cálculo agregado o un conteo no es una fuente" in AGENT_SYSTEM_PROMPT
