"""Deterministic source-span selection and measurement validation, without semantic trust claims."""

import re
from decimal import Decimal, InvalidOperation

from app.execution.research_contracts import ExtractedClaim


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
LITERAL = re.compile(r"</?[A-Za-z_][A-Za-z0-9_.:-]*>|`([^`\n]{1,120})`")


def supported_literals(text: str, sources: str) -> bool:
    """Preserve explicit tags/code identifiers; this is not semantic entailment."""
    for match in LITERAL.finditer(text):
        literal = match.group(1) if match.group(1) is not None else match.group()
        if literal not in sources:
            return False
    return True


def source_spans(text: str) -> dict[str, tuple[int, str]]:
    """Expose backend-owned paragraph slices so the model need not reproduce quotations."""
    spans: dict[str, tuple[int, str]] = {}
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text):
        paragraph = match.group().rstrip()
        for offset in range(0, len(paragraph), 800):
            excerpt = paragraph[offset:offset + 800]
            spans[f"s{len(spans)}"] = (match.start() + offset, excerpt)
    return spans


def canonical_number(value: str) -> Decimal | None:
    """Only equate unambiguous decimal/English thousands formats; do not round scores."""
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
        value = value.replace(",", "")
    elif not re.fullmatch(r"\d+(?:\.\d+)?", value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def supported_numbers(text: str, sources: str) -> bool:
    """Require every measurement to occur exactly or with equivalent thousands formatting."""
    known = set(NUMBER.findall(sources))
    for number in NUMBER.findall(text):
        if number in known:
            continue
        numeric = canonical_number(number)
        if numeric is None or not any(canonical_number(value) == numeric for value in known):
            return False
    return True


def validate_candidate(
    candidate: ExtractedClaim, piece: str, document: str,
    spans: dict[str, tuple[int, str]],
) -> tuple[ExtractedClaim | None, int, str | None]:
    """Return source-owned wording/offsets or a stable, content-free rejection reason."""
    if candidate.source_span_id:
        selected = spans.get(candidate.source_span_id)
        if selected is None:
            return None, -1, "unknown_source_span"
        offset, excerpt = selected
    else:
        excerpt = candidate.excerpt
        offset = piece.find(excerpt)
        if offset < 0:
            return None, -1, "excerpt_not_found"
    subject = candidate.subject.strip()
    if subject and (subject not in document or not re.search(r"[A-Za-z\u0080-\uffff]", subject)
                    or "%" in subject or len(subject.split()) > 8):
        return None, -1, "subject_not_found"
    # An entity verified elsewhere in the same document is context, not a measurement.
    prose = candidate.claim.replace(subject, "") if subject else candidate.claim
    if not supported_numbers(prose + " " + (candidate.evaluation_setup or ""), excerpt):
        return None, -1, "unsupported_measurement"
    value = candidate.value_text
    if value and value not in excerpt:
        numeric = canonical_number(value)
        matches = {number for number in NUMBER.findall(excerpt)
                   if numeric is not None and canonical_number(number) == numeric}
        if len(matches) != 1:
            return None, -1, "value_not_found"
        value = matches.pop()  # Publish the source's exact spelling, never a rounded/generated value.
    if candidate.unit and candidate.unit.casefold() not in excerpt.casefold():
        return None, -1, "unit_not_found"
    # Downstream receives the supported source statement, not unverified model paraphrase.
    return candidate.model_copy(update={"claim": excerpt, "excerpt": excerpt, "subject": subject,
                                        "value_text": value}), offset, None
