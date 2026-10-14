"""Insert only named local fixtures, then index through real hosted Embedding API."""
import asyncio
import json
from pathlib import Path

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict

from app.embeddings import Settings
from app.indexing import index_document
from app.main import DocumentRequest
from app.store import Store


async def main():
    settings = Settings()
    dsn = dotenv_values('.env').get('DATABASE_ADMIN_URL') or settings.database_url.get_secret_value()
    params = conninfo_to_dict(dsn)
    if params.get('host') not in ('127.0.0.1', 'localhost', '::1') or params.get('dbname') != 'cxj_blog':
        raise SystemExit('Seed requires local cxj_blog; no remote seeding allowed')
    fixtures = json.loads(Path('fixtures/posts.json').read_text(encoding='utf-8'))
    documents = []
    async with await psycopg.AsyncConnection.connect(dsn) as c:
        row = await (await c.execute("SELECT id FROM sys_user WHERE email='rag-fixture@example.test' AND deleted_at IS NULL")).fetchone()
        if row:
            author = row[0]
        else:
            author = (await (await c.execute("INSERT INTO sys_user(email,nickname,role,status) VALUES ('rag-fixture@example.test','RAG测试作者','USER','DISABLED') RETURNING id")).fetchone())[0]
        for fixture in fixtures:
            row = await (await c.execute('SELECT id,author_id FROM blog_post WHERE slug=%s AND deleted_at IS NULL', (fixture['slug'],))).fetchone()
            if row and row[1] != author:
                raise SystemExit('Fixture slug conflicts with a non-fixture article; stopping')
            if row:
                post_id = row[0]
            else:
                post_id = (await (await c.execute("""INSERT INTO blog_post(author_id,title,slug,summary,content_md,status,published_at)
                  VALUES (%s,%s,%s,%s,%s,%s,CASE WHEN %s='PUBLISHED' THEN now() END) RETURNING id""",
                  (author, fixture['title'], fixture['slug'], '本地 RAG 功能测试数据', fixture['content_md'], fixture['status'], fixture['status']))).fetchone())[0]
            await c.execute('SELECT ai.enqueue_article(%s)', (post_id,))
            revision = (await (await c.execute('SELECT revision FROM ai.index_job WHERE post_id=%s', (post_id,))).fetchone())[0]
            documents.append(DocumentRequest(post_id=post_id, revision=revision, **fixture))
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        for doc in documents:
            result = await index_document(doc, settings, Store(settings), client)
            print(doc.slug, result['status'], 'chunks=', result.get('chunks', 0))
    print('Fixtures ready: 3 published, 1 draft, 1 offline; existing articles untouched')


if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
