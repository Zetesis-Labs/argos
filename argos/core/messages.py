"""Contrato de mensajes NATS (S02 §8): solo `job_id` y `attempt`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from argos.core.model import JobType

JOBS_STREAM = "ARGOS_JOBS"
EVENTS_STREAM = "ARGOS_EVENTS"

JOB_SUBJECTS: dict[JobType, str] = {
    JobType.DOCUMENT_EXTRACT: "argos.jobs.document.extract.v1",
    JobType.CASE_ANALYZE: "argos.jobs.case.analyze.v1",
    JobType.SOURCE_INGEST: "argos.jobs.source.ingest.v1",
}

DOCUMENT_EXTRACTED_SUBJECT = "argos.events.document.extracted.v1"
DOCUMENT_FAILED_SUBJECT = "argos.events.document.failed.v1"
CASE_COMPLETED_SUBJECT = "argos.events.case.completed.v1"

MESSAGE_ID_HEADER = "Nats-Msg-Id"


@dataclass(frozen=True)
class JobMessage:
    job_id: str
    attempt: int


class MalformedMessageError(ValueError):
    pass


def encode_job_message(message: JobMessage) -> bytes:
    return json.dumps({"job_id": message.job_id, "attempt": message.attempt}).encode()


def decode_job_message(payload: bytes) -> JobMessage:
    try:
        decoded = cast(object, json.loads(payload))
    except ValueError as error:
        raise MalformedMessageError("payload is not JSON") from error
    if not isinstance(decoded, dict):
        raise MalformedMessageError("payload is not an object")
    fields = cast(dict[str, object], decoded)
    if set(fields) != {"job_id", "attempt"}:
        raise MalformedMessageError("payload must contain exactly job_id and attempt")
    job_id = fields["job_id"]
    attempt = fields["attempt"]
    if not isinstance(job_id, str) or not job_id:
        raise MalformedMessageError("job_id must be a non-empty string")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise MalformedMessageError("attempt must be a positive integer")
    return JobMessage(job_id=job_id, attempt=attempt)


@dataclass(frozen=True)
class StreamSpec:
    name: str
    subjects: tuple[str, ...]
    workqueue: bool


@dataclass(frozen=True)
class ConsumerSpec:
    stream: str
    durable: str
    subjects: tuple[str, ...]


STREAMS = (
    StreamSpec(
        name=JOBS_STREAM,
        subjects=tuple(JOB_SUBJECTS[job_type] for job_type in JobType),
        workqueue=True,
    ),
    StreamSpec(
        name=EVENTS_STREAM,
        subjects=(DOCUMENT_EXTRACTED_SUBJECT, DOCUMENT_FAILED_SUBJECT, CASE_COMPLETED_SUBJECT),
        workqueue=False,
    ),
)

DOCUMENT_EXTRACTOR = ConsumerSpec(
    stream=JOBS_STREAM,
    durable="document-extractor-v1",
    subjects=(JOB_SUBJECTS[JobType.DOCUMENT_EXTRACT],),
)
CASE_ANALYZER = ConsumerSpec(
    stream=JOBS_STREAM,
    durable="case-analyzer-v1",
    subjects=(JOB_SUBJECTS[JobType.CASE_ANALYZE],),
)
SOURCE_INGESTOR = ConsumerSpec(
    stream=JOBS_STREAM,
    durable="source-ingestor-v1",
    subjects=(JOB_SUBJECTS[JobType.SOURCE_INGEST],),
)
WORKFLOW_RESUMER = ConsumerSpec(
    stream=EVENTS_STREAM,
    durable="workflow-resumer-v1",
    subjects=(DOCUMENT_EXTRACTED_SUBJECT, DOCUMENT_FAILED_SUBJECT),
)
CONSUMERS = (DOCUMENT_EXTRACTOR, CASE_ANALYZER, SOURCE_INGESTOR, WORKFLOW_RESUMER)


def stream_of(subject: str) -> str:
    for stream in STREAMS:
        if subject in stream.subjects:
            return stream.name
    raise ValueError(f"subject {subject} belongs to no stream")


def failure_subject(job_type: JobType) -> str:
    if job_type is JobType.DOCUMENT_EXTRACT:
        return DOCUMENT_FAILED_SUBJECT
    return CASE_COMPLETED_SUBJECT


def success_subject(job_type: JobType) -> str:
    if job_type is JobType.DOCUMENT_EXTRACT:
        return DOCUMENT_EXTRACTED_SUBJECT
    return CASE_COMPLETED_SUBJECT
