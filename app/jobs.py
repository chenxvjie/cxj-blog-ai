import asyncio
import logging
import uuid

import httpx

from app.embeddings import Settings
from app.indexing import index_document
from app.store import Store, StoreError

LOG = logging.getLogger(__name__)


class Jobs(Store):
    async def claim(self):
        async with self.connection() as c:
            await c.execute("UPDATE ai.index_job SET state='FAILED',error_code='LEASE_EXPIRED' WHERE state='RUNNING' AND lease_until<now() AND attempts>=5")
            return await (await c.execute("""WITH candidate AS (
              SELECT post_id FROM ai.index_job WHERE attempts<5 AND
                ((state='PENDING' AND available_at<=now()) OR (state='RUNNING' AND lease_until<now()))
              ORDER BY available_at,post_id FOR UPDATE SKIP LOCKED LIMIT 1)
              UPDATE ai.index_job j SET state='RUNNING',attempts=attempts+1,lease=%s,
                lease_until=now()+interval '2 minutes',updated_at=now()
              FROM candidate c WHERE j.post_id=c.post_id RETURNING j.*""", (uuid.uuid4(),))).fetchone()

    async def heartbeat(self, job):
        async with self.connection() as c:
            result = await c.execute("UPDATE ai.index_job SET lease_until=now()+interval '2 minutes' WHERE post_id=%s AND revision=%s AND lease=%s AND state='RUNNING'", (job['post_id'], job['revision'], job['lease']))
            if result.rowcount != 1:
                raise StoreError('索引任务已被新版本替代')

    async def complete(self, job, state):
        async with self.connection() as c:
            await c.execute("UPDATE ai.index_job SET state=%s,lease=NULL,lease_until=NULL,error_code=NULL,updated_at=now() WHERE post_id=%s AND revision=%s AND lease=%s", (state, job['post_id'], job['revision'], job['lease']))

    async def fail(self, job, error_code):
        async with self.connection() as c:
            await c.execute("""UPDATE ai.index_job SET state=CASE WHEN attempts<5 THEN 'PENDING' ELSE 'FAILED' END,
              available_at=now()+(%s * interval '1 second'),error_code=%s,lease=NULL,lease_until=NULL,updated_at=now()
              WHERE post_id=%s AND revision=%s AND lease=%s""", (min(5 * 2 ** job['attempts'], 300), error_code, job['post_id'], job['revision'], job['lease']))

    async def list_jobs(self):
        async with self.connection() as c:
            records = await (await c.execute("SELECT j.post_id,j.revision,j.state,j.attempts,j.error_code,j.updated_at,p.title FROM ai.index_job j LEFT JOIN public.blog_post p ON p.id=j.post_id ORDER BY j.updated_at DESC LIMIT 100")).fetchall()
            counts = await (await c.execute('SELECT state,count(*) AS count FROM ai.index_job GROUP BY state')).fetchall()
            return {'records': records, 'counts': counts}

    async def retry(self, post_id):
        async with self.connection() as c:
            exists = await (await c.execute('SELECT id FROM public.blog_post WHERE id=%s', (post_id,))).fetchone()
            if not exists:
                raise StoreError('文章不存在')
            await c.execute('SELECT ai.enqueue_article(%s)', (post_id,))


async def process_one(settings: Settings, jobs: Jobs, client: httpx.AsyncClient):
    job = await jobs.claim()
    if not job:
        return False
    try:
        async with jobs.connection() as c:
            source = await (await c.execute("""SELECT id,title,slug,content_md,
                CASE WHEN deleted_at IS NOT NULL THEN 'DELETED' ELSE status END AS status
                FROM public.blog_post WHERE id=%s""", (job['post_id'],))).fetchone()
        if source is None:
            await jobs.complete(job, 'EXCLUDED')
            return True
        from app.main import DocumentRequest
        document = DocumentRequest(post_id=source['id'], revision=job['revision'], title=source['title'], slug=source['slug'], content_md=source['content_md'], status=source['status'])
        await index_document(document, settings, jobs, client, heartbeat=lambda: jobs.heartbeat(job))
        await jobs.complete(job, 'READY' if document.status == 'PUBLISHED' else 'EXCLUDED')
    except asyncio.CancelledError:
        # Lease expiry makes a terminated worker's job recoverable.
        raise
    except Exception as error:  # noqa: BLE001 - persist a type code, never provider bodies or credentials
        await jobs.fail(job, type(error).__name__[:80])
        LOG.warning('Index job failed: post_id=%s type=%s', job['post_id'], type(error).__name__)
    return True


async def worker_loop(settings: Settings, client: httpx.AsyncClient):
    jobs = Jobs(settings)
    while True:
        try:
            if not await process_one(settings, jobs, client):
                await asyncio.sleep(2)
        except StoreError:
            LOG.warning('Index worker database unavailable')
            await asyncio.sleep(5)
