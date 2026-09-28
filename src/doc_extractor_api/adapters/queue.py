"""Job queue on Redis with arq. The API only depends on the JobQueue Protocol."""

from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

PROCESS_JOB = "process_job"


class JobQueue(Protocol):
    async def enqueue(self, job_id: str) -> None: ...


class ArqJobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, job_id: str) -> None:
        # _job_id = our id: arq ignores a second enqueue of the same job.
        await self._pool.enqueue_job(PROCESS_JOB, job_id, _job_id=job_id)

    async def close(self) -> None:
        await self._pool.aclose()


def redis_settings(redis_url: str) -> RedisSettings:
    return RedisSettings.from_dsn(redis_url)


async def connect_queue(redis_url: str) -> ArqJobQueue:
    return ArqJobQueue(await create_pool(redis_settings(redis_url)))
