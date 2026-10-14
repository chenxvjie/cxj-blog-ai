import math

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    embedding_provider: str = "siliconflow"
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimensions: int = 1024
    ai_internal_token: SecretStr = SecretStr("")

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-flash"
    database_url: SecretStr = SecretStr("")
    public_blog_url: str = "http://127.0.0.1:5173"
    min_similarity: float = 0.45
    index_worker_enabled: bool = False

    def configured(self) -> bool:
        return (self.embedding_provider == "siliconflow"
                and self.embedding_base_url.rstrip("/") == "https://api.siliconflow.cn/v1"
                and bool(self.embedding_api_key.get_secret_value().strip())
                and self.embedding_model == "BAAI/bge-m3"
                and self.embedding_dimensions == 1024)


class EmbeddingError(Exception):
    pass


async def embed(texts: list[str], settings: Settings, client: httpx.AsyncClient) -> list[list[float]]:
    if not settings.configured():
        raise EmbeddingError("向量服务配置不完整或不受支持")
    if not 1 <= len(texts) <= 16 or any(not t.strip() or len(t.encode("utf-8")) > 8000 for t in texts):
        raise EmbeddingError("每批1–16条非空文本，每条最多8000字节")
    try:
        response = await client.post(
            settings.embedding_base_url.rstrip("/") + "/embeddings",
            headers={"Authorization": "Bearer " + settings.embedding_api_key.get_secret_value().strip()},
            json={"model": settings.embedding_model, "input": texts, "encoding_format": "float"},
        )
        if response.status_code != 200:
            raise EmbeddingError(f"向量服务请求失败（HTTP {response.status_code}）")
        payload = response.json()
        rows = payload["data"]
        if len(rows) != len(texts):
            raise ValueError()
        ordered: dict[int, list[float]] = {}
        for row in rows:
            index, vector = row["index"], row["embedding"]
            if type(index) is not int or index not in range(len(texts)) or index in ordered:
                raise ValueError()
            if not isinstance(vector, list) or len(vector) != settings.embedding_dimensions:
                raise ValueError()
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
                raise ValueError()
            if not any(vector):
                raise ValueError()
            ordered[index] = vector
        return [ordered[i] for i in range(len(texts))]
    except httpx.HTTPError:
        raise EmbeddingError("向量服务连接失败或超时") from None
    except (ValueError, KeyError, TypeError, OverflowError):
        raise EmbeddingError("向量响应无效，已拒绝使用") from None
