from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Evidence(StrictModel):
    page: int = Field(ge=1, description="One-based PDF page, not the printed page label")
    row_quote: str = Field(min_length=5, max_length=2500)
    header_quote: str = Field(min_length=2, max_length=2500)
    category_quote: str = Field(
        default="",
        max_length=150,
        description="Literal column category label when category is not in the row",
    )
    ore_raw: str = Field(min_length=1, max_length=30)
    ore_unit: Literal["Mt", "kt", "t"]
    grade_raw: str = Field(min_length=1, max_length=30)
    contained_raw: str = Field(min_length=1, max_length=30)
    contained_raw_unit: Literal["oz", "koz", "Moz", "t", "kt", "Mt"]


class ResourceRow(StrictModel):
    project: str = Field(min_length=1, max_length=150)
    table: str = Field(min_length=1, max_length=150)
    effective_date: str = Field(
        max_length=40, description="ISO date when known; 'unknown' otherwise"
    )
    basis: str = Field(
        min_length=1, max_length=100, description="e.g. total, in-situ, stockpiles, pit"
    )
    cutoff: str = Field(max_length=100)
    category: Literal["Indicated", "Inferred"]
    commodity: Literal["Au", "Cu", "Li2O"]
    ore_mt: float = Field(ge=0)
    grade: float = Field(ge=0)
    grade_unit: Literal["g/t", "%"]
    contained_metal: float = Field(ge=0)
    contained_unit: Literal["oz", "t"]
    evidence: Evidence


class Candidate(StrictModel):
    reporting_standard: Literal["NI43-101", "JORC", "unknown"]
    rows: list[ResourceRow] = Field(max_length=200)
    unresolved: list[str] = Field(
        max_length=100, description="Blocking uncertainties in eligible reported fields only"
    )
    notes: list[str] = Field(
        default_factory=list, max_length=100, description="Nonblocking source/context observations"
    )


class Critique(StrictModel):
    score: int = Field(ge=1, le=10)
    approved: bool
    complete: bool
    issues: list[str] = Field(
        max_length=100, description="Blocking errors; approved=true requires this list to be empty"
    )
    notes: list[str] = Field(
        default_factory=list, max_length=100, description="Nonblocking observations, never approval"
    )


class Attempt(StrictModel):
    round: int = Field(ge=1, le=3)
    candidate: Candidate | None = None
    critique: Critique | None = None
    rule_issues: list[str] = Field(default_factory=list)
    error: str | None = None


class AuditResult(StrictModel):
    schema_version: str = "1.0"
    document_id: str
    filename: str
    status: Literal["accepted", "abstain"]
    requires_manual_review: bool
    selected_pages: list[int]
    selection_scope: Literal["automatic", "explicit"]
    reasons: list[str]
    rows: list[ResourceRow]
    attempts: list[Attempt]
    models: dict[str, str]
    parser: str = "mineru"
    parser_notes: list[str] = Field(default_factory=list)


class TruthRow(StrictModel):
    project: str = ""
    table: str = ""
    effective_date: str = ""
    basis: str = ""
    cutoff: str = ""
    category: Literal["Indicated", "Inferred"]
    commodity: Literal["Au", "Cu", "Li2O"]
    ore_mt: float = Field(ge=0)
    grade: float = Field(ge=0)
    grade_unit: Literal["g/t", "%"]
    contained_metal: float = Field(ge=0)
    contained_unit: Literal["oz", "t"]


class GroundTruth(StrictModel):
    schema_version: str = "1.0"
    document_id: str | None = None
    source: dict = Field(default_factory=dict)
    rows: list[TruthRow] = Field(min_length=1, max_length=200)
