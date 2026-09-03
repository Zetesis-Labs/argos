"""Parada limpia de un proceso de larga vida."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable

Stop = Callable[[], bool]
Sleep = Callable[[float], Awaitable[None]]


def stop_on_signals() -> Stop:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, stopping.set)
    return stopping.is_set
