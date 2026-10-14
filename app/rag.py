import re
from typing import TypedDict

import httpx
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.deepseek import ModelError, generate, rewrite
from app.embeddings import Settings, embed
from app.store import Store


class State(TypedDict, total=False):
    streaming: bool
    question: str
    post_id: int | None
    history: list[dict]
    query: str
    vector: list[float]
    sources: list[dict]
    answer: str
    usage: dict
    grounded: bool


def build_graph(settings: Settings, store: Store, client: httpx.AsyncClient):
    async def contextualize(state):
        return {'query': await rewrite(state['question'], state.get('history', []), settings, client)}

    async def embedding(state):
        return {'vector': (await embed([state['query']], settings, client))[0]}

    async def retrieve(state):
        return {'sources': await store.retrieve(state['query'], state['vector'], state.get('post_id'))}

    async def answer(state):
        writer = get_stream_writer() if state.get('streaming') else None
        text, usage = await generate(state['query'], state['sources'], settings, client, on_delta=writer)
        return {'answer': text, 'usage': usage}

    async def insufficient(state):
        return {'answer': '当前知识库暂无足够资料回答这个问题，请尝试更具体的问题。', 'grounded': False, 'usage': {}}

    async def validate(state):
        citations = re.findall(r'\[S(\d+)\]', state['answer'])
        if not citations or any(not 1 <= int(i) <= len(state['sources']) for i in citations):
            raise ModelError('回答引用校验失败，请重试')
        if re.search(r'https?://', state['answer']):
            raise ModelError('回答包含未经验证的网址，已拒绝返回')
        if not await store.validate_sources(state['sources']):
            raise ModelError('文章公开状态或版本已变化，请重新提问')
        return {'grounded': True}

    graph = StateGraph(State)
    graph.add_node('contextualize', contextualize)
    graph.add_node('embed', embedding)
    graph.add_node('retrieve', retrieve)
    graph.add_node('answer', answer)
    graph.add_node('insufficient', insufficient)
    graph.add_node('validate', validate)
    graph.add_edge(START, 'contextualize')
    graph.add_edge('contextualize', 'embed')
    graph.add_edge('embed', 'retrieve')
    graph.add_conditional_edges('retrieve', lambda s: 'answer' if s['sources'] else 'insufficient')
    graph.add_edge('answer', 'validate')
    graph.add_edge('validate', END)
    graph.add_edge('insufficient', END)
    return graph.compile()
