"""
Minimal color-coded console logging for the RAG backend.
Slim one-line messages; no INFO spam. Set NO_COLOR=1 to disable ANSI.
"""
import os
import sys


def _use_color() -> bool:
    return (
        hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and not os.getenv("NO_COLOR")
    )


def _c(code: str) -> str:
    return f"\033[{code}m" if _use_color() else ""


def dim(s: str) -> str:
    return f"{_c('90')}{s}{_c('0')}"


def cyan(s: str) -> str:
    return f"{_c('36')}{s}{_c('0')}"


def green(s: str) -> str:
    return f"{_c('32')}{s}{_c('0')}"


def yellow(s: str) -> str:
    return f"{_c('33')}{s}{_c('0')}"


def red(s: str) -> str:
    return f"{_c('31')}{s}{_c('0')}"
