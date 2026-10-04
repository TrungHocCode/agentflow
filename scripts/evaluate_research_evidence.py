"""Opt-in local model evaluation on synthetic documents, NOT live-web or database E2E."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings
from app.execution.llm import get_llm
from app.execution.research_contracts import ResearchResult, SourceDocument
from app.execution.research_evidence import EvidenceProcessor
from app.execution.research_reduction import EvidenceReducer
from app.execution.tools.contracts import success_result


class FixtureStore:
    """In-memory test port; does not touch user conversations, runs or database state."""

    async def save_document(self, document: SourceDocument) -> None:
        pass

    async def save_bundle(self, run_id: str, task_id: str, bundle: ResearchResult) -> None:
        pass

    async def check_active(self, run_id: str) -> None:
        pass


def fixtures() -> list[tuple[str, list[dict], list[str]]]:
    def source(name: str, body: str) -> dict:
        return {"ok": True, "requested_url": f"https://fixture.invalid/{name}", "data": {"text": body}}

    alpha = "Model Alpha scored 62.2% on Benchmark X, setup A."
    beta = "Model Beta scored 70.0% on Benchmark X, setup A."
    changed = "Model Alpha scored 70.0% on Benchmark X, setup B."
    return [
        ("short_source", [source("alpha", alpha)], ["62.2"]),
        ("long_source", [source("long", ("Background methodology without numeric benchmark scores.\n" * 80)
                                     + alpha)], ["62.2"]),
        ("two_models", [source("alpha", alpha), source("beta", beta)], ["62.2", "70.0"]),
        ("conflicting_setups", [source("alpha", alpha), source("changed", changed)], ["62.2", "70.0"]),
        ("partial_fetch", [source("alpha", alpha), {"ok": False, "requested_url": "https://fixture.invalid/fail",
                                                    "status": "http_error"}], ["62.2"]),
    ]


async def evaluate(model: str, destination: Path) -> None:
    llm = get_llm(model_name_override=model, temperature=0)
    records = []
    with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
        for name, sources, expected in fixtures():
            started = perf_counter()
            processor = EvidenceProcessor(llm, FixtureStore(), name, "1")
            reducer = EvidenceReducer(llm, FixtureStore(), name, 2)
            error = None
            synthesis = None
            try:
                await processor.process(success_result({"sources": sources}),
                    "Extract the model benchmark scores and their exact evaluation setup from the supplied text.")
                synthesis = await reducer.reduce(processor.bundle.claims,
                    "Compare the model scores; preserve different setups and missing data without ranking incompatible scores.")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            observed = {claim.value_text for claim in processor.bundle.claims if claim.value_text}
            record = {"case": name, "model": model, "fixture_only": True,
                      "duration_ms": round((perf_counter() - started) * 1000, 3),
                      "numeric_values_preserved": set(expected).issubset(observed),
                      "expected_values": expected, "observed_values": sorted(observed), "error": error,
                      "research": processor.bundle.model_dump(mode="json"),
                      "synthesis": synthesis.model_dump() if synthesis else None,
                      "metrics": processor.metrics + reducer.metrics}
            records.append(record)
            print(json.dumps({key: record[key] for key in (
                "case", "duration_ms", "numeric_values_preserved", "observed_values", "error")}, ensure_ascii=False))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
                "scope": "synthetic evidence extraction/reduction; in-memory store; not full workflow E2E",
                "cases": records}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    destination = Path(args.output) if args.output else Path("workspace_data/evaluation") / (
        "research-evidence-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".json"
    )
    asyncio.run(evaluate(args.model, destination))


if __name__ == "__main__":
    main()
