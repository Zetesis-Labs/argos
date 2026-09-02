"""Trazas de Agno a Langfuse por OpenTelemetry (constitución §11)."""

from __future__ import annotations

import base64

from openinference.instrumentation.agno import AgnoInstrumentor
from opentelemetry import trace as trace_api
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from argos.config import Settings


def langfuse_basic_auth(public_key: str, secret_key: str) -> str:
    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def langfuse_otlp_endpoint(host: str) -> str:
    return f"{host.rstrip('/')}/api/public/otel/v1/traces"


def setup_tracing(settings: Settings, *, service_name: str = "argos") -> TracerProvider:
    exporter = OTLPSpanExporter(
        endpoint=langfuse_otlp_endpoint(settings.langfuse_host),
        headers={
            "Authorization": "Basic "
            + langfuse_basic_auth(
                settings.langfuse_public_key, settings.langfuse_secret_key.get_secret_value()
            )
        },
    )
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace_api.set_tracer_provider(provider)
    AgnoInstrumentor().instrument(tracer_provider=provider)
    return provider
