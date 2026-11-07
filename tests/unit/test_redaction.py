# SPDX-License-Identifier: Apache-2.0
"""Redaction tests (AF-0106, FR-010).

Credential-shaped test strings are assembled at runtime so that no literal token
pattern lives in the repository (secret scanners and push protection stay quiet,
and nobody mistakes a fixture for a leaked key).
"""

from __future__ import annotations

import pytest

from agentfence.core.redaction import (
    CREDENTIAL_ALIAS,
    HOME_ALIAS,
    REDACTED,
    SYNTHETIC_SECRET_ALIAS,
    redact_fields,
    scrub_text,
)


def fake(prefix: str, body: str) -> str:
    return prefix + body


CREDENTIAL_SAMPLES = [
    fake("sk-", "a" * 32),
    fake("sk-" + "ant-", "b" * 40),
    fake("sk-" + "proj-", "c" * 40),
    fake("gh" + "p_", "D" * 36),
    fake("gh" + "s_", "E" * 36),
    fake("github" + "_pat_", "F" * 40),
    fake("AK" + "IA", "ABCDEFGHIJKLMNOP"),
    fake("xo" + "xb-", "1234567890-abcdef"),
    fake("AI" + "za", "G" * 35),
    fake("ey" + "J", "hbGciOiJIUzI1NiJ9")
    + "."
    + fake("ey" + "J", "zdWIiOiIxMjM0NTY3ODkwIn0")
    + ".sig",
]


@pytest.mark.parametrize("secret", CREDENTIAL_SAMPLES)
def test_credential_shapes_removed(secret: str) -> None:
    out = scrub_text(f"value: {secret} end")
    assert secret not in out
    assert CREDENTIAL_ALIAS in out
    assert out.endswith(" end")


def test_pem_private_key_block_removed() -> None:
    begin = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
    end = "-----END OPENSSH " + "PRIVATE KEY-----"
    text = f"before\n{begin}\nAAAAB3NzaC1\nmore\n{end}\nafter"
    out = scrub_text(text)
    assert "AAAAB3NzaC1" not in out
    assert out == f"before\n{CREDENTIAL_ALIAS}\nafter"


def test_unterminated_pem_block_removed_to_end() -> None:
    begin = "-----BEGIN RSA " + "PRIVATE KEY-----"
    assert scrub_text(f"x {begin}\nsecretbytes") == f"x {CREDENTIAL_ALIAS}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Authorization: Bearer abcdefghijklmnop", f"Authorization: Bearer {CREDENTIAL_ALIAS}"),
        ("password=hunter2hunter2", f"password={CREDENTIAL_ALIAS}"),
        ("API-KEY: abcdefgh123", f"API-KEY: {CREDENTIAL_ALIAS}"),
        ("token validation failed", "token validation failed"),
        ("the secret santa list", "the secret santa list"),
    ],
)
def test_keyword_forms(text: str, expected: str) -> None:
    assert scrub_text(text) == expected


def test_synthetic_marker_aliased_not_double_redacted() -> None:
    out = scrub_text("token AF_FAKE_TOKEN_9f3a and secret = AF_FAKE_X")
    assert out == f"token {SYNTHETIC_SECRET_ALIAS} and secret = {SYNTHETIC_SECRET_ALIAS}"


def test_home_paths_aliased() -> None:
    out = scrub_text("/srv/me/x and /home/alice/.ssh and /Users/bob/p", home="/srv/me")
    assert out == f"{HOME_ALIAS}/x and {HOME_ALIAS}/.ssh and {HOME_ALIAS}/p"


def test_root_home_does_not_mangle_everything() -> None:
    assert scrub_text("/etc/hosts", home="/") == "/etc/hosts"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\x1b[31mred\x1b[0m", "red"),
        ("a\x1b]0;evil title\x07b", "ab"),
        ("a\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\b", "alinkb"),
        ("bell\x07 nul\x00 bs\x08 del\x7f c1\x9b", "bell nul bs del c1"),
        ("keep\ttabs\nand newlines", "keep\ttabs\nand newlines"),
        ("cr\roverwrite", "croverwrite"),
    ],
)
def test_terminal_control_sequences_stripped(text: str, expected: str) -> None:
    assert scrub_text(text) == expected


def test_ordinary_text_unchanged() -> None:
    text = "AF-README-001 PASS utility=COMPLETE evidence=6 WORKSPACE/src/app.py"
    assert scrub_text(text) == text


def test_redact_fields_is_allowlist_based() -> None:
    out = redact_fields(
        {"run_id": "r1", "count": 3, "ok": True, "none": None, "env": "PATH=/x", "ratio": 0.5},
        {"run_id", "count", "ok", "none", "ratio"},
    )
    assert out == {
        "count": 3,
        "env": REDACTED,
        "none": None,
        "ok": True,
        "ratio": 0.5,
        "run_id": "r1",
    }


def test_redact_fields_scrubs_allowed_values_and_keys() -> None:
    out = redact_fields(
        {"path_alias": "/home/carol/x", "AF_FAKE_KEY": "v", "status": ["a", 1]},
        {"path_alias", "status"},
    )
    assert out["path_alias"] == f"{HOME_ALIAS}/x"
    assert out[SYNTHETIC_SECRET_ALIAS] == REDACTED
    assert out["status"] == "['a', 1]"
