"""One-off privileged database provisioning; never called on API startup."""
import asyncio
import os

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from app.embeddings import Settings
from app.store import Store, StoreError
from scripts.migrate import main as migrate


async def main():
    settings = Settings()
    admin_url = os.environ.get('DATABASE_ADMIN_URL', '')
    if not admin_url:
        raise StoreError('DATABASE_ADMIN_URL is required for provisioning')
    runtime = conninfo_to_dict(settings.database_url.get_secret_value())
    admin = conninfo_to_dict(admin_url)
    if runtime.get('user') != 'cxj_blog_ai' or len(runtime.get('password', '')) < 32:
        raise StoreError('Expected dedicated cxj_blog_ai role with a strong password')
    for key, default in [('host', ''), ('port', '5432'), ('dbname', '')]:
        if runtime.get(key, default) != admin.get(key, default):
            raise StoreError('Runtime and migration database destinations must match')
    privileged = Store(settings.model_copy(update={'database_url': SecretStr(admin_url)}))
    async with privileged.connection() as c:
        role = await (await c.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname='cxj_blog_ai'")).fetchone()
        if role and any(role.values()):
            raise StoreError('Existing AI role is privileged; refusing adoption')
        if role is None:
            await c.execute(sql.SQL('CREATE ROLE cxj_blog_ai LOGIN PASSWORD {}').format(sql.Literal(runtime['password'])))
    # Existing roles are not silently reset; a wrong password must fail here.
    await check_login(settings)
    await migrate()
    print('Dedicated AI role and schema ready')


async def check_login(settings):
    async with Store(settings).connection() as c:
        await c.execute('SELECT 1')


if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
