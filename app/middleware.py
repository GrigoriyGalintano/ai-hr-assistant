from starlette.responses import JSONResponse

from app.errors import error_payload


class BodySizeLimit:
    """Bound request bodies before JSON or multipart parsing allocates temporary files."""

    def __init__(self, app, max_bytes=3 * 1024 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                response = JSONResponse(
                    error_payload("REQUEST_TOO_LARGE", "Размер запроса превышает 3 МБ."),
                    status_code=413,
                )
                return await response(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(messages)

        async def buffered_receive():
            return next(iterator, None) or await receive()

        await self.app(scope, buffered_receive, send)
