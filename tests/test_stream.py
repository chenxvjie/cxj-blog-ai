import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.deepseek import ModelError, generate
from app.embeddings import Settings
from app.rag import build_graph


def settings():
    return Settings(_env_file=None, embedding_api_key='test', deepseek_api_key='test')


class Chunks(httpx.AsyncByteStream):
    def __init__(self, answer='答案[S1]', complete=True):
        self.answer, self.complete, self.closed = answer, complete, False

    async def __aiter__(self):
        for text in [self.answer[:2], self.answer[2:]]:
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': text}}]}) + '\n\n').encode()
            await asyncio.sleep(0)
        if self.complete:
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b'data: {"choices":[],"usage":{"total_tokens":8}}\n\n'
            yield b'data: [DONE]\n\n'

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_real_deltas_precede_validated_final_state():
    store = AsyncMock()
    store.retrieve.return_value = [{'post_id': 1, 'revision': 1, 'title': '文章', 'heading': '', 'content': '事实'}]
    store.validate_sources.return_value = True
    stream = Chunks()
    def handler(request):
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.] * 1024}]})
        assert json.loads(request.content)['stream'] is True
        return httpx.Response(200, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [e async for e in build_graph(settings(), store, client).astream({'question': '问题', 'streaming': True}, stream_mode=['custom', 'values'])]
    delta_positions = [i for i, (mode, _) in enumerate(events) if mode == 'custom']
    assert ''.join(events[i][1]['text'] for i in delta_positions) == '答案[S1]'
    assert max(delta_positions) < len(events) - 1
    assert events[-1][1]['grounded'] is True
    assert stream.closed


@pytest.mark.asyncio
async def test_truncated_stream_never_succeeds_and_closes_upstream():
    stream = Chunks(complete=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream))) as client:
        with pytest.raises(ModelError, match='提前中断'):
            await generate('问题', [], settings(), client, on_delta=lambda d: None)
    assert stream.closed


@pytest.mark.asyncio
async def test_invalid_streamed_citation_never_reaches_grounded_state():
    store = AsyncMock()
    store.retrieve.return_value = [{'post_id': 1, 'revision': 1, 'title': '文章', 'heading': '', 'content': '事实'}]
    def handler(request):
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.] * 1024}]})
        return httpx.Response(200, stream=Chunks('错误[S9]'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelError, match='引用校验'):
            async for mode, value in build_graph(settings(), store, client).astream({'question': '问题', 'streaming': True}, stream_mode=['custom', 'values']):
                assert not (mode == 'values' and value.get('grounded'))
