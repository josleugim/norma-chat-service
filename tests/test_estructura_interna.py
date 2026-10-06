"""¿La respuesta expone cómo está construida la base?"""


class TestEstructuraInterna:
    """
    Imanol, 5-oct: "el chatbot no debe demostrar como está construida su bd".
    El detector mide; no reescribe.
    """

    def test_el_caso_de_imanol(self):
        from core.estructura_interna import expuestos
        assert expuestos('A partir de la revisión del campo "dissentingOpinions" '
                         "en las resoluciones administrativas VCN") == ["dissentingOpinions"]

    def test_herramientas_parametros_y_senales(self):
        from core.estructura_interna import expuestos
        texto = ("Usé buscar_expedientes con prefijo_expediente; "
                 "llegó AUSENCIA_NO_CONCLUYENTE y ADVERTENCIA_COBERTURA_PARCIAL.")
        assert expuestos(texto) == [
            "ADVERTENCIA_COBERTURA_PARCIAL", "AUSENCIA_NO_CONCLUYENTE",
            "buscar_expedientes", "prefijo_expediente"]

    def test_no_marca_identificadores_de_expediente_ni_palabras(self):
        """
        Los expedientes judiciales llevan guion bajo, y varios campos son
        palabras ("name", "authority"): por eso la lista es cerrada.
        """
        from core.estructura_interna import expuestos
        texto = ("VCN-001-2017, 1251_2017_1JD y 565_2023_1TCC_2025-04-24; la "
                 "autoridad fue COFECE; the name of the authority; expedientes.")
        assert expuestos(texto) == []

    def test_un_campo_nuevo_del_registro_queda_cubierto(self):
        """Los campos salen del modelo, no de una lista escrita a mano."""
        from core.estructura_interna import terminos_internos
        from models.schemas import ExpedienteRecord
        assert "relatedTccCaseFile" in ExpedienteRecord.model_fields
        assert "relatedTccCaseFile" in terminos_internos()

    def test_el_prompt_lo_prohibe(self):
        from prompts.system import AGENT_SYSTEM_PROMPT
        assert "No muestres cómo está construida la base" in AGENT_SYSTEM_PROMPT

    def test_es_indicador_comparable_entre_corridas(self):
        """Un indicador que no llega a `compare.py` no se puede comparar."""
        from core.tracing.compare import INDICADORES
        from core.tracing.export import COLUMNS
        assert "estructura_interna_expuesta" in dict(INDICADORES)
        assert "estructura_interna_expuesta" in COLUMNS
