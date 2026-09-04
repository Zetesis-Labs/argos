from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from argos.core.policy import Policy
from argos.core.ports import Clock, IdSource, Ledger


class Bookkeeping(Protocol):
    """Lo que basta para leer y mover el libro."""

    @property
    def ledger(self) -> Ledger: ...

    @property
    def clock(self) -> Clock: ...

    @property
    def ids(self) -> IdSource: ...

    @property
    def policy(self) -> Policy: ...


@dataclass(frozen=True)
class Services:
    ledger: Ledger
    clock: Clock
    ids: IdSource
    policy: Policy
