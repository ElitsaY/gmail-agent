"""
Shared memory module for the Gmail agent.
Persists sender stats, learned filter rules, frequent contacts,
and pending adaptive suggestions across runs.
"""

import json
import os
from datetime import datetime

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "memory.json")

DEFAULT_MEMORY = {
    "learned_filters": [],        # domains/senders Claude learned to filter
    "sender_stats": {},           # per-sender: times_filtered, times_kept, last_seen
    "frequent_contacts": [],      # senders whose emails are consistently kept + read
    "pending_suggestions": [],    # suggestions sent in digest, waiting for user reply
    "last_updated": None,
}


def load() -> dict:
    if not os.path.exists(MEMORY_PATH):
        return dict(DEFAULT_MEMORY)
    with open(MEMORY_PATH, "r") as f:
        data = json.load(f)
    # backfill any missing keys
    for k, v in DEFAULT_MEMORY.items():
        data.setdefault(k, v)
    return data


def save(memory: dict):
    memory["last_updated"] = datetime.now().isoformat()
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)


def record_filtered(memory: dict, sender: str):
    stats = memory["sender_stats"].setdefault(sender, {"times_filtered": 0, "times_kept": 0, "last_seen": None})
    stats["times_filtered"] += 1
    stats["last_seen"] = datetime.now().date().isoformat()


def record_kept(memory: dict, sender: str):
    stats = memory["sender_stats"].setdefault(sender, {"times_filtered": 0, "times_kept": 0, "last_seen": None})
    stats["times_kept"] += 1
    stats["last_seen"] = datetime.now().date().isoformat()
    # promote to frequent contact if kept 5+ times and never filtered
    if stats["times_kept"] >= 5 and stats["times_filtered"] == 0:
        if sender not in memory["frequent_contacts"]:
            memory["frequent_contacts"].append(sender)


def is_learned_filter(memory: dict, sender: str) -> bool:
    sender_lower = sender.lower()
    return any(f.lower() in sender_lower for f in memory["learned_filters"])


def get_filter_candidates(memory: dict, min_filtered: int = 3) -> list[dict]:
    """Return senders filtered 3+ times that aren't already in learned_filters."""
    candidates = []
    for sender, stats in memory["sender_stats"].items():
        if (
            stats["times_filtered"] >= min_filtered
            and stats["times_kept"] == 0
            and not is_learned_filter(memory, sender)
        ):
            candidates.append({"sender": sender, "times_filtered": stats["times_filtered"]})
    return sorted(candidates, key=lambda x: x["times_filtered"], reverse=True)
