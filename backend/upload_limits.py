"""Refuse oversized request bodies BEFORE the framework reads them.

Starlette parses a multipart body (spooling large parts to disk) before a route handler runs, so a
size check inside the handler cannot stop an oversized upload from being received. This ASGI middleware
checks the declared Content-Length up front for the Source CV endpoints and answers 413 immediately.
(The ASGI server never delivers more bytes than the declared length, so the declaration is enforceable.)
A multipart request that declares no length at all is refused with 411 rather than read unbounded.

Registered BEFORE CORSMiddleware so the CORS layer wraps it and the browser can read the 413 body.
"""
import json
from typing import Callable

from source_cv.extraction import MAX_UPLOAD_BYTES

MULTIPART_OVERHEAD_BYTES = 256 * 1024
JSON_BODY_LIMIT_BYTES = 1024 * 1024


class BodySizeLimitMiddleware:
    def __init__(self, app: Callable, *, path_prefix: str = "/api/source-cv", max_upload_bytes: int = MAX_UPLOAD_BYTES):
        self.app = app
        self.prefix = path_prefix
        self.max_upload_bytes = max_upload_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT") or not scope["path"].startswith(self.prefix):
            return await self.app(scope, receive, send)

        headers = {k.lower(): v for k, v in scope["headers"]}
        multipart = headers.get(b"content-type", b"").lower().startswith(b"multipart/")
        limit = self.max_upload_bytes + MULTIPART_OVERHEAD_BYTES if multipart else JSON_BODY_LIMIT_BYTES
        declared = headers.get(b"content-length")

        if declared is None and multipart:
            return await self._refuse(send, 411, "length_required", "The upload must declare its size.")
        try:
            too_big = declared is not None and int(declared) > limit
        except ValueError:
            return await self._refuse(send, 400, "bad_request", "The request is malformed.")
        if too_big:
            code, message = (
                ("file_too_large", f"The file is larger than the {self.max_upload_bytes // (1024 * 1024)} MB upload limit.")
                if multipart
                else ("request_too_large", "The request is too large.")
            )
            return await self._refuse(send, 413, code, message)
        return await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(send, status: int, code: str, message: str) -> None:
        body = json.dumps({"detail": {"code": code, "message": message, "retryable": False}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
