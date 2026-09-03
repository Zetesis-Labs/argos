"""Spans de la cadena (constitución §11). Los atributos los decide el núcleo:
identificadores, estados y tamaños; nunca contenido."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import trace as trace_api
from opentelemetry.trace import Span

from argos.core.observability import Attributes

TRACER = "argos"


@contextmanager
def span(name: str, attributes: Attributes) -> Generator[Span]:
    tracer = trace_api.get_tracer(TRACER)
    with tracer.start_as_current_span(name) as current:
        annotate(current, attributes)
        yield current


def annotate(current: Span, attributes: Attributes) -> None:
    for key, value in attributes.items():
        current.set_attribute(key, value)
