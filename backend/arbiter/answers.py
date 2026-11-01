"""What counts as a response's final answer.

Some models (e.g. Ollama's qwen3:4b even with think=false) write their
reasoning inline and end it with a lone ``</think>`` tag. For such a model, a
response that was cut off before any ``</think>`` contains reasoning only and
has no final answer. Graders and reviewers must only ever see the final
answer: reasoning can leak the system prompt (breaking blindness) and can
contain keywords that would otherwise satisfy rubric criteria.
"""

from __future__ import annotations

import json
import sqlite3

from .graders.text import strip_thinking

NO_ANSWER_NOTE = "[No final answer: the response was cut off at the max-token limit while still reasoning.]"


def inline_reasoning_models(conn: sqlite3.Connection, run_id: int) -> set[int]:
    """Run models that emitted at least one inline ``</think>`` in this run."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT run_model_id FROM responses WHERE run_id = ? AND status = 'ok'"
        " AND text LIKE '%</think>%'", (run_id,))}


def has_no_final_answer(response: dict, inline_models: set[int]) -> bool:
    text = (response.get("text") or "").lower()
    return (response.get("status") == "ok" and bool(response.get("truncated"))
            and response.get("run_model_id") in inline_models and "</think>" not in text)


def load_response(conn: sqlite3.Connection, response_id: int) -> dict | None:
    """A response row with ``truncated`` inferred for rows stored before it was tracked."""
    row = conn.execute("SELECT r.*, m.snapshot FROM responses r JOIN run_models m"
                       " ON m.id = r.run_model_id WHERE r.id = ?", (response_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("truncated") is None and d["output_tokens"]:
        limit = json.loads(d["snapshot"]).get("max_tokens")
        d["truncated"] = int(bool(limit) and d["output_tokens"] >= limit)
    return d


def display_text(response: dict, inline_models: set[int]) -> str:
    """Text to show a blind reviewer: the final answer only."""
    if has_no_final_answer(response, inline_models):
        return NO_ANSWER_NOTE
    return strip_thinking(response.get("text") or "")
