"""Container health probe: Redis can be alive while no RQ worker consumes jobs."""

from rq import Worker

from costura_optima.worker.queue import optimization_queue, redis_connection


def main() -> None:
    connection = redis_connection()
    if not connection.ping():
        raise SystemExit(1)
    workers = Worker.all(connection=connection, queue=optimization_queue())
    if not any(worker.get_state() in {"idle", "busy"} for worker in workers):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
