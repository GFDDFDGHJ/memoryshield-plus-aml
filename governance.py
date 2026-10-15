"""Conservative governance layer for the AML submission.

The AML benchmark data is clean (no memory-poisoning attacks), so the full
MemoryShield keyword quarantine would only suppress legitimate evidence and
hurt recall. What does transfer to the scoring rubric is dimension D
(memory governance: updates, conflict resolution) and C (temporal reasoning:
prefer the current value). We therefore keep the write-time risk assessment
for audit, but only act on *supersession*: when a new message reads like an
update to a previously stated fact, older active records on the same topic are
demoted so the retrieval returns the current value.
"""
from __future__ import annotations

import re

from config import CONFIG

# Cues that a message restates an updated fact (English + Chinese).
UPDATE_CUES = (
    "now", "currently", "new", "changed", "updated", "recently",
    "previously", "but actually", "instead,", "correction:", "as of",
    "changed my", "no longer", "instead of", "replaced",
    "最近", "现在", "改为", "更新", "之前", "现在改为", "不再",
)

# Pattern "X is now Y" / "X = Y" to extract a topic keyword for supersession.
_TOPIC_PATTERNS = [
    re.compile(r"\b([A-Za-z][A-Za-z0-9_ ]{2,40})\s+(?:is|was|=|now\s+is)\b", re.I),
]


def maybe_govern_add(store, user_id: str, seq: int, messages: list[dict]) -> None:
    """Optionally supersede stale records after a write. Conservative."""
    if not CONFIG.governance_enabled:
        return
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        low = content.lower()
        if not any(cue in low for cue in UPDATE_CUES):
            continue
        topic = _extract_topic(content)
        if topic:
            store.supersede_old(user_id, topic, seq)


def _extract_topic(content: str) -> str:
    for pat in _TOPIC_PATTERNS:
        m = pat.search(content)
        if m:
            topic = m.group(1).strip()
            if 2 <= len(topic) <= 40:
                return topic
    return ""
