"""NATS JetStream: publicación de comandos y eventos, y consumo durable (S02 §8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, cast

import nats
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.errors import Error as NatsError
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    ConsumerInfo,
    RetentionPolicy,
    StorageType,
    StreamConfig,
    StreamInfo,
)
from nats.js.errors import APIError

from argos.core.messages import (
    CONSUMERS,
    MESSAGE_ID_HEADER,
    STREAMS,
    ConsumerSpec,
    JobMessage,
    StreamSpec,
    decode_job_message,
)
from argos.core.policy import JobPolicy
from argos.core.ports import BusUnavailableError, Delivery, OutboundMessage


class _NatsModule(Protocol):
    """`nats` y `JetStreamContext` anotan con `**kwargs` sin tipo las llamadas que usamos."""

    async def connect(self, servers: str) -> Client: ...


class _ClientApi(Protocol):
    def jetstream(self) -> JetStreamContext: ...


class _StreamApi(Protocol):
    async def add_stream(self, *, config: StreamConfig) -> StreamInfo: ...

    async def update_stream(self, *, config: StreamConfig) -> StreamInfo: ...

    async def add_consumer(self, stream: str, *, config: ConsumerConfig) -> ConsumerInfo: ...


def stream_config(spec: StreamSpec, policy: JobPolicy) -> StreamConfig:
    retention = RetentionPolicy.WORK_QUEUE if spec.workqueue else RetentionPolicy.LIMITS
    return StreamConfig(
        name=spec.name,
        subjects=list(spec.subjects),
        retention=retention,
        storage=StorageType.FILE,
        duplicate_window=policy.duplicate_window.total_seconds(),
        max_age=policy.message_ttl.total_seconds(),
    )


def consumer_config(
    spec: ConsumerSpec, policy: JobPolicy, *, ack_wait: timedelta | None = None
) -> ConsumerConfig:
    return ConsumerConfig(
        durable_name=spec.durable,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=(ack_wait or policy.lease).total_seconds(),
        max_deliver=policy.max_deliveries,
        filter_subjects=list(spec.subjects),
    )


@dataclass(frozen=True)
class StreamState:
    name: str
    subjects: frozenset[str]
    workqueue: bool
    duplicate_window: timedelta


@dataclass(frozen=True)
class ConsumerState:
    stream: str
    durable: str
    subjects: frozenset[str]
    explicit_ack: bool
    ack_wait: timedelta
    max_deliveries: int


@dataclass(frozen=True)
class TopologyState:
    streams: tuple[StreamState, ...]
    consumers: tuple[ConsumerState, ...]


def stream_state(info: StreamInfo) -> StreamState:
    window = info.config.duplicate_window or 0.0
    return StreamState(
        name=info.config.name or "",
        subjects=frozenset(info.config.subjects or []),
        workqueue=info.config.retention == RetentionPolicy.WORK_QUEUE,
        duplicate_window=timedelta(seconds=window),
    )


def consumer_state(stream: str, info: ConsumerInfo) -> ConsumerState:
    config = info.config
    filters = config.filter_subjects or ([config.filter_subject] if config.filter_subject else [])
    return ConsumerState(
        stream=stream,
        durable=config.durable_name or "",
        subjects=frozenset(filters),
        explicit_ack=config.ack_policy == AckPolicy.EXPLICIT,
        ack_wait=timedelta(seconds=config.ack_wait or 0.0),
        max_deliveries=config.max_deliver or 0,
    )


class JetStreamDelivery:
    def __init__(self, raw: Msg) -> None:
        self._raw = raw
        self._message = decode_job_message(raw.data)

    @property
    def message(self) -> JobMessage:
        return self._message

    @property
    def subject(self) -> str:
        return self._raw.subject

    @property
    def delivery_count(self) -> int:
        return self._raw.metadata.num_delivered or 1

    async def ack(self) -> None:
        await self._raw.ack()

    async def nak(self) -> None:
        await self._raw.nak()


class JetStreamDeliveries:
    def __init__(self, subscription: JetStreamContext.PullSubscription) -> None:
        self._subscription = subscription

    async def fetch(self, *, limit: int, timeout: float) -> Sequence[Delivery]:
        try:
            raw = await self._subscription.fetch(limit, timeout=timeout)
        except NatsTimeoutError:
            return []
        return [JetStreamDelivery(message) for message in raw]


class JetStreamBus:
    def __init__(self, url: str, *, policy: JobPolicy) -> None:
        self._url = url
        self._policy = policy
        self._client: Client | None = None
        self._jetstream: JetStreamContext | None = None

    async def connect(self) -> None:
        try:
            client = await cast(_NatsModule, nats).connect(self._url)
        except (NatsError, OSError) as error:
            raise BusUnavailableError(f"cannot reach {self._url}") from error
        self._client = client
        self._jetstream = cast(_ClientApi, client).jetstream()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._jetstream = None

    @property
    def _js(self) -> JetStreamContext:
        if self._jetstream is None:
            raise BusUnavailableError("the bus is not connected")
        return self._jetstream

    async def declare(
        self,
        *,
        streams: Sequence[StreamSpec] = STREAMS,
        consumers: Sequence[ConsumerSpec] = CONSUMERS,
    ) -> None:
        api = cast(_StreamApi, self._js)
        for stream in streams:
            config = stream_config(stream, self._policy)
            try:
                await api.add_stream(config=config)
            except APIError:
                await api.update_stream(config=config)
        for consumer in consumers:
            await api.add_consumer(consumer.stream, config=consumer_config(consumer, self._policy))

    async def describe(
        self,
        *,
        streams: Sequence[StreamSpec] = STREAMS,
        consumers: Sequence[ConsumerSpec] = CONSUMERS,
    ) -> TopologyState:
        declared_streams: list[StreamState] = []
        for stream in streams:
            declared_streams.append(stream_state(await self._js.stream_info(stream.name)))
        declared_consumers: list[ConsumerState] = []
        for consumer in consumers:
            info = await self._js.consumer_info(consumer.stream, consumer.durable)
            declared_consumers.append(consumer_state(consumer.stream, info))
        return TopologyState(streams=tuple(declared_streams), consumers=tuple(declared_consumers))

    async def publish(self, message: OutboundMessage) -> None:
        try:
            await self._js.publish(
                message.subject,
                message.payload,
                headers={MESSAGE_ID_HEADER: message.message_id, **message.headers},
            )
        except (NatsError, APIError, OSError) as error:
            raise BusUnavailableError(f"cannot publish to {message.subject}") from error

    async def deliveries(
        self, spec: ConsumerSpec, *, ack_wait: timedelta | None = None
    ) -> JetStreamDeliveries:
        subscription = await self._js.pull_subscribe(
            spec.subjects[0],
            durable=spec.durable,
            stream=spec.stream,
            config=consumer_config(spec, self._policy, ack_wait=ack_wait),
        )
        return JetStreamDeliveries(subscription)

    async def purge(self, *streams: str) -> None:
        for name in streams:
            await self._js.purge_stream(name)
