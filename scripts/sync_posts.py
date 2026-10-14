"""Queue all current articles and drain available jobs once."""
import asyncio

import httpx

from app.embeddings import Settings
from app.jobs import Jobs, process_one


async def main():
    settings = Settings()
    jobs = Jobs(settings)
    async with jobs.connection() as c:
        rows = await (await c.execute('SELECT id FROM public.blog_post ORDER BY id')).fetchall()
    for row in rows:
        await jobs.retry(row['id'])
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        while await process_one(settings, jobs, client):
            pass
    result = await jobs.list_jobs()
    print('Index job counts:', result['counts'])

if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
