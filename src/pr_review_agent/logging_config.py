"""Logging configuration: UTC timestamps, colorized console output, secret redaction."""

import json
import logging
import logging.config
import re
import sys
import time

_LOGGER_NAME = "pr_review_agent"

_BEARER_TOKEN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")
_GH_TOKEN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b")
_ANTHROPIC_KEY = re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{10,})\b")


class _RedactionFilter(logging.Filter):
    """Scrub GitHub/Anthropic tokens from every emitted log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Render the full message and replace secrets with [REDACTED].

        Args:
            record: The log record being emitted.

        Returns:
            bool: Always True — this filter never suppresses records.
        """
        message = record.getMessage()
        message = _BEARER_TOKEN.sub(r"\1[REDACTED]", message)
        message = _GH_TOKEN.sub("[REDACTED]", message)
        message = _ANTHROPIC_KEY.sub("[REDACTED]", message)
        record.msg = message
        record.args = None
        return True


_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_RESET = "\033[0m"


class _ColoredConsoleFormatter(logging.Formatter):
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


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record to JSON.

        Args:
            record: The log record being emitted.

        Returns:
            str: A JSON string representation of the record.
        """
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": f"{record.module}:{record.funcName}:{record.lineno}",
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


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
        "json": {
            "()": _JsonFormatter,
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


def configure_logging(verbose: bool = False, json_format: bool = False) -> None:
    """Apply the logging configuration and force UTC timestamps.

    Call once at CLI startup, before the first log statement. Output goes to
    stderr so stdout stays reserved for review output (console/JSON/markdown
    formats).

    Args:
        verbose: When True, sets the `pr_review_agent` logger to DEBUG
            instead of INFO.
        json_format: When True, emits structured JSON lines instead of the
            colorized human-readable format.
    """
    config = dict(LOGGING_CONFIG)
    config["handlers"] = dict(LOGGING_CONFIG["handlers"])
    config["handlers"]["console"] = dict(LOGGING_CONFIG["handlers"]["console"])
    config["handlers"]["console"]["formatter"] = "json" if json_format else "console"

    config["loggers"] = dict(LOGGING_CONFIG["loggers"])
    config["loggers"][_LOGGER_NAME] = dict(LOGGING_CONFIG["loggers"][_LOGGER_NAME])
    config["loggers"][_LOGGER_NAME]["level"] = "DEBUG" if verbose else "INFO"

    logging.config.dictConfig(config)
    logging.Formatter.converter = time.gmtime
