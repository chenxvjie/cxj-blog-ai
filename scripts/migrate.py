import asyncio
import os

from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from app.embeddings import Settings
from app.store import Store


async def main():
    settings = Settings()
    admin = os.environ.get('DATABASE_ADMIN_URL') or dotenv_values('.env').get('DATABASE_ADMIN_URL')
    if admin:
        settings = settings.model_copy(update={'database_url': SecretStr(admin)})
    await Store(settings).migrate()
    runtime_user = conninfo_to_dict(Settings().database_url.get_secret_value()).get('user')
    admin_user = conninfo_to_dict(settings.database_url.get_secret_value()).get('user')
    if runtime_user and runtime_user != admin_user:
        async with Store(settings).connection() as connection:
            role = sql.Identifier(runtime_user)
            for statement in [
                'GRANT USAGE ON SCHEMA ai,public TO {}',
                'GRANT SELECT,INSERT,UPDATE,DELETE ON ai.document,ai.chunk,ai.index_job TO {}',
                'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA ai TO {}',
                'GRANT SELECT ON public.blog_post TO {}',
                'GRANT EXECUTE ON FUNCTION ai.enqueue_article(bigint) TO {}',
                'REVOKE ALL ON ai.schema_migration FROM {}',
            ]:
                await connection.execute(sql.SQL(statement).format(role))
    print('AI schema migration completed')


if __name__ == '__main__':
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
