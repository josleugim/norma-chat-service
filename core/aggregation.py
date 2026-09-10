"""
Agregaciones deterministas sobre el universo de expedientes.

COFECE, adjudicación de v1.3: "cuando una pregunta exija revisar un universo
completo, no debe depender de un top-k semántico ni de que el LLM reconstruya
el universo a partir de una muestra".

El problema medido: ante "¿cuál es el plazo menor?" sobre 4,588 CNT, el agente
procesó 15 casos y afirmó un mínimo global. Ante "¿la multa máxima?", 3
documentos frente a 1,838 con multas.

La respuesta no es un prompt mejor: un máximo se calcula, no se estima. Estas
funciones recorren el universo elegible y devuelven el resultado **con la
cuenta de lo procesado**, para que la afirmación de exhaustividad sea
verificable en vez de confiada.
"""
import json
import re
from typing import Any, Callable, Optional

# Los montos vienen como texto con formato contable dentro de agentFines.
_MONTO_RE = re.compile(r"[\d][\d,\.]*")


def parse_multas(valor: Any) -> dict[str, float]:
    """
    Extrae {agente: monto} de `agentFines`, que llega como dict o como string
    con un dict adentro. Los valores no numéricos —"CONFIDENCIAL", "N/A"— se
    omiten y se reportan aparte: no son cero, son desconocidos.
    """
    if not valor:
        return {}
    datos = valor
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto or texto in ("{}", "None", "null"):
            return {}
        try:
            datos = json.loads(texto.replace("'", '"'))
        except Exception:
            datos = None
            if ":" in texto:
                datos = {}
                for parte in texto.strip("{}").split(","):
                    if ":" in parte:
                        k, v = parte.split(":", 1)
                        datos[k.strip().strip("'\"")] = v.strip().strip("'\"")
            if datos is None:
                return {}
    if not isinstance(datos, dict):
        return {}

    salida = {}
    for agente, monto in datos.items():
        numero = _a_numero(monto)
        if numero is not None:
            salida[str(agente)] = numero
    return salida


def parse_monto(valor: Any) -> dict:
    """
    Convierte un monto legacy a número, o lo rechaza si es ambiguo.

    Regla de COFECE, textual: *"si el dato puede convertirse de forma
    determinista a un único valor, utilizarlo; si requiere adivinar qué quiso
    decir el dato, no utilizarlo"*.

    El caso que lo motivó: `$1,400,000,00`. El parser anterior eliminaba las
    comas y devolvía **140,000,000** — unas cien veces el valor probable— y con
    eso el agente reportó una multa máxima falsa. No se sabe si esa última coma
    es decimal (1,400,000.00) o de millar (1,400,00,000 mal escrito), así que
    la respuesta correcta es no usarlo, no adivinar.

    Retorna {status, value, raw, reason} con status:
      valid       → convertible sin ambigüedad
      ambiguous   → formato dudoso; NO usar en cálculos
      non_numeric → confidencial, N/D, vacío
    """
    crudo = valor
    if isinstance(valor, (int, float)):
        return {"status": "valid", "value": float(valor), "raw": crudo, "reason": None}
    if not isinstance(valor, str):
        return {"status": "non_numeric", "value": None, "raw": crudo,
                "reason": "tipo no numérico"}

    texto = valor.strip()
    if not texto:
        return {"status": "non_numeric", "value": None, "raw": crudo,
                "reason": "vacío"}
    if any(p in texto.upper() for p in ("CONFIDENCIAL", "RESERVAD", "N/A", "N.D.", "N/D")):
        return {"status": "non_numeric", "value": None, "raw": crudo,
                "reason": "valor reservado o no disponible"}

    match = _MONTO_RE.search(texto.replace(" ", "").replace("$", ""))
    if not match:
        return {"status": "non_numeric", "value": None, "raw": crudo,
                "reason": "sin dígitos reconocibles"}

    numero = match.group(0)

    # Formato canónico: miles con coma y exactamente dos decimales con punto.
    if re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d{1,2})?", numero) or \
            re.fullmatch(r"\d+(\.\d{1,2})?", numero):
        try:
            return {"status": "valid", "value": float(numero.replace(",", "")),
                    "raw": crudo, "reason": None}
        except ValueError:
            pass

    # Todo lo demás es ambiguo: grupos de coma irregulares (`1,400,000,00`),
    # mezcla de separadores, o más de dos decimales.
    return {
        "status": "ambiguous",
        "value": None,
        "raw": crudo,
        "reason": (
            "formato ambiguo: no se puede determinar si los separadores son "
            "de millar o decimales sin adivinar"
        ),
    }


