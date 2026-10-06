"""Logging that redacts secrets from every record."""

from __future__ import annotations

import logging

from openapply.security.redact import redact

LOGGER_NAME = "openapply"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def configure_logging(*, verbose: bool = False, debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.propagate = False
