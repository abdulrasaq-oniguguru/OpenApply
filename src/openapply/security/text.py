"""Sanitising untrusted text before it is stored or shown in a terminal."""

from __future__ import annotations

import re

# C0 controls (except tab/newline), DEL and C1 controls. ESC (0x1b) is in here, so ANSI
# escape sequences lose their escape byte and cannot move the cursor or recolour output.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Bidirectional override/isolate characters can make text display differently from its content.
_BIDI_RE = re.compile("[‪-‮⁦-⁩‎‏]")


def strip_control_chars(text: str) -> str:
    """Remove control and bidi-override characters; keep tabs and newlines."""
    return _BIDI_RE.sub("", _CONTROL_RE.sub("", text))
