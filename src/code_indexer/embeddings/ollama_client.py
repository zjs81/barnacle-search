import logging
from typing import Any, Optional

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
        self._client: Optional[Any] = None
        self._ollama_mod: Optional[Any] = None

    def _load_ollama(self):
        if self._ollama_mod is not None:
            return self._ollama_mod
        try:
            import ollama as ollama_mod
        except ImportError as exc:
            raise RuntimeError(
                "The official Ollama Python package is not installed. Run: pip install ollama"
            ) from exc
        self._ollama_mod = ollama_mod
        return ollama_mod

    async def _get_client(self):
        if self._client is None:
            ollama_mod = self._load_ollama()
            self._client = ollama_mod.AsyncClient(host=self.base_url)
        return self._client

    def _response_embeddings(self, response: Any) -> Optional[list[list[float]]]:
        embeddings = None
        if isinstance(response, dict):
            embeddings = response.get("embeddings")
            if embeddings is None and response.get("embedding") is not None:
                embeddings = [response["embedding"]]
        else:
            embeddings = getattr(response, "embeddings", None)
            if embeddings is None:
                single = getattr(response, "embedding", None)
                if single is not None:
                    embeddings = [single]

        if not isinstance(embeddings, list) or len(embeddings) == 0:
            return None

        return [[float(v) for v in vec] for vec in embeddings]

    def _response_model_names(self, response: Any) -> list[str]:
        if isinstance(response, dict):
            models = response.get("models", [])
        else:
            models = getattr(response, "models", [])

        names: list[str] = []
        for model in models or []:
            if isinstance(model, dict):
                name = model.get("model") or model.get("name")
            else:
                name = getattr(model, "model", None) or getattr(model, "name", None)
            if name:
                names.append(str(name))
        return names

    def _response_error_detail(self, exc: Exception) -> str:
        detail = str(exc)
        error = getattr(exc, "error", None)
        if error:
            detail = str(error)
        return detail

    async def embed(self, text: str) -> Optional[list[float]]:
        result = await self.embed_batch([text])
        if result is None or len(result) == 0:
            return None
        return result[0]

    async def pull_model(self, model: Optional[str] = None) -> bool:
        model = model or self.model
        logger.info("Pulling Ollama model '%s' via ollama-python...", model)
        try:
            client = await self._get_client()
            stream = await client.pull(model=model, stream=True)
            async for update in stream:
                if isinstance(update, dict):
                    status = update.get("status", "")
                    err = update.get("error")
                else:
                    status = getattr(update, "status", "")
                    err = getattr(update, "error", None)
                if err:
                    logger.error("Ollama pull error: %s", err)
                    return False
                if status:
                    logger.info("Ollama pull [%s]: %s", model, status)
            logger.info("Ollama model '%s' pulled successfully.", model)
            return True
        except Exception as exc:
            logger.error("Ollama pull failed: %s", self._response_error_detail(exc))
            return False

    async def embed_batch(
        self, texts: list[str], _retry: bool = True
    ) -> Optional[list[list[float]]]:
        if not texts:
            return []

        ollama_mod = self._load_ollama()
        client = await self._get_client()
        try:
            response = await client.embed(model=self.model, input=texts)
            embeddings = self._response_embeddings(response)
            if embeddings is None:
                logger.warning("Ollama embed: unexpected response structure")
                return None
            return embeddings
        except ollama_mod.ResponseError as exc:
            err_msg = self._response_error_detail(exc)
            status_code = getattr(exc, "status_code", None)
            if status_code == 404 and "not found" in err_msg.lower():
                if _retry:
                    logger.info("Model '%s' not found; attempting auto-pull...", self.model)
                    pulled = await self.pull_model()
                    if pulled:
                        return await self.embed_batch(texts, _retry=False)
                raise ModelNotFoundError(self.model, err_msg) from exc
            logger.warning("Ollama embed error %s: %s", status_code, err_msg)
            return None
        except Exception as exc:
            logger.warning("Ollama embed request failed: %s", self._response_error_detail(exc))
            return None

    async def is_available(self) -> bool:
        try:
            client = await self._get_client()
            await client.list()
            return True
        except Exception as exc:
            logger.warning("Ollama availability check failed: %s", self._response_error_detail(exc))
            return False

    async def list_models(self) -> list[str]:
        try:
            client = await self._get_client()
            response = await client.list()
            return self._response_model_names(response)
        except Exception as exc:
            logger.warning("Ollama list_models failed: %s", self._response_error_detail(exc))
            return []
