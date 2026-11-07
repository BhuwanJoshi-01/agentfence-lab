# SPDX-License-Identifier: Apache-2.0
"""Hardened YAML loading for configs and scenario manifests.

``yaml.safe_load`` alone is not strict enough for security-relevant input:

* duplicate keys silently overwrite earlier values (``no_host_mounts: true``
  followed later by ``no_host_mounts: false``);
* anchors and aliases allow exponential expansion ("billion laughs");
* arbitrarily large documents are parsed fully.

This loader rejects all three and requires a mapping at the top level.
"""

from __future__ import annotations

from typing import Any, Final

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

MAX_DOCUMENT_BYTES: Final = 256 * 1024


class StrictYAMLError(ValueError):
    """Input is not acceptable YAML for AgentFence. Message names the source and reason."""


class _StrictSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if isinstance(event, AliasEvent):
            raise yaml.composer.ComposerError(problem="YAML aliases are not allowed")
        if getattr(event, "anchor", None) is not None:
            raise yaml.composer.ComposerError(problem="YAML anchors are not allowed")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_mapping(text: str | bytes, *, source: str) -> dict[str, Any]:
    """Parse one YAML document that must be a mapping with string keys."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise StrictYAMLError(
            f"{source}: document is {len(raw)} bytes; the limit is {MAX_DOCUMENT_BYTES}"
        )
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictYAMLError(f"{source}: not valid UTF-8 ({exc.reason})") from exc
    try:
        documents = list(yaml.load_all(decoded, Loader=_StrictSafeLoader))
    except yaml.YAMLError as exc:
        raise StrictYAMLError(f"{source}: {exc}") from exc
    if len(documents) != 1:
        raise StrictYAMLError(f"{source}: expected exactly one YAML document, got {len(documents)}")
    data = documents[0]
    if not isinstance(data, dict):
        raise StrictYAMLError(f"{source}: top level must be a mapping, got {type(data).__name__}")
    bad_keys = [k for k in _iter_keys(data) if not isinstance(k, str)]
    if bad_keys:
        raise StrictYAMLError(f"{source}: all mapping keys must be strings, got {bad_keys[0]!r}")
    return data


def _iter_keys(value: object) -> list[object]:
    keys: list[object] = []
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            keys.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return keys
