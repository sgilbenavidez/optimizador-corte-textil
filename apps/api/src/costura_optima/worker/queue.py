from redis import Redis
from rq import Queue, Retry
from rq.job import Job

from costura_optima.settings import get_settings


def redis_connection() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1)


def optimization_queue() -> Queue:
    settings = get_settings()
    return Queue(settings.optimization_queue_name, connection=redis_connection(), default_timeout=180)


def enqueue_optimization_run(run_id: str, timeout_seconds: int = 180) -> str:
    job = optimization_queue().enqueue(
        "costura_optima.worker.tasks.execute_optimization_run", run_id,
        job_id=run_id, job_timeout=timeout_seconds, result_ttl=86400, failure_ttl=604800,
        retry=Retry(max=1), on_failure="costura_optima.worker.tasks.record_worker_failure",
    )
    return job.id


def cancel_queued_job(job_id: str) -> None:
    try:
        job = Job.fetch(job_id, connection=redis_connection())
        if job.get_status(refresh=True) in {"queued", "deferred", "scheduled"}:
            job.cancel()
    except Exception:
        return
