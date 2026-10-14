import httpx
import pytest
from fastapi.testclient import TestClient

from app.embeddings import EmbeddingError, Settings, embed
from app.main import create_app


def config():
    return Settings(_env_file=None, embedding_api_key="test-key", ai_internal_token="test-token")


@pytest.mark.asyncio
async def test_order_and_request():
    def handler(request):
        assert request.url == "https://api.siliconflow.cn/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [2.] * 1024}, {"index": 0, "embedding": [1.] * 1024}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await embed(["文章", "问题"], config(), client))[0][0] == 1.


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"index": 0, "embedding": [1.]}], [{"index": 0, "embedding": [0.] * 1024}], [{"index": True, "embedding": [1.] * 1024}]])
async def test_invalid_response(rows):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": rows}))) as client:
        with pytest.raises(EmbeddingError):
            await embed(["测试"], config(), client)


@pytest.mark.asyncio
async def test_no_secret_in_errors():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret-provider-body"))) as client:
        with pytest.raises(EmbeddingError) as error:
            await embed(["测试"], config(), client)
        assert "secret" not in str(error.value) and "test-key" not in str(error.value)


def test_health_and_internal_auth():
    with TestClient(create_app(config())) as client:
        assert client.get("/healthz").json()["rag_ready"] is False
        assert client.post("/internal/embeddings", json={"texts": ["测试"]}).status_code == 401


@pytest.mark.asyncio
async def test_empty_input_never_calls_provider():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("must not call"))) as client:
        with pytest.raises(EmbeddingError):
            await embed([" "], config(), client)
