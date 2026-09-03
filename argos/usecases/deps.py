from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from argos.core.policy import Policy
from argos.core.ports import Clock, IdSource, Ledger, MessageBus, S3ObjectStore


class Bookkeeping(Protocol):
    """Lo que basta para leer y mover el libro: sin bus ni almacén de objetos."""

    @property
    def ledger(self) -> Ledger: ...

    @property
    def clock(self) -> Clock: ...

    @property
    def ids(self) -> IdSource: ...

    @property
    def policy(self) -> Policy: ...


@dataclass(frozen=True)
class Dispatching:
    """Lo que necesita el dispatcher: nunca toca el almacén de objetos."""

    ledger: Ledger
    bus: MessageBus
    clock: Clock
    policy: Policy


@dataclass(frozen=True)
class Analyzing:
    """Lo que necesitan las herramientas, el resumer y el analizador: nunca artefactos."""

    ledger: Ledger
    clock: Clock
    ids: IdSource
    policy: Policy


@dataclass(frozen=True)
class Services:
    ledger: Ledger
    object_store: S3ObjectStore
    bus: MessageBus
    clock: Clock
    ids: IdSource
    policy: Policy
    bucket: str

    @property
    def dispatching(self) -> Dispatching:
        return Dispatching(ledger=self.ledger, bus=self.bus, clock=self.clock, policy=self.policy)

    @property
    def analyzing(self) -> Analyzing:
        return Analyzing(ledger=self.ledger, clock=self.clock, ids=self.ids, policy=self.policy)
