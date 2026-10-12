"""One-shot loopback listener for OAuth redirects.

A small asyncio HTTP server bound to the provider's fixed callback port. It answers only
``GET <callback_path>`` and hands the query parameters to the login manager, which matches the
``state`` against pending logins. It never logs query strings (they carry the code).
"""

import asyncio
import contextlib
import html
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

CallbackHandler = Callable[[dict[str, str]], Awaitable[tuple[int, str]]]

MAX_REQUEST_BYTES = 16 * 1024
READ_TIMEOUT_S = 10.0
REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}


def page(title: str, message: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>BrainBench</title></head>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>"
    )


class CallbackServer:
    def __init__(self, host: str, port: int, path: str, handler: CallbackHandler) -> None:
        self.host, self.port, self.path = host, port, path
        self._handler = handler
        self._server: asyncio.Server | None = None

    @property
    def listening(self) -> bool:
        return self._server is not None

    async def start(self) -> bool:
        try:
            self._server = await asyncio.start_server(self._serve, self.host, self.port)
        except OSError as exc:
            log.warning("OAuth callback port %d unavailable: %s", self.port, exc.strerror)
            return False
        log.info("listening for OAuth callbacks on %s:%d", self.host, self.port)
        return True

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
            log.info("OAuth callback listener on port %d closed", self.port)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            status, body = await self._respond(reader)
        except TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError:
            status, body = 400, page("Bad request", "The request could not be read.")
        payload = body.encode()
        head = (
            f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
        )
        try:
            writer.write(head.encode() + payload)
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _respond(self, reader: asyncio.StreamReader) -> tuple[int, str]:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT_S)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        method, target, _ = raw.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
        if method != "GET":
            return 405, page("Not allowed", "Only GET is accepted here.")
        parts = urlsplit(target)
        if parts.path != self.path:
            return 404, page("Not found", "This is the BrainBench sign-in callback listener.")
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        return await self._handler(params)
