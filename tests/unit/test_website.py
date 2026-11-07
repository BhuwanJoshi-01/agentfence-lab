# SPDX-License-Identifier: Apache-2.0
"""The project website (website/, published by .github/workflows/pages.yml) stays honest
and self-contained: every local link resolves, nothing loads from elsewhere, no scripts,
a restrictive CSP, and a valid 1280x640 social card."""

from __future__ import annotations

import re
import struct
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "website"
PAGE = (SITE / "index.html").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str, str]] = []
        self.tags: list[str] = []
        self.meta: dict[str, str] = {}
        self.images: list[dict[str, str | None]] = []
        self.links: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        values = dict(attrs)
        for attr in ("href", "src"):
            if values.get(attr):
                self.refs.append((tag, attr, str(values[attr])))
        if tag == "meta":
            key = values.get("property") or values.get("name") or values.get("http-equiv")
            if key:
                self.meta[key] = values.get("content") or ""
        if tag == "img":
            self.images.append(values)
        if tag == "link":
            self.links.append(values)


PARSED = Collector()
PARSED.feed(PAGE)


def deployed(ref: str) -> Path:
    """Where the Pages workflow takes a relative reference from."""
    if ref.startswith("images/"):
        return ROOT / "docs" / ref
    return SITE / ref


def test_every_local_reference_exists_and_is_deployed() -> None:
    local = [ref for _, _, ref in PARSED.refs if not ref.startswith(("https://", "#"))]
    assert local
    for ref in local:
        if ref == "./":
            continue
        assert deployed(ref).is_file(), ref
        assert Path(ref).name in WORKFLOW, f"{ref} is not copied by pages.yml"


def test_nothing_loads_from_another_origin_and_there_are_no_scripts() -> None:
    assert "script" not in PARSED.tags
    assert not [i["src"] for i in PARSED.images if str(i["src"]).startswith("http")]
    remote_links = [link for link in PARSED.links if str(link["href"]).startswith("http")]
    assert [link["rel"] for link in remote_links] == ["canonical"]  # a URL, never fetched
    csp = PARSED.meta["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script" not in csp
    assert "fonts.googleapis" not in PAGE and "@import" not in (SITE / "styles.css").read_text()


def test_images_have_alt_text_and_real_dimensions() -> None:
    for img in PARSED.images:
        assert img.get("alt") and len(str(img["alt"])) > 20, img
        path = deployed(str(img["src"]))
        head = path.read_bytes()[:32]
        size = (
            struct.unpack("<HH", head[6:10])
            if path.suffix == ".gif"
            else struct.unpack(">II", head[16:24])
        )
        assert (int(str(img["width"])), int(str(img["height"]))) == size, path.name


def test_social_card_is_1280_by_640_and_announced() -> None:
    card = SITE / "social-card.png"
    assert struct.unpack(">II", card.read_bytes()[16:24]) == (1280, 640)
    assert (ROOT / "docs" / "images" / "social-card.png").read_bytes() == card.read_bytes()
    assert (
        PARSED.meta["og:image"] == "https://bhuwanjoshi-01.github.io/agentfence-lab/social-card.png"
    )
    assert PARSED.meta["twitter:card"] == "summary_large_image"
    assert PARSED.meta["og:image:alt"]


def test_the_page_states_its_limits() -> None:
    text = re.sub(r"<[^>]+>", " ", PAGE)
    for claim in (
        "mock agent",
        "independent security review",
        "not a certification",
        "What it does not claim",
    ):
        assert claim in text, claim
