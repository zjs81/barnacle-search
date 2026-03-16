import httpx
import logging
from typing import Optional
from ..constants import OLLAMA_BASE_URL, EMBED_MODEL

logger = logging.getLogger(__name__)


class ModelNotFoundError(Exception):
    """Raised when the requested Ollama embedding model is not pulled locally."""
    def __init__(self, model: str, detail: str = ""):
        self.model = model
        super().__init__(
            f"Ollama model '{model}' not found. Run: ollama pull {model}"
            + (f"\nDetail: {detail}" if detail else "")
        )


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = EMBED_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def embed(self, text: str) -> Optional[list[float]]:
        """
        Generate embedding for a single text string.
        Uses POST /api/embed (current endpoint) with {"model": ..., "input": ...}
        Response: {"embeddings": [[...floats...]]}  — index [0] for single input.
        Returns None on any error (Ollama not running, model not pulled, etc.).
        """
        result = await self.embed_batch([text])
        if result is None or len(result) == 0:
            return None
        return result[0]

    async def pull_model(self, model: Optional[str] = None) -> bool:
        """
        Pull a model from Ollama. Streams progress and returns True on success.
        Uses POST /api/pull with stream=True; each line is a JSON status object.
        """
        model = model or self.model
        url = f"{self.base_url}/api/pull"
        logger.info("Pulling Ollama model '%s' — this may take a few minutes...", model)
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream("POST", url, json={"model": model, "stream": True}) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            import json as _json
                            obj = _json.loads(line)
                            status = obj.get("status", "")
                            if "error" in obj:
                                logger.error("Ollama pull error: %s", obj["error"])
                                return False
                            if status:
                                logger.info("Ollama pull [%s]: %s", model, status)
                        except Exception:
                            pass
            logger.info("Ollama model '%s' pulled successfully.", model)
            return True
        except httpx.HTTPStatusError as exc:
            logger.error("Ollama pull HTTP error %s: %s", exc.response.status_code, exc.response.text[:200])
            return False
        except httpx.RequestError as exc:
            logger.error("Ollama pull request error: %s", exc)
            return False
        except Exception as exc:
            logger.error("Ollama pull unexpected error: %s", exc)
            return False

    async def embed_concurrent(
        self,
        texts: list[str],
        batch_size: int = 8,
        concurrency: int = 4,
    ) -> Optional[list[list[float]]]:
        """
        Embed a large list of texts using small concurrent batches.

        Sends `concurrency` requests in parallel, each with `batch_size` inputs.
        - batch_size ≤ 8 avoids Ollama's quality-degradation bug (issue #6262)
        - concurrency requires OLLAMA_NUM_PARALLEL ≥ concurrency on the Ollama server

        Returns list of vectors in same order as input, or raises ModelNotFoundError.
        """
        import asyncio

        if not texts:
            return []

        # Split into small batches
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        results: list[Optional[list[list[float]]]] = [None] * len(batches)

        # Process with a semaphore to cap concurrency
        sem = asyncio.Semaphore(concurrency)

        async def _do_batch(idx: int, batch: list[str]):
            async with sem:
                results[idx] = await self.embed_batch(batch)

        await asyncio.gather(*[_do_batch(i, b) for i, b in enumerate(batches)])

        # Flatten in order
        out: list[list[float]] = []
        for i, vecs in enumerate(results):
            if vecs is None:
                logger.warning("Batch %d failed — embedding quality may be incomplete", i)
                # Insert zero vectors as placeholders to preserve index alignment
                out.extend([[0.0]] * len(batches[i]))
            else:
                out.extend(vecs)
        return out

    async def embed_batch(self, texts: list[str], _retry: bool = True) -> Optional[list[list[float]]]:
        """
        Generate embeddings for multiple texts in a single API call.
        If the model is not found, automatically pulls it and retries once.
        Returns list of vectors in same order as input, or None on error.
        """
        if not texts:
            return []
        url = f"{self.base_url}/api/embed"
        payload = {"model": self.model, "input": texts}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                embeddings = data.get("embeddings")
                if not isinstance(embeddings, list) or len(embeddings) == 0:
                    logger.warning("Ollama embed: unexpected response structure: %s", list(data.keys()))
                    return None
                return [[float(v) for v in vec] for vec in embeddings]
        except httpx.TimeoutException as exc:
            logger.warning("Ollama embed timed out: %s", exc)
            return None
        except httpx.HTTPStatusError as exc:
            try:
                err_msg = exc.response.json().get("error", exc.response.text[:200])
            except Exception:
                err_msg = exc.response.text[:200]
            if exc.response.status_code == 404 and "not found" in err_msg.lower():
                if _retry:
                    logger.info("Model '%s' not found — attempting auto-pull...", self.model)
                    pulled = await self.pull_model()
                    if pulled:
                        return await self.embed_batch(texts, _retry=False)
                raise ModelNotFoundError(self.model, err_msg) from exc
            logger.warning("Ollama embed HTTP error %s: %s", exc.response.status_code, err_msg)
            return None
        except httpx.RequestError as exc:
            logger.warning("Ollama embed request error: %s", exc)
            return None
        except ModelNotFoundError:
            raise
        except (ValueError, KeyError) as exc:
            logger.warning("Ollama embed JSON parse error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Ollama embed unexpected error: %s", exc)
            return None

    async def is_available(self) -> bool:
        """Check if Ollama is running. GET /api/tags, returns True if 200."""
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                return response.status_code == 200
        except httpx.TimeoutException as exc:
            logger.warning("Ollama availability check timed out: %s", exc)
            return False
        except httpx.RequestError as exc:
            logger.warning("Ollama availability check failed: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Ollama availability check unexpected error: %s", exc)
            return False

    async def list_models(self) -> list[str]:
        """Return list of available model names from GET /api/tags."""
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                models = data.get("models", [])
                return [m["name"] for m in models if isinstance(m, dict) and "name" in m]
        except httpx.TimeoutException as exc:
            logger.warning("Ollama list_models timed out: %s", exc)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning("Ollama list_models HTTP error %s: %s", exc.response.status_code, exc)
            return []
        except httpx.RequestError as exc:
            logger.warning("Ollama list_models request error: %s", exc)
            return []
        except (ValueError, KeyError) as exc:
            logger.warning("Ollama list_models JSON parse error: %s", exc)
            return []
        except Exception as exc:
            logger.warning("Ollama list_models unexpected error: %s", exc)
            return []
