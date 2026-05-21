import logging

from gunicorn.glogging import Logger as GunicornBaseLogger  # type: ignore[import-untyped]


class SubstringFilter(logging.Filter):
    def __init__(self, substrings: list[str]):
        self.substrings = substrings

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(substring in message for substring in self.substrings)


class GunicornHealthCheckFilterLogger(GunicornBaseLogger):
    def access(self, resp, req, environ, request_time):
        if "/health" not in req.path:
            super().access(resp, req, environ, request_time)


def setup_logger(logger: logging.Logger):
    # Remove existing filters to avoid duplication
    for filter in logger.filters[:]:
        if isinstance(filter, SubstringFilter):
            logger.removeFilter(filter)

    logger.addFilter(
        SubstringFilter(
            [
                "AFC is enabled",  # Google genai library logs this
                "AFC remote call",  # Google genai library logs this
                "Item exceeds size limit",  # Langfuse
                # Self-hosted deployments run without a Langfuse server.
                # When LANGFUSE_PUBLIC_KEY is empty the SDK emits these
                # WARNINGs on every `@observe` invocation; in the autofix
                # coding loop that floods the logs at ~50 lines/s.
                "Langfuse client initialized without public_key",
                "No active span in current context",
            ]
        )
    )
    logger.setLevel(logging.INFO)


# Set up root logger and module loggers
root_loggers = [logging.getLogger(__name__), logging.getLogger()]
celery_loggers = [
    logging.getLogger("celery"),
    logging.getLogger("celery.worker"),
    logging.getLogger("celery.app.trace"),
    logging.getLogger("celery.worker.strategy"),
    logging.getLogger("celery.worker.consumer"),
    logging.getLogger("celery.concurrency"),
]
library_loggers = [logging.getLogger("langfuse")]
loggers = root_loggers + celery_loggers + library_loggers
for logger in loggers:
    setup_logger(logger)
