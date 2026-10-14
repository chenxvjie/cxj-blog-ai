import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.chunking import Chunk, digest, terms
from app.embeddings import Settings


class StoreError(Exception):
    pass


def signature(settings: Settings) -> str:
    return f"{settings.embedding_provider}:{settings.embedding_model}:{settings.embedding_dimensions}:markdown-v1"


def vector(value: list[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in value) + "]"


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings

    @asynccontextmanager
    async def connection(self):
        if not self.settings.database_url.get_secret_value():
            raise StoreError("数据库尚未配置")
        try:
            connection = await psycopg.AsyncConnection.connect(
                self.settings.database_url.get_secret_value(), connect_timeout=5, row_factory=dict_row,
                options="-c statement_timeout=10000 -c lock_timeout=5000",
            )
            async with connection:
                yield connection
        except psycopg.Error:
            raise StoreError("知识库数据库操作失败，请检查服务与迁移状态") from None

    async def migrate(self):
        async with self.connection() as c:
            await c.execute("SELECT pg_advisory_xact_lock(818141141)")
            await c.execute("CREATE SCHEMA IF NOT EXISTS ai")
            await c.execute("CREATE TABLE IF NOT EXISTS ai.schema_migration(name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
            for migration in sorted((Path(__file__).resolve().parent.parent / 'sql').glob('*.sql')):
                content = migration.read_text(encoding='utf-8')
                checksum = hashlib.sha256(content.encode()).hexdigest()
                old = await (await c.execute("SELECT checksum FROM ai.schema_migration WHERE name=%s", (migration.name,))).fetchone()
                if old:
                    if old['checksum'] != checksum:
                        raise StoreError('已执行的AI迁移内容发生变化，请新增迁移而不是修改历史文件')
                    continue
                await c.execute(content)
                await c.execute("INSERT INTO ai.schema_migration(name,checksum) VALUES (%s,%s)", (migration.name, checksum))

    async def health(self):
        async with self.connection() as c:
            result = await (await c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")).fetchone()
            await c.execute("SELECT 1 FROM ai.document LIMIT 1")
            return result is not None

    async def stage(self, post_id: int, revision: int, title: str, slug: str, content: str, status: str):
        content_hash = digest(title, slug, content)
        async with self.connection() as c:
            await c.execute("SELECT pg_advisory_xact_lock(%s)", (post_id,))
            old = await (await c.execute("SELECT * FROM ai.document WHERE post_id=%s FOR UPDATE", (post_id,))).fetchone()
            if old and (revision < old['revision'] or (revision == old['revision'] and content_hash != old['content_hash'] and old['index_state'] != 'PENDING')):
                raise StoreError("文章版本过旧或同版本内容不一致")
            if old and revision == old['revision'] and old['status'] == status and old['index_state'] == 'READY' and old['model_signature'] == signature(self.settings):
                return False
            await c.execute("""INSERT INTO ai.document(post_id,revision,title,slug,content_md,content_hash,status,index_state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s='PUBLISHED' THEN 'PENDING' ELSE 'EXCLUDED' END) ON CONFLICT(post_id) DO UPDATE SET
                revision=excluded.revision,title=excluded.title,slug=excluded.slug,content_md=excluded.content_md,
                content_hash=excluded.content_hash,status=excluded.status,active_revision=NULL,
                index_state=excluded.index_state,updated_at=now()""", (post_id, revision, title, slug, content, content_hash, status, status))
            return True

    async def finish(self, post_id: int, revision: int, chunks: list[Chunk], vectors: list[list[float]]):
        async with self.connection() as c:
            doc = await (await c.execute("SELECT * FROM ai.document WHERE post_id=%s FOR UPDATE", (post_id,))).fetchone()
            if not doc or doc['revision'] != revision or doc['status'] != 'PUBLISHED':
                raise StoreError("文章版本或公开状态已变化，索引结果已丢弃")
            await c.execute("DELETE FROM ai.chunk WHERE post_id=%s", (post_id,))
            for index, (chunk, embedding) in enumerate(zip(chunks, vectors, strict=True)):
                title_terms = ' '.join(terms(doc['title'] + ' ' + chunk.heading))
                body_terms = ' '.join(terms(chunk.content))
                await c.execute("""INSERT INTO ai.chunk(post_id,revision,ordinal,heading,content,search_vector,embedding)
                  VALUES (%s,%s,%s,%s,%s,setweight(to_tsvector('simple',%s),'A') || setweight(to_tsvector('simple',%s),'B'),%s::vector)""",
                  (post_id, revision, index, chunk.heading, chunk.content, title_terms, body_terms, vector(embedding)))
            await c.execute("UPDATE ai.document SET active_revision=%s,index_state='READY',model_signature=%s WHERE post_id=%s", (revision, signature(self.settings), post_id))

    async def failed(self, post_id: int, revision: int):
        async with self.connection() as c:
            await c.execute("UPDATE ai.document SET index_state='FAILED' WHERE post_id=%s AND revision=%s AND active_revision IS NULL", (post_id, revision))

    async def retrieve(self, query: str, embedding: list[float], post_id: int | None = None):
        lexical = ' OR '.join(terms(query)[:64]) or 'cxj_empty_query_marker'
        async with self.connection() as c:
            return await (await c.execute("""WITH candidates AS (
              SELECT c.id,c.post_id,c.revision,c.heading,c.content,d.title,d.slug,
                1-(c.embedding <=> %(vec)s::vector) AS similarity,
                ts_rank_cd(c.search_vector,websearch_to_tsquery('simple',%(query)s)) AS lexical
              FROM ai.chunk c JOIN ai.document d ON d.post_id=c.post_id JOIN public.blog_post p ON p.id=d.post_id
              WHERE p.status='PUBLISHED' AND p.deleted_at IS NULL AND p.title=d.title AND p.slug=d.slug AND p.content_md=d.content_md
                AND d.status='PUBLISHED' AND d.active_revision=c.revision AND d.revision=c.revision
                AND d.model_signature=%(signature)s AND (%(post_id)s::bigint IS NULL OR c.post_id=%(post_id)s)
            ), dense AS (SELECT id,row_number() OVER(ORDER BY similarity DESC,id) AS rank FROM candidates ORDER BY similarity DESC,id LIMIT 20),
            sparse AS (SELECT id,row_number() OVER(ORDER BY lexical DESC,id) AS rank FROM candidates WHERE lexical>0 ORDER BY lexical DESC,id LIMIT 20)
            SELECT c.*,coalesce(1.0/(60+d.rank),0)+coalesce(1.0/(60+s.rank),0) AS score FROM candidates c
              LEFT JOIN dense d ON d.id=c.id LEFT JOIN sparse s ON s.id=c.id
              WHERE (d.id IS NOT NULL OR s.id IS NOT NULL) AND (c.similarity >= %(threshold)s OR c.lexical>0)
              ORDER BY score DESC,c.id LIMIT 6""",
              {'vec': vector(embedding), 'query': lexical, 'signature': signature(self.settings), 'post_id': post_id, 'threshold': self.settings.min_similarity})).fetchall()

    async def validate_sources(self, sources):
        async with self.connection() as c:
            for source in sources:
                doc = await (await c.execute("SELECT d.revision,d.active_revision,d.status FROM ai.document d JOIN public.blog_post p ON p.id=d.post_id WHERE d.post_id=%s AND p.status='PUBLISHED' AND p.deleted_at IS NULL AND p.title=d.title AND p.slug=d.slug AND p.content_md=d.content_md", (source['post_id'],))).fetchone()
                if not doc or doc['status'] != 'PUBLISHED' or doc['revision'] != source['revision'] or doc['active_revision'] != source['revision']:
                    return False
            return True
