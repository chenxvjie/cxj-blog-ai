"""Manual live probe: sends only a fixed short text; never prints credentials or vectors."""
import asyncio

import httpx

from app.embeddings import EmbeddingError, Settings, embed


async def main():
    try:
        settings = Settings()
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            result = await embed(["博客知识库向量接口连接测试"], settings, client)
        print(f"Embedding probe passed: count={len(result)}, dimensions={len(result[0])}")
    except EmbeddingError as error:
        print(str(error))
        raise SystemExit(1) from None
    except Exception:  # noqa: BLE001 - avoid exposing credentials in configuration errors
        print("配置或测试运行失败；未输出敏感信息")
        raise SystemExit(1) from None


if __name__ == "__main__":
    asyncio.run(main())
