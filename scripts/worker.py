import asyncio

import httpx

from app.embeddings import Settings
from app.jobs import worker_loop


async def main():
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        await worker_loop(Settings(), client)


if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
