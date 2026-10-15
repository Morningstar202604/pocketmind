"""OpenTelemetry 可观测性：FastAPI 插桩 + Agent/Scheduler Tracer。

设计：
- TracerProvider 带 Resource（service.name / service.version）；
- Exporter 通过环境变量选择：
    OTEL_TRACES_EXPORTER=otlp  → OTLP/gRPC（可发 Tempo/Jaeger Collector）
    OTEL_TRACES_EXPORTER=console → 控制台输出（本地调试）
    默认 none（仅创建 tracer，不导出——生产上不导出就无法观测，但可以扩展）；
- FastAPIInstrumentor 自动为每个 HTTP 请求创建 span；
- 提供 ``get_tracer()`` / ``agent_span()`` / ``scheduler_span()`` 供手动 span 使用。
"""
from __future__ import annotations

from contextlib import contextmanager

_TRACER = None


def setup_telemetry(app, service_name: str, version: str) -> None:
    import logging as _logging

    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider

    resource = Resource.create({SERVICE_NAME: service_name, SERVICE_VERSION: version})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    exporter = __import__("os").environ.get("OTEL_TRACES_EXPORTER", "").lower()
    if exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        endpoint = __import__("os").environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    elif exporter == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        _logging.getLogger(__name__).warning(
            "OTEL_TRACES_EXPORTER 未设置 —— SDK span 不会导出到任何后端；"
            "生产环境请设置 OTEL_TRACES_EXPORTER=otlp + OTEL_EXPORTER_OTLP_ENDPOINT"
        )

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app)


def get_tracer(name: str = "pocketmind"):
    from opentelemetry import trace
    global _TRACER
    if _TRACER is None:
        _TRACER = trace.get_tracer(name)
    return _TRACER


@contextmanager
def agent_span(session_id: str, label: str = "agent.run"):
    tracer = get_tracer()
    with tracer.start_as_current_span(label) as span:
        span.set_attribute("agent.session_id", session_id)
        yield span


@contextmanager
def scheduler_span(job_id: str):
    tracer = get_tracer()
    with tracer.start_as_current_span("scheduler.job") as span:
        span.set_attribute("scheduler.job_id", job_id)
        yield span
