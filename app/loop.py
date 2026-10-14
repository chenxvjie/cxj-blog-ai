import asyncio


def loop_factory():
    # Psycopg async connections on Windows require a selector loop.
    return asyncio.SelectorEventLoop()
