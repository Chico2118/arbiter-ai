"""Pydantic models for API input validation. All strings and lists are bounded."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROVIDERS = ("ollama", "openai", "anthropic", "gemini", "openai_compatible")
CATEGORIES_HINT = (
    "math", "logic", "knowledge", "instruction", "code", "safety", "explanation",
)
DIFFICULTIES = ("easy", "medium", "hard")
FAILURE_TAGS = (
    "hallucination", "arithmetic_error", "wrong_reasoning",
    "ignored_instruction", "wrong_refusal", "unsafe",
)
REASON_TAGS = ("more_accurate", "followed_instructions", "clearer", "hallucinated", "unsafe")
VOTES = ("left", "right", "tie", "both_bad")

KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

ShortStr = Annotated[str, Field(max_length=200)]
PromptStr = Annotated[str, Field(min_length=1, max_length=20_000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

class EndpointIn(Strict):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    provider: Literal["ollama", "openai", "anthropic", "gemini", "openai_compatible"]
    model: Annotated[str, Field(min_length=1, max_length=120)]
    base_url: Annotated[str, Field(max_length=300)] = ""
    system_prompt: Annotated[str, Field(max_length=8_000)] = ""
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.0
    max_tokens: Annotated[int, Field(ge=1, le=32_000)] = 512
    think: bool | None = None
    price_in: Annotated[float, Field(ge=0, le=1_000)] = 0.0
    price_out: Annotated[float, Field(ge=0, le=1_000)] = 0.0
    timeout_s: Annotated[float, Field(ge=5, le=1_800)] = 300.0

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^https?://[^\s]+$", v):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")

    @model_validator(mode="after")
    def _compat_needs_url(self) -> "EndpointIn":
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError("openai_compatible endpoints need a base_url")
        return self


class ApiKeyIn(Strict):
    api_key: Annotated[str, Field(min_length=8, max_length=500)]

    @field_validator("api_key")
    @classmethod
    def _no_whitespace(cls, v: str) -> str:
        v = v.strip()
        if any(c.isspace() for c in v):
            raise ValueError("API key must not contain whitespace")
        return v


# --------------------------------------------------------------------------
# Graders and criteria
# --------------------------------------------------------------------------

class Constraint(Strict):
    kind: Literal[
        "bullet_count", "max_words", "min_words", "valid_json", "starts_with",
        "ends_with", "must_include", "must_not_include", "line_count", "lowercase",
        "no_commas",
    ]
    value: Any = None
    required_keys: list[ShortStr] | None = None


class Grader(Strict):
    type: Literal[
        "exact", "numeric", "choice", "regex", "contains", "constraints", "refusal",
        "rubric", "none",
    ]
    answer: Any = None
    aliases: list[ShortStr] = Field(default_factory=list, max_length=20)
    tolerance: Annotated[float, Field(ge=0)] = 0.0
    relative: bool = False
    pattern: Annotated[str, Field(max_length=500)] | None = None
    flags: Annotated[str, Field(max_length=5)] = ""
    must_include: list[ShortStr] = Field(default_factory=list, max_length=30)
    must_not_include: list[ShortStr] = Field(default_factory=list, max_length=30)
    constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    should_refuse: bool | None = None
    pass_threshold: Annotated[float, Field(ge=0, le=1)] = 0.7
    must_cap: Annotated[float, Field(ge=0, le=1)] = 0.5

    @model_validator(mode="after")
    def _check(self) -> "Grader":
        t = self.type
        if t in ("exact", "choice") and not isinstance(self.answer, str):
            raise ValueError(f"{t} grader needs a string answer")
        if t == "numeric":
            try:
                float(self.answer)
            except (TypeError, ValueError):
                raise ValueError("numeric grader needs a numeric answer") from None
        if t == "regex":
            if not self.pattern:
                raise ValueError("regex grader needs a pattern")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from None
        if t == "contains" and not (self.must_include or self.must_not_include):
            raise ValueError("contains grader needs must_include or must_not_include")
        if t == "constraints" and not self.constraints:
            raise ValueError("constraints grader needs at least one constraint")
        if t == "refusal" and self.should_refuse is None:
            raise ValueError("refusal grader needs should_refuse true/false")
        return self


class CriterionIn(Strict):
    key: Annotated[str, Field(pattern=KEY_RE.pattern)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=500)]
    kind: Literal["must", "nice", "penalty"]
    weight: Annotated[float, Field(gt=0, le=100)] = 1.0
    check_type: Literal["keywords", "regex", "human", "judge"]
    keywords: list[ShortStr] = Field(default_factory=list, max_length=30)
    match: Literal["any", "all"] = "any"
    pattern: Annotated[str, Field(max_length=500)] | None = None
    failure_tag: Literal[
        "hallucination", "arithmetic_error", "wrong_reasoning",
        "ignored_instruction", "wrong_refusal", "unsafe",
    ] | None = None

    @model_validator(mode="after")
    def _check(self) -> "CriterionIn":
        if self.check_type == "keywords" and not self.keywords:
            raise ValueError("keywords criterion needs at least one keyword")
        if self.check_type == "regex":
            if not self.pattern:
                raise ValueError("regex criterion needs a pattern")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from None
        return self


class ItemIn(Strict):
    key: Annotated[str, Field(pattern=KEY_RE.pattern)]
    category: Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")]
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    prompt: PromptStr
    grader: Grader
    open_ended: bool = False
    notes: Annotated[str, Field(max_length=2_000)] = ""
    criteria: list[CriterionIn] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _rubric_needs_criteria(self) -> "ItemIn":
        if self.grader.type == "rubric" and not self.criteria:
            raise ValueError("rubric grader needs at least one hidden criterion")
        return self


class SuiteIn(Strict):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(max_length=2_000)] = ""
    items: list[ItemIn] = Field(default_factory=list, max_length=2_000)

    @model_validator(mode="after")
    def _unique_keys(self) -> "SuiteIn":
        keys = [i.key for i in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("item keys must be unique within a suite")
        return self


class SuiteImport(Strict):
    filename: Annotated[str, Field(min_length=1, max_length=120)]
    suite: SuiteIn

    @field_validator("filename")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9 _.()-]{1,120}\.json$", v) or ".." in v:
            raise ValueError("filename must be a plain .json name without paths")
        return v


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

class JudgeConfig(Strict):
    enabled: bool = False
    endpoint_id: int | None = None
    pairwise: bool = False


class RunIn(Strict):
    name: Annotated[str, Field(max_length=120)] = ""
    suite_version_id: int
    candidate_id: int
    baseline_ids: list[int] = Field(min_length=1, max_length=5)
    repetitions: Annotated[int, Field(ge=1, le=3)] = 1
    item_ids: list[int] | None = Field(default=None, max_length=2_000)
    budget_usd: Annotated[float, Field(ge=0, le=10_000)] = 1.0
    judge: JudgeConfig = Field(default_factory=JudgeConfig)

    @model_validator(mode="after")
    def _distinct(self) -> "RunIn":
        ids = [self.candidate_id, *self.baseline_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate and baselines must be different endpoints")
        if self.judge.enabled and self.judge.endpoint_id is None:
            raise ValueError("judge.endpoint_id is required when the judge is enabled")
        return self


class VoteIn(Strict):
    vote: Literal["left", "right", "tie", "both_bad"]
    reason_tags: list[Literal[
        "more_accurate", "followed_instructions", "clearer", "hallucinated", "unsafe",
    ]] = Field(default_factory=list, max_length=5)
    note: Annotated[str, Field(max_length=1_000)] = ""


class CriterionLabel(Strict):
    criterion_id: int
    met: bool


class CriteriaReviewIn(Strict):
    labels: list[CriterionLabel] = Field(min_length=1, max_length=30)


class TagsIn(Strict):
    tags: list[Literal[
        "hallucination", "arithmetic_error", "wrong_reasoning",
        "ignored_instruction", "wrong_refusal", "unsafe",
    ]] = Field(default_factory=list, max_length=6)


class GateIn(Strict):
    baseline_model_id: int | None = None
    max_category_drop: Annotated[float, Field(ge=0, le=100)] | None = 5.0
    min_overall: Annotated[float, Field(ge=0, le=100)] | None = None
    no_significant_regression: bool = True
    max_unsafe: Annotated[int, Field(ge=0)] | None = 0
