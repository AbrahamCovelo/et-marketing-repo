"""Shared contract between cleaner, storage and CLI (all content is text)."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractionResult:
    url: str
    canonical_url: str
    title: str
    language: str = 'en'
    published_at: str | None = None
    modified_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # key, position, source_order, kind, text, heading_level, section_path,
    # data (table cells/list hierarchy), flags.
    blocks: list[dict[str, Any]] = field(default_factory=list)
    # block_key, source_order, url, anchor, kind, disposition.
    links: list[dict[str, Any]] = field(default_factory=list)
    # source_order, reason, text, selector, requires_review,
    # prev_block_key, next_block_key, details.
    events: list[dict[str, Any]] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    original_text: str = ''
    cleaner_version: str = '1.0.0'

    @property
    def clean_text(self) -> str:
        return '\n\n'.join(block['text'] for block in self.blocks if block['text'])
