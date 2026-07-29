from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
assert DATABASE_URL is not None, "DATABASE_URL is not set in the environment variables."

# 生产环境推荐的连接池配置
engine = create_async_engine(
    DATABASE_URL,
    echo=True,                    # 生产关闭
    pool_pre_ping=True,            # 每次取连接前检查是否有效（强烈推荐）
    pool_recycle=3600,             # 1小时回收一次，防止 MySQL 断开
    pool_timeout=30,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,        # 很重要！防止提交后对象属性失效
    autoflush=False,
    autocommit=False,
)


# 依赖注入
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()