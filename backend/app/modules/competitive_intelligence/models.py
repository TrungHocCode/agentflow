"""Versioned CI contracts; proposals never authorize execution."""

import hashlib
import ipaddress
import json
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.shared.validation import enforce_json_size

SourceKind = Literal["pricing", "features", "integrations", "release_notes"]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]


def public_url(value: str) -> str:
    """Check syntax without fetching; collection revalidates DNS and redirects."""
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Use a public HTTP(S) URL without credentials or fragment.")
    if parsed.port not in {None, 80, 443} or host == "localhost" or "." not in host:
        raise ValueError("Only public standard-port websites are supported.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private network sources are not allowed.")
    return value


def digest(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InvestigationBudget(Contract):
    max_rounds: int = Field(3, ge=1, le=10)
    max_tasks: int = Field(20, ge=1, le=100)
    max_llm_calls: int = Field(64, ge=1, le=256)
    max_total_tokens: int = Field(262144, ge=1, le=1048576)
    max_duration_seconds: int = Field(3600, ge=1, le=86400)


class SourceContext(Contract):
    language: str | None = Field(None, max_length=32)
    region: str | None = Field(None, max_length=64)


class SourceConfig(Contract):
    id: UUID | None = None
    url: str = Field(max_length=2048)
    kind: SourceKind
    enabled: bool = True
    context: SourceContext = Field(default_factory=SourceContext)
    _url = field_validator("url")(public_url)


class ProfileFact(Contract):
    field: ShortText
    value: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    provenance: Literal["owner_asserted", "evidence_linked"] = "owner_asserted"

    @model_validator(mode="after")
    def require_evidence(self) -> "ProfileFact":
        if (self.provenance == "evidence_linked") != bool(self.evidence_ids):
            raise ValueError("Evidence provenance and references must agree.")
        return self


class ProfileDate(Contract):
    value: datetime
    provenance: Literal["owner_asserted", "evidence_linked"] = "owner_asserted"
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_date(self) -> "ProfileDate":
        if self.value.tzinfo is None:
            raise ValueError("Profile dates must include a timezone.")
        if (self.provenance == "evidence_linked") != bool(self.evidence_ids):
            raise ValueError("Date provenance and evidence references must agree.")
        return self


class ProductProfile(Contract):
    schema_version: Literal["1"] = "1"
    facts: list[ProfileFact] = Field(default_factory=list, max_length=100)
    target_segments: list[ShortText] = Field(default_factory=list, max_length=20)
    launch_at: ProfileDate | None = None
    published_at: ProfileDate | None = None
    effective_at: ProfileDate | None = None
    observed_at: ProfileDate | None = None
    fetched_at: ProfileDate | None = None


class ProductConfig(Contract):
    id: UUID | None = None
    name: ShortText
    description: str = Field("", max_length=1000)
    kind: Literal["own", "competitor"] = "competitor"
    company: str = Field("", max_length=200)
    official_website: str | None = Field(None, max_length=2048)
    profile_version_id: UUID | None = None
    profile: ProductProfile = Field(default_factory=ProductProfile)
    sources: list[SourceConfig] = Field(default_factory=list, max_length=20)

    @field_validator("official_website")
    @classmethod
    def website(cls, value: str | None) -> str | None:
        return public_url(value) if value else None


class ComparisonCriterion(Contract):
    field: ShortText
    objective: str = Field(min_length=1, max_length=1000)
    importance: Literal["high", "medium", "low"] = "medium"


class WatchlistConfig(Contract):
    schema_version: Literal["1"] = "1"
    goal: str = Field(min_length=1, max_length=4000)
    dimensions: list[SourceKind] = Field(min_length=1, max_length=4)
    products: list[ProductConfig] = Field(min_length=1, max_length=10)
    workflow_version_id: str | None = Field(None, min_length=1, max_length=128)
    comparison_criteria: list[ComparisonCriterion] = Field(default_factory=list, max_length=20)
    internal_strategy: str = Field("", max_length=4000)
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)

    @model_validator(mode="after")
    def consistent_membership(self) -> "WatchlistConfig":
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("Dimensions must be unique.")
        if sum(product.kind == "own" for product in self.products) > 1:
            raise ValueError("A watchlist may contain at most one own product.")
        identities = [str(p.id) for p in self.products if p.id]
        sources = [s for p in self.products for s in p.sources]
        source_ids = [str(s.id) for s in sources if s.id]
        if len(set(identities)) != len(identities) or len(set(source_ids)) != len(source_ids):
            raise ValueError("Product/source identities must be unique within a revision.")
        if len(sources) > 40 or any(s.kind not in self.dimensions for s in sources):
            raise ValueError("Use at most 40 sources within the selected dimensions.")
        enforce_json_size(self.model_dump(mode="json"), max_bytes=131072, field_name="config")
        return self


class CreateWatchlist(Contract):
    name: ShortText
    description: str = Field("", max_length=2000)
    config: WatchlistConfig


class UpdateWatchlist(CreateWatchlist):
    expected_revision_id: UUID


class Revision(Contract):
    id: UUID
    watchlist_id: UUID
    revision_number: int
    config: WatchlistConfig
    config_hash: str
    approval_status: Literal["unapproved", "approved"] = "unapproved"
    approved_at: datetime | None = None
    approved_by: str | None = None
    created_at: datetime


class Watchlist(Contract):
    id: UUID
    owner_id: str
    name: str
    description: str
    status: Literal["active", "archived"]
    current_revision: Revision
    created_at: datetime
    updated_at: datetime


class ApprovalRequest(Contract):
    workflow_version_id: str = Field(min_length=1, max_length=128)


class StartRunRequest(ApprovalRequest):
    revision_id: UUID


class SetupProposalRequest(Contract):
    conversation_id: UUID


class ProductView(ProductConfig):
    watchlist_id: UUID


class ProductProfileVersion(Contract):
    id: UUID
    product_id: UUID
    version_number: int
    profile: ProductProfile
    profile_hash: str
    created_at: datetime


class SourceView(SourceConfig):
    watchlist_id: UUID
    product_id: UUID
    config_version: str
    baseline_snapshot_id: UUID | None = None


class Pagination(Contract):
    limit: int
    offset: int
    total: int


Item = TypeVar("Item")


class Page(Contract, Generic[Item]):
    items: list[Item]
    pagination: Pagination
