"""Marginal-vote product index: one served entry point over the v5 artifacts (v5 #5).

Composes the product's served surfaces into a single index with per-artifact
content hashes (tamper-evidence) and a one-line description each: the
defection-watch (who breaks ranks), venue-score (where a bill passes), the weekly
marginal-votes brief (who to work, with evidence), and the forward prediction
registry (the track record). Dependency-free server-rendered HTML.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class ServedArtifact:
    title: str
    href: str
    description: str
    content_sha256: str

    @classmethod
    def of(cls, *, title: str, href: str, description: str, content: str) -> ServedArtifact:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return cls(title=title, href=href, description=description, content_sha256=digest)


def render_product_index(artifacts: list[ServedArtifact]) -> str:
    """Render the product homepage linking each served artifact with its hash."""
    style = (
        "body{font:15px system-ui,sans-serif;margin:2rem;max-width:60rem;color:#111}"
        "h1{font-size:1.5rem}.card{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}"
        ".muted{color:#666}a{color:#1a5fb4;font-weight:600}code{background:#f2f2f2;padding:.1rem .3rem}"
    )
    cards = "".join(
        '<div class="card">'
        f'<div><a href="{escape(a.href)}">{escape(a.title)}</a></div>'
        f"<p>{escape(a.description)}</p>"
        f'<div class="muted">sha256 <code>{escape(a.content_sha256[:16])}…</code></div>'
        "</div>"
        for a in artifacts
    )
    body = cards or '<p class="muted">No artifacts.</p>'
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Psephos America — Marginal-Vote Product</title>"
        f"<style>{style}</style></head><body>"
        "<h1>Psephos America — Marginal-Vote Product</h1>"
        '<p class="muted">Who will break with their party, where a bill passes, who to '
        "work this week, and our standing forward track record — every surface cited and "
        "content-hashed.</p>"
        f"{body}</body></html>"
    )
