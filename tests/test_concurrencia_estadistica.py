"""
El estado de la última consulta (`last_total`, `last_truncado`, …) es por
petición. El cliente es uno solo para todo el servicio; como atributo normal,
dos consultas simultáneas se pisaban el total entre la búsqueda y su lectura.
"""
import asyncio

from retrieval.estadistica_client import EstadisticaSearchClient


def test_dos_peticiones_simultaneas_no_se_pisan_el_total():
    cliente = EstadisticaSearchClient(base_url="http://x", api_key="k")

    async def peticion(total, espera):
        cliente.last_total = total
        cliente.last_truncado = total > 10
        await asyncio.sleep(espera)  # otra petición corre aquí
        return cliente.last_total, cliente.last_truncado

    async def ambas():
        return await asyncio.gather(peticion(5, 0.02), peticion(500, 0.01))

    assert asyncio.run(ambas()) == [(5, False), (500, True)]


def test_dentro_de_una_peticion_se_lee_lo_que_se_escribio():
    cliente = EstadisticaSearchClient(base_url="http://x", api_key="k")

    async def una():
        assert cliente.last_total is None and cliente.last_pages_fetched == 1
        cliente.last_total = 38
        await asyncio.sleep(0)
        return cliente.last_total

    assert asyncio.run(una()) == 38
