"""Console helpers.

The legacy pipeline prints Greek letters and emoji. On Windows the default
console encoding (cp1252) can't encode them and ``print`` raises
UnicodeEncodeError, so entry points switch stdout/stderr to UTF-8.
"""
import sys


def ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
