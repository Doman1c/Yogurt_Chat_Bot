from __future__ import annotations

from typing import Literal


SingingErrorCode = Literal[
    "invalid_request",
    "unknown_asset",
    "unsupported_media",
    "separator_unavailable",
    "separation_failed",
    "engine_unavailable",
    "conversion_failed",
    "remix_failed",
    "timeout",
    "cancelled",
]


class SingingError(RuntimeError):
    """Internal error carrying only a public, bounded failure category."""

    def __init__(self, code: SingingErrorCode) -> None:
        super().__init__(code)
        self.code = code
