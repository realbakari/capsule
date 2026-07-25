"""Zero-dependency ANSI color formatting helper for Capsule CLI.

Respects sys.stdout.isatty() and the NO_COLOR environment variable.
"""

from __future__ import annotations

import os
import sys

_USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _wrap(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(text: str) -> str:
    return _wrap("32", text)


def red(text: str) -> str:
    return _wrap("31", text)


def yellow(text: str) -> str:
    return _wrap("33", text)


def cyan(text: str) -> str:
    return _wrap("36", text)


def bold(text: str) -> str:
    return _wrap("1", text)


def dim(text: str) -> str:
    return _wrap("2", text)


def pass_badge() -> str:
    return bold(green("PASS"))


def fail_badge() -> str:
    return bold(red("FAIL"))


def warn_badge() -> str:
    return bold(yellow("WARN"))


def info_badge() -> str:
    return bold(cyan("INFO"))
