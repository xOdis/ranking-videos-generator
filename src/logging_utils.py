"""Logging utilities with structured output and optional rich console formatting."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional


class ColorFormatter(logging.Formatter):
    """Minimal ANSI color formatter for console handlers on Windows 10+ terminals."""

    GREY = "\x1b[38;20m"
    BLUE = "\x1b[34;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD = "\x1b[1m"
    RESET = "\x1b[0m"

    LEVEL_COLORS = {
        logging.DEBUG: GREY,
        logging.INFO: BLUE,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD + RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, self.GREY)
        fmt = f"{color}%(asctime)s [%(levelname)s] %(name)s: %(message)s{self.RESET}"
        return logging.Formatter(fmt, datefmt="%H:%M:%S").format(record)


def configure_logging(
    level: str | None = None,
    log_file: Optional[Path] = None,
    logger_name: str = "ranking_generator",
) -> logging.Logger:
    """Configure and return the application root logger.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR). Falls back to
            env var ``LOG_LEVEL`` or ``INFO``.
        log_file: Optional path to a rotating-ish log file (plain append).
        logger_name: Name of the logger to configure.

    Returns:
        The configured :class:`logging.Logger`.
    """
    level_str = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    numeric = getattr(logging, level_str, logging.INFO)

    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric)
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric)
    console.setFormatter(ColorFormatter())
    logger.addHandler(console)

    if log_file is not None:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(numeric)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "ranking_generator") -> logging.Logger:
    """Return an existing logger or configure a default one."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        configure_logging(logger_name=name)
    return logger