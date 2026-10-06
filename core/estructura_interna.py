"""
¿La respuesta expone cómo está construida la base?

Imanol, 5-oct-2026, sobre una respuesta que decía *"A partir de la revisión del
campo "dissentingOpinions" en las resoluciones administrativas VCN"*:

    "el chatbot no debe demostrar como está construida su bd; basta con que
     diga que a partir de su información disponible"

El modelo copia lo que lee: los resultados de herramienta traen los nombres
reales de los campos, de las herramientas y de las señales que le pasamos. El
prompt le pide no hacerlo; esto **mide** si lo hizo, sin reescribir el texto.
Reescribir "el campo dissentingOpinions" a algo legible deja una frase que
sigue hablando de un campo.

Se busca sólo una lista cerrada de identificadores que de verdad le llegan al
modelo, no un patrón genérico de camelCase o snake_case: los identificadores
de expediente judiciales llevan guiones bajos (`1251_2017_1JD`) y un patrón
los marcaría a todos.

Los campos y las herramientas se toman del código, así que un campo nuevo
queda cubierto sin tocar esta lista.
"""
import re
from functools import lru_cache

# Nombres de campo que también son palabras comunes: marcarlos daría falsos
# positivos en cualquier texto que diga "name" o "authority" citando algo.
_CAMPOS_PALABRA = {"id", "name", "authority", "parent", "resource"}

# Parámetros de herramienta sin guion bajo son palabras en español
# ("autoridad", "expedientes", "unidad"): sólo cuentan los compuestos.

# Claves y señales que van en los resultados de herramienta.
_CLAVES_DEL_PAYLOAD = {
    "ficha_fuente", "tipo_fuente", "voz_etiqueta", "voz_evidencia",
    "autor_del_voto", "composicion_fuentes", "cobertura_por_documento",
    "campos_no_disponibles", "cobertura_completa", "denominador_real",
    "total_del_universo", "total_en_la_base", "universo_completo_revisado",
    "universo_recuperado", "universo_tras_filtros",
    "fuera_de_cobertura_del_calendario", "text_len_in_context",
    "NO_DISPONIBLE_EN_ESTA_BUSQUEDA", "AUSENCIA_NO_CONCLUYENTE",
    "ACUERDOS_DE_SUSPENSION_COINCIDENTES", "ADVERTENCIA_COBERTURA",
    "ADVERTENCIA_COBERTURA_PARCIAL", "ADVERTENCIA_CONFIDENCIALES",
    "ADVERTENCIA_VALORES_AMBIGUOS", "ALCANCE_DE_LA_CIFRA",
    "CANDIDATOS_DE_EJEMPLO", "COBERTURA_DEL_CALENDARIO", "COMO_CITAR",
    "COMO_DEBES_DESCRIBIR_LA_COBERTURA", "EVIDENCE_CHECK",
    "EVIDENCIA_FALTANTE_POR_DOCUMENTO", "EVIDENCIA_INSUFICIENTE_REINTENTA",
    "EVIDENCIA_INSUFICIENTE_SEPARA_PROCEDENCIA", "EXCLUSIONES_NO_JUSTIFICADAS",
    "EXPEDIENTES_CON_FECHAS_INCONSISTENTES", "FECHAS_CORREGIDAS_DESDE_EL_REGISTRO",
    "FILTRO_SIN_COINCIDENCIAS", "INSTRUCCION_LISTADO", "LISTADO_CANONICO",
    "LISTADO_CANONICO_COUNT", "NO_CALCULABLES", "PROCEDENCIA_DE_LAS_FECHAS",
    "REGISTROS_SIN_IDENTIDAD_DESCARTADOS", "REQUISITOS_INCUMPLIDOS",
    "agent-search", "vector-search",
}


@lru_cache(maxsize=1)
def terminos_internos() -> frozenset:
    from models.schemas import ExpedienteRecord
    from agent.tools import TOOLS

    terminos = set(_CLAVES_DEL_PAYLOAD)
    for nombre, campo in ExpedienteRecord.model_fields.items():
        for t in (nombre, campo.alias):
            if t and t not in _CAMPOS_PALABRA:
                terminos.add(t)

    def parametros(esquema):
        if not isinstance(esquema, dict):
            return
        for k, v in (esquema.get("properties") or {}).items():
            if "_" in k:
                terminos.add(k)
            parametros(v)
        parametros(esquema.get("items"))

    for t in TOOLS:
        f = t.get("function", t)
        terminos.add(f["name"])
        parametros(f.get("parameters"))
    return frozenset(terminos)


@lru_cache(maxsize=1)
def _patron() -> re.Pattern:
    # Más largos primero, para que `ADVERTENCIA_COBERTURA_PARCIAL` no se lea
    # como `ADVERTENCIA_COBERTURA`. Frontera: ni letra, ni dígito, ni `_`/`-`.
    alternativas = "|".join(
        re.escape(t) for t in sorted(terminos_internos(), key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternativas})(?![\w-])")


def expuestos(texto: str) -> list[str]:
    """Identificadores internos que aparecen en la respuesta, sin repetir."""
    if not texto:
        return []
    return sorted(set(_patron().findall(texto)))
