from __future__ import annotations

from dataclasses import dataclass

from argos.core.policy import Policy
from argos.core.ports import Clock, IdSource, Ledger, MessageBus, S3ObjectStore


@dataclass(frozen=True)
class Dispatching:
    """Lo que necesita el dispatcher: nunca toca el almacén de objetos."""

    ledger: Ledger
    bus: MessageBus
    clock: Clock
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
