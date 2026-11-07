"""Text scrubbing and allowlist-based field redaction (AF-0106, FR-010).

This is the first layer of redaction, shared by logs now and reports later:

* :func:`scrub_text` removes synthetic secret markers, common credential shapes,
  user home paths and terminal control sequences from free text.
* :func:`redact_fields` passes only allowlisted field names through (scrubbed);
  everything else becomes ``[REDACTED]``. Unknown fields are hidden by default
  instead of shown by default.

Pattern matching is a safety net, not a guarantee. The primary defense is that the
lab never handles real credentials at all (ADR-009).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

REDACTED: Final = "[REDACTED]"
SYNTHETIC_SECRET_ALIAS: Final = "[SYNTHETIC_SECRET]"  # noqa: S105 - alias label
CREDENTIAL_ALIAS: Final = "[CREDENTIAL]"
HOME_ALIAS: Final = "$HOME"

_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_SYNTHETIC_MARKER = re.compile(r"AF_FAKE_[A-Za-z0-9_\-]+")
_CREDENTIAL_PATTERNS: Final = (
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"),  # OpenAI / Anthropic style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key IDs
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),  # Slack
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google API keys
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"(?i)\b(bearer)(\s+)(?!\[)[A-Za-z0-9._~+/\-]{8,}=*"),
    re.compile(
        r"(?i)\b(token|api[_-]?key|password|passwd|secret|access[_-]?key)(\s*[:=]\s*)"
        r"(?!\[)[^\s'\"]{6,}"
    ),
)
_USER_HOME = re.compile(r"(?:/home|/Users)/[^/\s:'\"]+")
# CSI / OSC / other ESC sequences, then remaining C0 controls except \t and \n, plus DEL
# and C1 controls. Prevents terminal injection from untrusted fixture or agent text.
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Invisible formatting characters: zero-width, bidi embeddings/overrides/isolates, BOM.
# They can make displayed text differ from its bytes ("Trojan Source").
_FORMAT = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


def _credential_sub(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 2:  # keyword form: keep the keyword
        return f"{match.group(1)}{match.group(2)}{CREDENTIAL_ALIAS}"
    return CREDENTIAL_ALIAS


def scrub_text(text: str, *, home: str | None = None) -> str:
    """Return ``text`` with secrets, home paths and control sequences removed."""
    out = _ANSI.sub("", text)
    out = _CONTROL.sub("", out)
    out = _FORMAT.sub("", out)
    out = _PEM_BLOCK.sub(CREDENTIAL_ALIAS, out)
    out = _SYNTHETIC_MARKER.sub(SYNTHETIC_SECRET_ALIAS, out)
    for pattern in _CREDENTIAL_PATTERNS:
        out = pattern.sub(_credential_sub, out)
    if home and home not in {"/", ""}:
        out = out.replace(home.rstrip("/"), HOME_ALIAS)
    return _USER_HOME.sub(HOME_ALIAS, out)


Scalar = str | int | float | bool | None


def redact_fields(
    fields: Mapping[str, object], allow: Iterable[str], *, home: str | None = None
) -> dict[str, Scalar]:
    """Allowlist redaction: only names in ``allow`` keep a (scrubbed) value."""
    allowed = frozenset(allow)
    result: dict[str, Scalar] = {}
    for key in sorted(fields):
        safe_key = scrub_text(str(key), home=home)
        if key not in allowed:
            result[safe_key] = REDACTED
            continue
        value = fields[key]
        if value is None or isinstance(value, bool | int | float):
            result[safe_key] = value
        else:
            result[safe_key] = scrub_text(str(value), home=home)
    return result
