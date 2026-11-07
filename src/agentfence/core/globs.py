# SPDX-License-Identifier: Apache-2.0
"""Workspace-relative glob matching for scenario boundaries.

Semantics (deliberately small and predictable, unlike :mod:`fnmatch`):

* ``**`` matches zero or more whole path segments (``src/**`` matches ``src/a/b.py``
  and also ``src`` itself);
* ``*`` matches within one segment, never across ``/``;
* ``?`` matches one character other than ``/``;
* ``[abc]`` / ``[a-z]`` character classes within one segment (``[!x]`` negates);
* everything else is literal. Matching is case-sensitive.

Patterns are validated by :data:`agentfence.core.types.RelativePattern` before they get
here; this module only turns them into anchored regular expressions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache


@lru_cache(maxsize=512)
def compile_glob(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    segments = pattern.split("/")
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            # Zero or more segments. Trailing '**' also matches the directory itself.
            if last:
                parts.append(".*" if index == 0 else "(?:/.*)?")
            else:
                parts.append("(?:[^/]+/)*")
            continue
        parts.append(_segment(segment) + ("" if last else "/"))
    regex = "".join(parts)
    # 'src/**' compiled to 'src/(?:/.*)?' would need the slash folded in.
    regex = regex.replace("/(?:/.*)?", "(?:/.*)?")
    return re.compile(rf"\A{regex}\Z")


def _segment(segment: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            end = segment.find("]", i + 1)
            if end == -1:
                out.append(re.escape(ch))
            else:
                body = segment[i + 1 : end]
                negate = body.startswith("!")
                body = body[1:] if negate else body
                cls = re.escape(body).replace(r"\-", "-")
                out.append(f"[{'^' if negate else ''}{cls}]" if body else re.escape("[]"))
                i = end
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def matches(path: str, pattern: str) -> bool:
    return compile_glob(pattern).match(path) is not None


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)
