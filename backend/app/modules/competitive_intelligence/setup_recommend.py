"""CI-P7 setup recommendation contracts.

Recommendations are unverified drafts: candidate products and source URLs with explicit
provenance, never persisted as approved configuration. Approval happens only through
the explicit create path with user-picked entries.
"""

from typing import Literal

from pydantic import Field

from app.modules.competitive_intelligence.models import Contract, SourceKind

RecommendationProvenance = Literal["model_knowledge", "user_supplied", "web_search", "site_crawl"]
MAX_RECOMMENDED_PRODUCTS = 10
MAX_RECOMMENDED_SOURCES = 40
MAX_DISCOVERY_DOMAINS = 10


class RecommendedSource(Contract):
    url: str = Field(max_length=2048)
    kind: SourceKind
    provenance: RecommendationProvenance
    note: str = Field(default="", max_length=500)


class RecommendedProduct(Contract):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["own", "competitor"] = "competitor"
    company: str = Field(default="", max_length=200)
    official_website: str | None = Field(None, max_length=2048)
    sources: list[RecommendedSource] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=500)
    provenance: RecommendationProvenance = "model_knowledge"
    # True when the user named this product, so the UI may pre-select it.
    suggested: bool = False


class SuggestedCriterion(Contract):
    field: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    importance: Literal["high", "medium", "low"] = "medium"


class SetupRecommendation(Contract):
    """Unverified draft; every entry still requires explicit user approval."""

    products: list[RecommendedProduct] = Field(default_factory=list, max_length=MAX_RECOMMENDED_PRODUCTS)
    suggested_goal: str = Field(default="", max_length=4000)
    suggested_dimensions: list[SourceKind] = Field(default_factory=list, max_length=4)
    suggested_criteria: list[SuggestedCriterion] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=16)


# What a setup request says about the user's own product. A model fills it; Scout keeps a competitor
# name only when the request contains it verbatim. No docstring: it would become the schema text the model reads.
# Every field is required: with optional fields, schema-constrained decoding let the model skip them.
class ProductBrief(Contract):
    own_product: str | None = Field(max_length=200, description=(
        "Name of the user's own product, exactly as written; null if not given."))
    named_competitors: list[str] = Field(max_length=10, description=(
        "Other products the user names in the request, exactly as written; empty if none are named."))
    category: str | None = Field(max_length=120, description=(
        "What kind of product the user's own product is: a short English noun phrase (2-6 words) "
        "that a buyer would type into a search engine; null if the request does not say."))


class RecommendRequest(Contract):
    prompt: str = Field(min_length=1, max_length=2000)


class DiscoverCompetitorsRequest(Contract):
    prompt: str = Field(min_length=1, max_length=2000)


class DiscoverSourcesRequest(Contract):
    domains: list[str] = Field(min_length=1, max_length=MAX_DISCOVERY_DOMAINS)
    dimension: SourceKind
