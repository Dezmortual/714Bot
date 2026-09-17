"""Config loading and logging helpers."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = "config.yaml"


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    load_dotenv()                      # picks up .env if present
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    # environment variable overrides (12-factor friendly)
    if os.environ.get("EXCHANGE"):
        cfg["exchange"] = os.environ["EXCHANGE"]
    if os.environ.get("BOT_SYMBOL"):
        cfg["symbol"] = os.environ["BOT_SYMBOL"]
    if os.environ.get("BOT_TIMEFRAME"):
        cfg["timeframe"] = os.environ["BOT_TIMEFRAME"]
    return cfg


def setup_logging(log_file: str | None = None,
                  level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("dtc_bot")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if log_file:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.propagate = False
    return logger


def fmt_money(x: float) -> str:
    return f"{x:,.2f}"


def fmt_price(x: float) -> str:
    """Adaptive price formatting (BTC needs few decimals, alts need many)."""
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:,.4f}"
    return f"{x:.8f}".rstrip("0").rstrip(".")
