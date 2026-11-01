"""Text normalisation and final-answer extraction shared by the graders."""

from __future__ import annotations

import re
import string
import unicodedata

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# "Answer: 42", "**Final answer:** 42", "answer - 42" ... last one wins.
ANSWER_LINE_RE = re.compile(
    r"^[\s>*_#-]*(?:final\s+)?answer\s*[:\-–]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
# A fraction "3/4", or a number with optional thousands/lakh separators
# ("1,250", "1,25,000") and decimals. A leading minus only counts when it is
# not glued to a preceding word ("step-2" is not -2).
NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w.])(?P<num>-?\d+)\s*/\s*(?P<den>\d+)(?![\d.])"
    r"|(?<![\w.])(?P<value>-?(?:\d+(?:,\d{2,3})+|\d+)(?:\.\d+)?|-?\.\d+)"
)
CURRENCY = "$₹€£¥"
ARTICLES = {"a", "an", "the"}


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks some reasoning models emit.

    Some builds (e.g. Ollama's qwen3:4b with think=false) return the reasoning
    inline with only a closing </think> tag; everything before it is dropped.
    """
    text = THINK_RE.sub("", text or "")
    lower = text.lower()
    if "</think>" in lower and "<think>" not in lower:
        text = text[lower.rindex("</think>") + len("</think>"):]
    # An unterminated <think> means the model ran out of tokens mid-thought.
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()


def answer_line(text: str) -> str | None:
    """Content of the last 'Answer: ...' line, or None if absent."""
    matches = ANSWER_LINE_RE.findall(strip_thinking(text))
    if not matches:
        return None
    return matches[-1].strip().strip("*_`").strip()


def final_segment(text: str) -> str:
    """Best guess at the final answer: the answer line, else the last non-empty line."""
    line = answer_line(text)
    if line is not None:
        return line
    lines = [ln.strip() for ln in strip_thinking(text).splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation/articles, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("`", " ").replace("*", " ")
    text = text.translate(str.maketrans({c: " " for c in string.punctuation}))
    words = [w for w in text.split() if w not in ARTICLES]
    return " ".join(words)


def parse_number(token: str) -> float | None:
    token = token.replace("−", "-").replace(",", "").strip()
    try:
        return float(token)
    except ValueError:
        return None


def extract_number(text: str) -> float | None:
    """Final numeric answer: last number in the answer line, else in the whole text.

    Handles thousands separators ("1,250"), currency symbols ("₹1,250.50",
    "$3") and simple fractions ("3/4").
    """
    for source in (answer_line(text), strip_thinking(text)):
        if not source:
            continue
        cleaned = source.replace("−", "-")
        for sym in CURRENCY:
            cleaned = cleaned.replace(sym, " ")
        cleaned = re.sub(r"\b(?:rs|inr|usd)\.?\s*", " ", cleaned, flags=re.IGNORECASE)
        matches = list(NUMBER_TOKEN_RE.finditer(cleaned))
        if not matches:
            continue
        last = matches[-1]
        if last.group("num") is not None:
            den = int(last.group("den"))
            return int(last.group("num")) / den if den else None
        return parse_number(last.group("value"))
    return None


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", strip_thinking(text)))


def contains_phrase(text: str, phrase: str) -> bool:
    """Case-insensitive phrase match on word boundaries (punctuation-tolerant)."""
    hay = f" {normalize(text)} "
    needle = normalize(phrase)
    return bool(needle) and f" {needle} " in hay
