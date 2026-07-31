"""Logging configuration: UTC timestamps, colorized console output, secret redaction."""

import copy
import logging
import logging.config
import re
import sys
import time
from typing import Any

_LOGGER_NAME = "pr_review_agent"

_BEARER_TOKEN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
_GH_TOKEN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b")
_GH_FINE_GRAINED_PAT = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_ANTHROPIC_KEY = re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{10,})\b")
_OPENROUTER_KEY = re.compile(r"\b(sk-or-(?:v\d+-)?[A-Za-z0-9_-]{10,})\b")


def redact(text: str) -> str:
    """Replace GitHub/Anthropic/OpenRouter secrets in `text` with `[REDACTED]`.

    Shared by the log-record filter, the formatters' traceback rendering, and
    subprocess error paths that never pass through a logging filter at all.

    Args:
        text: Arbitrary text that may contain tokens — a log message, a
            rendered traceback, or captured subprocess stderr.

    Returns:
        str: The same text with every recognized token pattern replaced.
    """
    text = _BEARER_TOKEN.sub(r"\1[REDACTED]", text)
    text = _GH_TOKEN.sub("[REDACTED]", text)
    text = _GH_FINE_GRAINED_PAT.sub("[REDACTED]", text)
    text = _ANTHROPIC_KEY.sub("[REDACTED]", text)
    return _OPENROUTER_KEY.sub("[REDACTED]", text)


class _RedactionFilter(logging.Filter):
    """Scrub GitHub/Anthropic/OpenRouter tokens from every emitted log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Render the full message and replace secrets with [REDACTED].

        Args:
            record: The log record being emitted.

        Returns:
            bool: Always True — this filter never suppresses records.
        """
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class _RedactingFormatter(logging.Formatter):
    """Base formatter that scrubs secrets out of rendered tracebacks.

    The redaction filter only sees `record.getMessage()`; `exc_info` is
    rendered separately by the formatter and would otherwise bypass it.
    """

    def formatException(self, ei: Any) -> str:
        """Render the exception info, then redact secrets from it.

        Args:
            ei: The `sys.exc_info()`-style tuple carried by the record.

        Returns:
            str: The formatted traceback with tokens replaced.
        """
        return redact(super().formatException(ei))


_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_RESET = "\033[0m"


class _ColoredConsoleFormatter(_RedactingFormatter):
    """Console formatter that colorizes the log level name."""

    def format(self, record: logging.LogRecord) -> str:
        """Apply ANSI color to levelname before delegating to standard format.

        Colors a temporary value rather than permanently mutating the shared
        LogRecord, and only emits ANSI codes when stderr is a TTY so
        piped/captured CI logs stay clean.

        Args:
            record: The log record being emitted.

        Returns:
            str: Formatted log line with colorized level.
        """
        original_levelname = record.levelname
        color = _LEVEL_COLORS.get(original_levelname, "") if sys.stderr.isatty() else ""
        padded_levelname = f"{original_levelname:<8}"
        record.levelname = (
            f"{color}{padded_levelname}{_RESET}" if color else padded_levelname
        )
        try:
            return super().format(record)
        finally:
            record.levelname = original_levelname


_CONSOLE_FORMAT = (
    "[%(asctime)s][%(levelname)-8s] %(module)s:%(funcName)s:%(lineno)d | %(message)s"
)

LOGGING_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redaction": {"()": _RedactionFilter},
    },
    "formatters": {
        "console": {
            "()": _ColoredConsoleFormatter,
            "format": _CONSOLE_FORMAT,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "console",
            "filters": ["redaction"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        _LOGGER_NAME: {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


def configure_logging(verbose: bool = False) -> None:
    """Apply the logging configuration and force UTC timestamps.

    Call once at CLI startup, before the first log statement. Output goes to
    stderr so stdout stays reserved for review output (console/JSON/markdown
    formats).

    Args:
        verbose: When True, sets the `pr_review_agent` logger to DEBUG
            instead of INFO.
    """
    # dictConfig writes converted filter/formatter objects back into the dict
    # it is given, so hand it a deep copy and keep LOGGING_CONFIG pristine for
    # any later call.
    config = copy.deepcopy(LOGGING_CONFIG)
    config["loggers"][_LOGGER_NAME]["level"] = "DEBUG" if verbose else "INFO"

    logging.config.dictConfig(config)
    logging.Formatter.converter = time.gmtime
