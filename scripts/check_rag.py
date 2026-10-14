"""Explicit live smoke test. Uses real Embedding + one bounded DeepSeek answer."""
import asyncio
import json

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.embeddings import Settings, embed
from app.rag import build_graph
from app.store import Store


async def main():
    settings = Settings()
    admin_dsn = dotenv_values('.env')['DATABASE_ADMIN_URL']
    params = conninfo_to_dict(admin_dsn)
    if params.get('host') not in ('127.0.0.1', 'localhost', '::1') or params.get('dbname') != 'cxj_blog':
        raise SystemExit('Smoke test only permits local cxj_blog')
    store = Store(settings)
    async with await psycopg.AsyncConnection.connect(admin_dsn, row_factory=dict_row) as admin:
        rows = await (await admin.execute("SELECT id,slug FROM blog_post WHERE slug LIKE 'rag-demo-%' AND deleted_at IS NULL")).fetchall()
        ids = {r['slug']: r['id'] for r in rows}
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            question = 'Docker 部署和直接部署有什么区别？回滚时数据库会自动恢复吗？'
            result = await build_graph(settings, store, client).ainvoke({'question': question, 'post_id': ids['rag-demo-docker']})
            assert result['grounded'] and result['sources']
            print('Live DeepSeek RAG answer:')
            print(result['answer'])
            print('Sources:', [s['slug'] for s in result['sources']])
            print('Usage:', json.dumps(result['usage'], ensure_ascii=False))
            query_vector = (await embed(['知识库草稿和下架资料'], settings, client))[0]
            for slug in ['rag-demo-private', 'rag-demo-offline']:
                assert await store.retrieve('资料', query_vector, ids[slug]) == []
            print('Draft and offline exclusion passed')
            # Mutate only our own fixture and restore even if an assertion fails.
            await admin.execute("UPDATE blog_post SET status='OFFLINE' WHERE id=%s", (ids['rag-demo-docker'],))
            await admin.commit()
            try:
                assert not await store.validate_sources(result['sources'])
                assert await store.retrieve('Docker', query_vector, ids['rag-demo-docker']) == []
            finally:
                await admin.execute("UPDATE blog_post SET status='PUBLISHED' WHERE id=%s", (ids['rag-demo-docker'],))
                await admin.commit()
            print('Immediate source-status validation passed')
            empty = await build_graph(settings, store, client).ainvoke({'question': '这篇文章说了什么？', 'post_id': ids['rag-demo-private']})
            assert empty['grounded'] is False and empty['usage'] == {}
            print('No-evidence response passed without DeepSeek call')


if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
