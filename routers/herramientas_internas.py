"""
Herramientas del conector, para el servidor MCP que vive en otro repositorio.

El servidor MCP de producción (OAuth con cuentas de Norma+, mcp.normaplus.ai)
es un proyecto aparte. La lógica de las herramientas se queda aquí, junto al
agente: un arreglo en la búsqueda, la historia procesal o los plazos llega
igual al chat y al conector, y se mide una sola vez.

Dos rutas, ambas fuera del esquema público y protegidas con una llave interna
(`INTERNAL_TOOLS_KEY`). Sin llave configurada el router no se monta.

- `GET  /internal/tools`          catálogo: nombre, descripción, esquema de
                                  entrada y anotaciones de cada herramienta, más
                                  las instrucciones del servidor. El otro repo
                                  las registra tal cual; las descripciones
                                  medidas con el holdout viven en un solo lugar.
- `POST /internal/tools/{nombre}` cuerpo = argumentos. Devuelve el resultado en
                                  el formato de MCP (`CallToolResult`), listo
                                  para reenviarse sin traducir.

Un error de la herramienta (consulta fallida, expediente inexistente,
argumentos inválidos) no es un error HTTP: va como resultado con
`isError: true`, que es como MCP se lo muestra al modelo.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from mcp.server.mcpserver.exceptions import ToolError

from mcp_server.servidor import servidor

logger = logging.getLogger("herramientas_internas")


def _verificar_llave(request: Request, x_internal_key: str | None = Header(default=None)) -> None:
    esperada = request.app.state.settings.internal_tools_key
    if not esperada or not x_internal_key or not secrets.compare_digest(x_internal_key, esperada):
        raise HTTPException(status_code=401, detail="Llave interna inválida")


router = APIRouter(prefix="/internal", include_in_schema=False,
                   dependencies=[Depends(_verificar_llave)])


def _volcar(modelo) -> dict:
    return modelo.model_dump(mode="json", by_alias=True, exclude_none=True)


@router.get("/tools")
async def catalogo() -> dict:
    return {
        "servidor": {
            "name": servidor.name,
            "title": servidor.title,
            "instructions": servidor.instructions,
            "website_url": servidor.website_url,
        },
        "tools": [_volcar(t) for t in await servidor.list_tools()],
    }


@router.post("/tools/{nombre}")
async def ejecutar(nombre: str, argumentos: dict[str, Any] = Body(default_factory=dict),
                   x_usuario_ref: str | None = Header(default=None)) -> dict:
    if nombre not in {t.name for t in await servidor.list_tools()}:
        raise HTTPException(status_code=404, detail=f"Herramienta desconocida: {nombre}")
    inicio = time.monotonic()
    try:
        resultado = _volcar(await servidor.call_tool(nombre, argumentos))
    except ToolError as e:
        resultado = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    finally:
        logger.info("herramienta %s usuario=%s %d ms", nombre, x_usuario_ref or "-",
                    int((time.monotonic() - inicio) * 1000))
    return resultado
