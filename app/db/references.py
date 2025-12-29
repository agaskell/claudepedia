"""Cross-reference parsing and utilities."""

import re
from uuid import UUID

# Pattern matches [[uuid]] or [[uuid|display text]]
# UUID format: 8-4-4-4-12 hex chars
REFERENCE_PATTERN = re.compile(
    r'\[\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\|([^\]]+))?\]\]',
    re.IGNORECASE
)


def extract_references(content: str) -> list[UUID]:
    """Extract all entry IDs referenced in content via [[entry-id]] syntax.

    Returns a deduplicated list of UUIDs in order of first appearance.
    """
    seen = set()
    refs = []
    for match in REFERENCE_PATTERN.finditer(content):
        entry_id = UUID(match.group(1))
        if entry_id not in seen:
            seen.add(entry_id)
            refs.append(entry_id)
    return refs


def render_references(content: str, base_url: str = "") -> str:
    """Convert [[entry-id]] and [[entry-id|text]] to HTML links.

    [[uuid]] -> <a href="{base_url}/entry/{uuid}">{uuid}</a>
    [[uuid|text]] -> <a href="{base_url}/entry/{uuid}">text</a>
    """
    def replace_ref(match: re.Match) -> str:
        entry_id = match.group(1)
        display_text = match.group(2) or entry_id[:8] + "..."  # Short ID if no text
        return f'<a href="{base_url}/entry/{entry_id}" class="cross-ref">{display_text}</a>'

    return REFERENCE_PATTERN.sub(replace_ref, content)


def strip_references(content: str) -> str:
    """Strip [[entry-id]] and [[entry-id|text]] syntax, keeping only display text.

    [[uuid]] -> (removed entirely)
    [[uuid|text]] -> text

    Useful for previews where we don't want the full link markup.
    """
    def replace_ref(match: re.Match) -> str:
        display_text = match.group(2)  # Custom display text if provided
        return display_text or ""  # Remove if no display text

    return REFERENCE_PATTERN.sub(replace_ref, content)
