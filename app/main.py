import asyncio
import json
import secrets
from contextlib import asynccontextmanager, suppress
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.deepseek import ModelError
from app.embeddings import EmbeddingError, Settings, embed
from app.indexing import index_document
from app.jobs import Jobs, worker_loop
from app.rag import build_graph
from app.store import Store, StoreError


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=16)


class DocumentRequest(BaseModel):
    post_id: int = Field(gt=0)
    revision: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=r'^[A-Za-z0-9_-]{1,240}$')
    content_md: str = Field(max_length=100000)
    status: Literal['PUBLISHED', 'DRAFT', 'OFFLINE', 'DELETED'] = 'PUBLISHED'


class Turn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=8000)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    post_id: int | None = Field(default=None, gt=0)
    history: list[Turn] = Field(default_factory=list, max_length=3)

    @field_validator('question')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('问题不能为空')
        return value.strip()


def create_app(settings: Settings | None = None):
    config = settings or Settings()
    store = Store(config)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            app.state.client = client
            app.state.graph = build_graph(config, store, client)
            app.state.capacity = asyncio.Semaphore(2)
            worker = asyncio.create_task(worker_loop(config, client)) if config.index_worker_enabled else None
            try:
                yield
            finally:
                if worker:
                    worker.cancel()
                    with suppress(asyncio.CancelledError):
                        await worker

    app = FastAPI(title='CXJ Blog AI', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def authorize(authorization: str = Header(default='')):
        token = config.ai_internal_token.get_secret_value()
        if not token:
            raise HTTPException(503, '内部服务认证尚未配置')
        if not secrets.compare_digest(authorization.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(401, '内部服务认证失败')

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'stage': 'rag', 'rag_ready': False}

    @app.get('/internal/readyz', dependencies=[Depends(authorize)])
    async def ready():
        configured = config.configured() and bool(config.deepseek_api_key.get_secret_value())
        try:
            database = await store.health()
        except StoreError:
            database = False
        if not configured or not database:
            raise HTTPException(503, {'models_configured': configured, 'database_ready': database})
        return {'ready': True, 'external_models_probed': False}

    @app.post('/internal/embeddings', dependencies=[Depends(authorize)])
    async def embeddings(body: EmbeddingRequest):
        try:
            async with app.state.capacity:
                vectors = await embed(body.texts, config, app.state.client)
        except EmbeddingError as error:
            raise HTTPException(502, str(error)) from None
        return {'model': config.embedding_model, 'dimensions': 1024, 'vectors': vectors}

    @app.put('/internal/documents', dependencies=[Depends(authorize)])
    async def documents(body: DocumentRequest):
        try:
            async with app.state.capacity:
                return await index_document(body, config, store, app.state.client)
        except StoreError as error:
            raise HTTPException(409, str(error)) from None
        except EmbeddingError as error:
            raise HTTPException(502, str(error)) from None

    @app.get('/internal/index/jobs', dependencies=[Depends(authorize)])
    async def index_jobs():
        return await Jobs(config).list_jobs()

    @app.post('/internal/index/jobs/{post_id}/retry', dependencies=[Depends(authorize)])
    async def retry_index(post_id: int):
        try:
            await Jobs(config).retry(post_id)
        except StoreError as error:
            raise HTTPException(409, str(error)) from None
        return {'queued': True}

    @app.post('/internal/ask', dependencies=[Depends(authorize)])
    async def ask(body: QuestionRequest):
        try:
            async with app.state.capacity:
                result = await app.state.graph.ainvoke({'question': body.question, 'post_id': body.post_id, 'history': [turn.model_dump() for turn in body.history]}, config={'recursion_limit': 12})
        except (EmbeddingError, ModelError) as error:
            raise HTTPException(502, str(error)) from None
        except StoreError as error:
            raise HTTPException(503, str(error)) from None
        return answer_payload(result)

    def answer_payload(result):
        sources = [{'id': f'S{i+1}', 'post_id': source['post_id'], 'revision': source['revision'],
                    'title': source['title'], 'heading': source['heading'], 'excerpt': source['content'],
                    'url': config.public_blog_url.rstrip('/') + '/posts/' + source['slug']}
                   for i, source in enumerate(result['sources'])]
        return {'answer': result['answer'], 'sources': sources, 'grounded': result['grounded'], 'usage': result['usage']}


    @app.post('/internal/ask/stream', dependencies=[Depends(authorize)])
    async def ask_stream(body: QuestionRequest):
        def event(name, data):
            return 'event: ' + name + '\ndata: ' + json.dumps(data, ensure_ascii=False) + '\n\n'

        async def events():
            # One pending read keeps the graph running across heartbeat timeouts.
            iterator, pending = None, None
            try:
                yield event('status', {'message': '正在检索文章…'})
                async with app.state.capacity:
                    iterator = app.state.graph.astream(
                        {'question': body.question, 'post_id': body.post_id, 'history': [t.model_dump() for t in body.history], 'streaming': True},
                        config={'recursion_limit': 12}, stream_mode=['custom', 'values']).__aiter__()
                    result = None
                    while True:
                        if pending is None:
                            pending = asyncio.create_task(anext(iterator))
                        done, _ = await asyncio.wait({pending}, timeout=1)
                        if not done:
                            yield event('heartbeat', {})
                            continue
                        try:
                            mode, value = pending.result()
                        except StopAsyncIteration:
                            break
                        finally:
                            pending = None
                        if mode == 'custom':
                            yield event('delta', value)
                        else:
                            result = value
                    if result is None or 'grounded' not in result:
                        raise ModelError('回答未完成')
                    yield event('done', answer_payload(result))
            except (EmbeddingError, ModelError, StoreError):
                yield event('error', {'message': '回答生成或引用校验失败，本次未扣次数，请重试'})
            except Exception:  # noqa: BLE001 - never expose provider payloads
                yield event('error', {'message': 'AI服务暂不可用，本次未扣次数，请重试'})
            finally:
                if pending:
                    pending.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await pending
                if iterator:
                    await iterator.aclose()

        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})

    return app


app = create_app()
