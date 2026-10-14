import httpx

from app.chunking import split_markdown
from app.embeddings import EmbeddingError, Settings, embed
from app.store import Store


async def index_document(document, settings: Settings, store: Store, client: httpx.AsyncClient, heartbeat=None):
    changed = await store.stage(document.post_id, document.revision, document.title, document.slug, document.content_md, document.status)
    if not changed:
        return {'status': 'unchanged', 'post_id': document.post_id}
    if document.status != 'PUBLISHED':
        return {'status': 'excluded', 'post_id': document.post_id}
    try:
        chunks = split_markdown(document.content_md)
        if not chunks:
            raise EmbeddingError('正文没有可索引的内容')
        vectors = []
        for offset in range(0, len(chunks), 16):
            if heartbeat is not None:
                await heartbeat()
            batch = chunks[offset:offset+16]
            texts = [document.title + '\n' + chunk.heading + '\n' + chunk.content for chunk in batch]
            vectors.extend(await embed(texts, settings, client))
        await store.finish(document.post_id, document.revision, chunks, vectors)
        return {'status': 'ready', 'post_id': document.post_id, 'chunks': len(chunks)}
    except EmbeddingError:
        await store.failed(document.post_id, document.revision)
        raise