def _a_numero(valor: Any) -> Optional[float]:
    """Compatibilidad: solo devuelve el valor cuando es inequívoco."""
    return parse_monto(valor).get("value")


def tiene_multa_no_numerica(valor: Any) -> bool:
    """
    True si hay multas pero al menos una no es un número —confidencial,
    reservada—. Un máximo calculado sobre el resto no puede afirmarse como
    global, y hay que decirlo.
    """
    if not valor:
        return False
    texto = str(valor).upper()
    return any(p in texto for p in ("CONFIDENCIAL", "RESERVAD", "N/A", "N.D."))


def agregar(
    registros: list[dict],
    operacion: str,
    extraer: Callable[[dict], Optional[float]],
    top: int = 5,
) -> dict:
    """
    Aplica max/min/suma/promedio/conteo sobre un universo ya recuperado.

    Devuelve siempre `procesados`, `con_valor` y `sin_valor`: sin esos números
    una afirmación de máximo o mínimo no es verificable.
    """
    con_valor, sin_valor = [], []
    for r in registros:
        v = extraer(r)
        (con_valor if v is not None else sin_valor).append((v, r))

    resultado = {
        "operacion": operacion,
        "procesados": len(registros),
        "con_valor": len(con_valor),
        "sin_valor": len(sin_valor),
    }
    if not con_valor:
        resultado["resultado"] = None
        resultado["nota"] = "Ningún registro del universo tiene el valor pedido."
        return resultado

    con_valor.sort(key=lambda par: par[0], reverse=(operacion == "max"))
    valores = [v for v, _ in con_valor]

    if operacion in ("max", "min"):
        resultado["resultado"] = valores[0]
        resultado["ganadores"] = [
            {"valor": v, **_resumen(r)} for v, r in con_valor[:top]
        ]
    elif operacion == "suma":
        resultado["resultado"] = sum(valores)
    elif operacion == "promedio":
        resultado["resultado"] = round(sum(valores) / len(valores), 2)
        resultado["mediana"] = sorted(valores)[len(valores) // 2]
    elif operacion == "conteo":
        resultado["resultado"] = len(con_valor)

    return resultado


def _resumen(registro: dict) -> dict:
    return {
        "case_link": registro.get("caseLink") or registro.get("id_expediente", ""),
        "authority": registro.get("authority"),
        "senseOfResolution": registro.get("senseOfResolution"),
        "resolutionDate": registro.get("resolutionDate"),
    }


def render_listado(registros: list[dict]) -> str:
    """
    Lista canónica, ya numerada y sin duplicados.

    COFECE: "no pedir al LLM que reconstruya el conjunto desde contexto
    truncado; renderizar/serializar determinísticamente los resultados". En
    v1.3 el agente recuperó 36 expedientes, enumeró 36 renglones y solo tenía
    34 únicos, con dos duplicados y una omisión. Esta cadena se le entrega ya
    hecha para que la reproduzca en vez de rearmarla.
    """
    vistos, filas = set(), []
    for r in registros:
        link = r.get("caseLink") or r.get("id_expediente", "")
        if not link or link in vistos:
            continue
        vistos.add(link)
        partes = [link]
        if r.get("name"):
            partes.append(str(r["name"]))
        if r.get("senseOfResolution"):
            partes.append(str(r["senseOfResolution"]))
        if r.get("resolutionDate"):
            partes.append(str(r["resolutionDate"]))
        filas.append(f"{len(filas) + 1}. " + " | ".join(partes))
    return "\n".join(filas)
