"""
Cost tracking module.
Records token usage and calculates USD cost after every Claude API call.
"""

import json
import os
from datetime import datetime

COSTS_PATH = os.path.join(os.path.dirname(__file__), "costs.json")

# Pricing per 1M tokens (USD)
PRICING = {
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00},
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00},
}


def load() -> dict:
    if not os.path.exists(COSTS_PATH):
        return {"runs": [], "total_cost_usd": 0.0}
    with open(COSTS_PATH, "r") as f:
        return json.load(f)


def save(data: dict):
    with open(COSTS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = PRICING.get(model, {"input": 5.00, "output": 25.00})
    return (input_tokens / 1_000_000 * pricing["input"]) + \
           (output_tokens / 1_000_000 * pricing["output"])


def record(
    agent: str,          # "filter" or "digest"
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    emails_processed: int = 0,
    emails_filtered: int = 0,
):
    data = load()
    cost = calculate_cost(model, input_tokens, output_tokens)

    run = {
        "timestamp": datetime.now().isoformat(),
        "agent": agent,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cost_usd": round(cost, 6),
        "emails_processed": emails_processed,
        "emails_filtered": emails_filtered,
    }

    data["runs"].append(run)
    data["total_cost_usd"] = round(data["total_cost_usd"] + cost, 6)
    save(data)
    return cost
