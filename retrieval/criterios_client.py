"""
Cliente HTTP para el endpoint de búsqueda vectorial en criterios.
Consume el endpoint real de José Miguel:
POST https://norma-api-xxx.run.app/paragraphs/vector-search

Autenticación: header x-api-key con formato {chatbot_id}.{key}
Request: { question, limit, caseLink?, authority?, ... }
Response: array de { id, content, metadata, distance }
"""
import logging
import httpx
from models.schemas import CriterioResult

logger = logging.getLogger(__name__)


class CriteriosSearchClient:

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def search(
        self,
        query: str,
        top_k: int = 15,
        max_distance: float = 0.7,
        filters: dict | None = None,
        collector=None,
    ) -> list[CriterioResult]:
        """
        Búsqueda vectorial en criterios vía endpoint de José Miguel.

        Args:
            query: Texto de búsqueda (campo 'question' en la API)
            top_k: Máximo de resultados (campo 'limit' en la API)
            max_distance: Filtrar resultados con distancia mayor a este valor
                          (menor distancia = mayor similitud)
            filters: Filtros opcionales con nombres de la API real:
                     caseLink, authority, typeOfProcedure,
                     senseOfResolution, economicAgents, etc.
            collector: TraceCollector opcional. Si viene, se registran las
                       etapas de retrieval. No altera el resultado.
        """
        payload = {
            "question": query,
            "limit": top_k,
        }

        # Agregar filtros opcionales (mapeo nombre interno → nombre API)
        if filters:
            filter_map = {
                "id_expediente": "caseLink",
                "caseLink": "caseLink",
                "autoridad": "authority",
                "authority": "authority",
                "tipo_procedimiento": "typeOfProcedure",
                "typeOfProcedure": "typeOfProcedure",
                "sentido_resolucion": "senseOfResolution",
                "senseOfResolution": "senseOfResolution",
                "agentes_economicos": "economicAgents",
                "economicAgents": "economicAgents",
                "mercados_relevantes": "relevantMarkets",
                "relevantMarkets": "relevantMarkets",
            }
            for key, value in filters.items():
                api_key_name = filter_map.get(key, key)
                if value is not None:
                    payload[api_key_name] = value

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        logger.debug(f"Buscando criterios: question='{query}', limit={top_k}")

        url = f"{self.base_url}/paragraphs/vector-search"
        if collector is not None:
            collector.record_http_request("POST", url, body=payload)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        # La API retorna un array directo
        items = data if isinstance(data, list) else data.get("results", data)

        # Etapa 1 — lo que devolvió la API, crudo, antes de tocar nada.
        if collector is not None:
            collector.record_stage(
                stage="candidates",
                method="vector_search:/paragraphs/vector-search",
                docs=list(items),
                notes=f"limit={top_k} enviado a la API.",
            )

        results = []
        dropped_ids: list[str] = []
        for item in items:
            distance = item.get("distance", 1.0)

            # Filtrar por distancia máxima (menor = más similar)
            if distance > max_distance:
                dropped_ids.append(str(item.get("id", "")))
                continue

            # Convertir distance a score (1 - distance)
            score = max(0, 1.0 - distance)

            metadata_raw = item.get("metadata", {})
            pdf_pages = metadata_raw.get("pdf_pages", [])
            resolution_pages = metadata_raw.get("resolution_pages", [])

            # Campos nuevos: José Miguel los puso fuera de metadata,
            # como campos de primer nivel en la respuesta.
            # caseName, caseLink → strings
            # articleNames, titleNames → arrays de strings (pueden ser [] vacíos)
            id_expediente = item.get("caseLink", "")
            case_name = item.get("caseName", "")
            article_names = item.get("articleNames") or []  # None → []
            title_names = item.get("titleNames") or []      # None → []

            # Título: usar el primer titleName si existe, sino fallback a anchor
            anchor = metadata_raw.get("anchor", "")
            title = title_names[0] if title_names else (anchor[:120] if anchor else "")

            # Artículo: unir articleNames en string
            article = " | ".join(article_names) if article_names else ""

            results.append(CriterioResult(
                id=str(item.get("id", "")),
                text=item.get("content", ""),
                score=round(score, 4),
                metadata={
                    # Metadata original — José Miguel confirmó que sí la retorna.
                    # grounding tiene coordenadas de bounding box para fase 2.
                    "anchor": anchor,
                    "context": metadata_raw.get("context", ""),
                    "grounding": metadata_raw.get("grounding"),
                    "pdf_pages": pdf_pages,
                    "resolution_pages": resolution_pages,
                    # Campos de primer nivel mapeados a nuestro schema interno
                    "id_expediente": id_expediente,
                    "caseLink": id_expediente,
                    "nombre_expediente": case_name,
                    "title": title,
                    "titleNames": title_names,       # array completo
                    "articleNames": article_names,   # array completo
                    "article": article,
                    "paginas_parrafos": self._format_pages(metadata_raw),
                    "distance": distance,
                },
            ))

        results.sort(key=lambda r: r.score, reverse=True)

        # Etapa 2 — tras filtro por distancia y ordenamiento por score.
        # No hay reranker: esta etapa es filtro + orden, y así se declara.
        if collector is not None:
            collector.record_stage(
                stage="after_ranking",
                method=f"distance_filter(<{max_distance})+sort_by_score",
                docs=[
                    {"id": r.id, "text": r.text, "score": r.score,
                     "metadata": r.metadata}
                    for r in results
                ],
                dropped_ids=dropped_ids,
                notes=(
                    "No hay reranker en el pipeline. Esta etapa descarta por "
                    "distancia y reordena por score."
                ),
            )
            collector.record_coverage(
                total_available=None,
                requested_limit=top_k,
                returned=len(results),
                truncated=len(items) >= top_k,
                truncation_reason="top_k" if len(items) >= top_k else None,
            )

        logger.debug(
            f"Criterios encontrados: {len(results)} "
            f"(de {len(items)} totales, filtrados por distance < {max_distance})"
        )
        return results

    def _format_pages(self, metadata: dict) -> str:
        """Formatea las páginas del PDF/resolución."""
        pages = metadata.get("resolution_pages") or metadata.get("pdf_pages") or []
        if pages:
            return ", ".join(str(p) for p in pages)
        return ""
