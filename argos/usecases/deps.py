from __future__ import annotations

from dataclasses import dataclass

from argos.core.policy import Policy
from argos.core.ports import Clock, IdSource, Ledger, MessageBus, ObjectStore


@dataclass(frozen=True)
class Services:
    ledger: Ledger
    object_store: ObjectStore
    bus: MessageBus
    clock: Clock
    ids: IdSource
    policy: Policy
    bucket: str
