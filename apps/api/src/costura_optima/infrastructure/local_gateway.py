"""Fixed-destination TCP ingress for an otherwise offline Docker network."""
from __future__ import annotations

import asyncio
import contextlib


ROUTES = (
    ("0.0.0.0", 8000, "api", 8000),
    ("0.0.0.0", 5173, "web", 5173),
)


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()


async def _forward(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
    except OSError:
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        _copy(client_reader, upstream_writer),
        _copy(upstream_reader, client_writer),
    )


async def serve() -> None:
    servers = []
    for listen_host, listen_port, target_host, target_port in ROUTES:
        servers.append(await asyncio.start_server(
            lambda reader, writer, host=target_host, port=target_port: _forward(reader, writer, host, port),
            listen_host,
            listen_port,
        ))
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(serve())
