from typing import Any


class APIError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: list[dict[str, Any]] | None = None
    ):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(message)
