"""
Actuaciones posteriores que la respuesta no cita: las agrega el código.

Desde que existe la lectura de resoluciones, el modelo citaba menos la
historia procesal (H18: 7 de 15, contra 8 de 9 antes; 17-oct). Por
construcción, no por prompt.
"""
from core.citations import CitationRegistry
from core.relaciones import completar_historia_procesal, resumen_historia_procesal


def _caso():
    reg = CitationRegistry()
    e1 = reg.assign({"caseLink": "275_2023_1JD"}, "E")
    e2 = reg.assign({"caseLink": "353_2024_1TCC"}, "E")
    entradas = [{"documento": "275_2023_1JD", "posteriores": [
        {"expediente": "353_2024_1TCC", "ref": e2, "enlace": "resuelto",
         "tipo": "Amparo en revisión", "fecha": "21-08-2025"}]}]
    return reg, entradas, e1, e2


def test_agrega_la_actuacion_omitida_antes_de_fuentes():
    reg, entradas, e1, e2 = _caso()
    texto = f"Pasaron 14 días [{e1}].\n\nFUENTES\n- [{e1}] 275_2023_1JD"
    nuevo, agregadas = completar_historia_procesal(entradas, texto, reg)
    assert agregadas == [{"documento": "275_2023_1JD", "actuacion": "353_2024_1TCC", "ref": e2}]
    assert f"353_2024_1TCC (Amparo en revisión, 21-08-2025), posterior a 275_2023_1JD [{e2}]" in nuevo
    assert nuevo.index("Actuaciones posteriores") < nuevo.index("FUENTES")


def test_si_ya_la_cita_no_agrega_nada():
    reg, entradas, e1, e2 = _caso()
    texto = f"Pasaron 14 días [{e1}]; la revisión lo modificó [{e2}]."
    assert completar_historia_procesal(entradas, texto, reg) == (texto, [])


def test_si_no_usa_el_documento_no_agrega_nada():
    reg, entradas, _, _ = _caso()
    texto = "Otra cosa sin relación."
    assert completar_historia_procesal(entradas, texto, reg) == (texto, [])


def test_el_indicador_sigue_midiendo_al_modelo():
    reg, entradas, e1, _ = _caso()
    nuevo, agregadas = completar_historia_procesal(entradas, f"14 días [{e1}].", reg)
    assert resumen_historia_procesal(entradas, nuevo, reg)["posteriores_no_mencionados"] == []
    medido = resumen_historia_procesal(entradas, nuevo, reg, agregadas_por_codigo=agregadas)
    assert medido["posteriores_no_mencionados"] == [
        {"documento": "275_2023_1JD", "actuacion": "353_2024_1TCC"}]
