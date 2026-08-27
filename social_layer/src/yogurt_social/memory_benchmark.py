"""Offline, privacy-safe benchmark for the V5 Exact + FTS recall baseline.

This module is deliberately a test/diagnostic harness, not a production memory
import path.  It seeds an isolated SQLite database and executes real
``MemoryService.recall`` calls without contacting a network service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .memory_recall_gate import RecallCandidate
from .memory_service.core import MemoryService, MemoryServiceConfig


SCHEMA_VERSION = 1
DATASET_VERSION = "memory-recall-v1"
DEFAULT_USER_ID = "benchmark-owner"
_ALLOWED_TYPES = {"fact", "preference", "episode"}
_SENSITIVE_MARKERS = (
    "password", "api key", "private key", "session credential", "cookie",
    "身份证", "银行卡", "信用卡", "完整聊天记录", "prompt history",
)


@dataclass(frozen=True)
class SeedMemory:
    memory_id: str
    memory_type: str
    source_id: int
    active: bool


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    compact = value.casefold()
    if any(marker in compact for marker in _SENSITIVE_MARKERS):
        return True
    # Obvious secret-shaped values are unsafe even when their surrounding text
    # omits a marker.  This keeps fixtures from becoming a credential store.
    return "-----begin" in compact or "sk-" in compact or "ghp_" in compact


def _require_string(record: Dict[str, Any], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("seed %s must be a non-empty string" % name)
    # A benchmark query may safely *mention* a regulated topic to verify the
    # existing gate (for example, a visa or bank-card question).  Canonical
    # seed text must never carry those values.
    if name != "query" and _contains_sensitive(value):
        raise ValueError("seed %s contains sensitive material" % name)
    return value.strip()


def load_seed(path: Path) -> List[Dict[str, Any]]:
    """Load and validate the narrow benchmark-only JSONL seed format."""
    records: List[Dict[str, Any]] = []
    seen: set = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("invalid seed JSON at line %d: %s" % (number, error))
        if not isinstance(record, dict):
            raise ValueError("seed line %d must be an object" % number)
        memory_id = _require_string(record, "id")
        if memory_id in seen:
            raise ValueError("duplicate seed id: %s" % memory_id)
        seen.add(memory_id)
        memory_type = _require_string(record, "type")
        if memory_type not in _ALLOWED_TYPES:
            raise ValueError("unsupported seed type: %s" % memory_type)
        if not isinstance(record.get("active", True), bool):
            raise ValueError("seed active must be boolean: %s" % memory_id)
        aliases = record.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ValueError("seed aliases must be a string list: %s" % memory_id)
        if any(_contains_sensitive(item) for item in aliases):
            raise ValueError("seed aliases contain sensitive material: %s" % memory_id)
        if memory_type == "fact":
            _require_string(record, "key")
            _require_string(record, "value")
        elif memory_type == "preference":
            _require_string(record, "category")
            _require_string(record, "value")
            if record.get("polarity") not in (-1, 1):
                raise ValueError("preference polarity must be -1 or 1: %s" % memory_id)
        else:
            _require_string(record, "summary")
            importance = record.get("importance", 0.5)
            if not isinstance(importance, (float, int)) or not 0 <= float(importance) <= 1:
                raise ValueError("episode importance must be between 0 and 1: %s" % memory_id)
        records.append(record)
    if not records:
        raise ValueError("seed contains no records")
    return records


def load_cases(path: Path) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("invalid cases JSON: %s" % error)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("cases schema_version must be %d" % SCHEMA_VERSION)
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    seen: set = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        case_id = _require_string(case, "id")
        if case_id in seen:
            raise ValueError("duplicate case id: %s" % case_id)
        seen.add(case_id)
        _require_string(case, "query")
        if not isinstance(case.get("should_recall"), bool):
            raise ValueError("case should_recall must be boolean: %s" % case_id)
        for key in ("expected_memory_ids", "acceptable_memory_ids", "tags"):
            value = case.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("case %s must be a string list: %s" % (key, case_id))
        if case["should_recall"] and not case["expected_memory_ids"]:
            raise ValueError("recall case needs expected_memory_ids: %s" % case_id)
        if not case["should_recall"] and case["expected_memory_ids"]:
            raise ValueError("no-recall case cannot have expected ids: %s" % case_id)
        if case.get("surface") not in {"wechat", "native"}:
            raise ValueError("case surface must be wechat or native: %s" % case_id)
        _require_string(case, "category")
    return cases


def seed_service(service: MemoryService, records: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, int], str]:
    """Seed canonical rows into an isolated benchmark database.

    MemoryStore's production writes correctly reactivate upserts.  Inactive
    fixture rows therefore use a direct, local SQLite update after insertion;
    this stays isolated to benchmark setup and never becomes an import API.
    """
    mapping: Dict[Tuple[str, int], str] = {}
    for record in records:
        kind = str(record["type"])
        if kind == "fact":
            service.memory.add_fact(DEFAULT_USER_ID, record["key"], record["value"], float(record.get("confidence", 1.0)), "benchmark")
            table, key_column, value = "facts", "fact_key", record["key"]
        elif kind == "preference":
            service.memory.add_preference(DEFAULT_USER_ID, record["category"], record["value"], int(record["polarity"]), float(record.get("confidence", 1.0)), "benchmark")
            table, key_column, value = "preferences", "category", record["category"]
        else:
            source_id = service.memory.add_episode(DEFAULT_USER_ID, record["summary"], float(record.get("importance", 0.5)), source="benchmark")
            if not record.get("active", True):
                with sqlite3.connect(service.db_path) as connection:
                    connection.execute("UPDATE episodes SET active = 0 WHERE id = ?", (source_id,))
                    connection.commit()
            mapping[(kind, source_id)] = str(record["id"])
            continue
        with sqlite3.connect(service.db_path) as connection:
            row = connection.execute(
                "SELECT id FROM %s WHERE user_id = ? AND %s = ?" % (table, key_column),
                (DEFAULT_USER_ID, value),
            ).fetchone()
            if row is None:
                raise RuntimeError("benchmark seed was not persisted")
            source_id = int(row[0])
            if not record.get("active", True):
                connection.execute("UPDATE %s SET active = 0 WHERE id = ?" % table, (source_id,))
                connection.commit()
        mapping[(kind, source_id)] = str(record["id"])
    service.index.rebuild_user(canonical_user_id=DEFAULT_USER_ID, persona_id="yogurt")
    return mapping


def _expected_ids(case: Dict[str, Any]) -> set:
    return set(case.get("expected_memory_ids", [])) | set(case.get("acceptable_memory_ids", []))


def _ranked_ids(service: MemoryService, query: str, source_map: Dict[Tuple[str, int], str], surface: str, conversation_key: str) -> List[str]:
    state = service.states.get(DEFAULT_USER_ID, service.scoped_conversation_key(surface, conversation_key))
    continuity = [state.current_topic or "", *state.open_threads] if state is not None else []
    explicit = service.gate._explicit_request(query)
    ranked: List[RecallCandidate] = []
    for document in service.index.search(canonical_user_id=DEFAULT_USER_ID, message=query, limit=8):
        score, lexical, continuity_score, quality = service.gate._score(
            message=query, continuity_texts=continuity, candidate_text=document.search_text,
            quality=document.quality, explicit_request=explicit,
        )
        ranked.append(RecallCandidate(
            "episodic" if document.source_kind == "episode" else "stable",
            document.source_kind, document.source_id, document.memory_key, document.prompt_text,
            score, lexical, continuity_score, quality,
        ))
    ranked.sort(key=lambda item: (item.score, item.lexical_score, item.quality_score), reverse=True)
    return [source_map[(item.kind, item.source_id)] for item in ranked if (item.kind, item.source_id) in source_map]


def _failure_reason(case: Dict[str, Any], decision: str, returned_id: Optional[str], ranked: Sequence[str], reasons: Sequence[str]) -> str:
    expected = _expected_ids(case)
    if case["should_recall"]:
        if decision == "skip":
            return "unexpected_pre_gate"
        if decision == "allow" and returned_id in expected:
            return "no_failure"
        if expected and any(item in ranked[1:] for item in expected):
            return "wrong_rank"
        if expected and not any(item in ranked for item in expected):
            return "retrieval_miss"
        if decision != "allow":
            return "gate_false_negative"
        return "returned_unexpected_memory"
    if decision == "allow":
        if case.get("category") == "stale" or (returned_id and "old" in returned_id):
            return "stale_hit"
        return "gate_false_positive"
    if "low_information" in " ".join(reasons) and case.get("category") != "low_info":
        return "unexpected_pre_gate"
    return "no_failure"


def _percent(numerator: int, denominator: int) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0


def _metrics(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    positives = [item for item in results if item["should_recall"]]
    negatives = [item for item in results if not item["should_recall"]]
    top1 = sum(item["top1_correct"] for item in positives)
    at3 = sum(item["recall_at_3"] for item in positives)
    mrr = sum(item["reciprocal_rank"] for item in positives)
    false_recalls = sum(item["decision"] == "allow" for item in negatives)
    no_recall = [item for item in results if item["decision"] != "allow"]
    true_no_recall = sum(not item["should_recall"] for item in no_recall)
    correct_decisions = sum(item["decision_correct"] for item in results)
    return {
        "cases": len(results),
        "top1_accuracy": _percent(top1, len(positives)),
        "recall_at_3": _percent(at3, len(positives)),
        "mrr": round(mrr / len(positives), 4) if positives else 0.0,
        "false_recall_rate": _percent(false_recalls, len(negatives)),
        "no_recall_precision": _percent(true_no_recall, len(no_recall)),
        "recall_decision_rate": _percent(sum(item["decision"] == "allow" for item in results), len(results)),
        "decision_accuracy": _percent(correct_decisions, len(results)),
    }


def run_benchmark(seed_path: Path, cases_path: Path) -> Dict[str, Any]:
    records = load_seed(seed_path)
    cases = load_cases(cases_path)
    start = time.perf_counter()
    results: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="yogurt-memory-benchmark-") as directory:
        service = MemoryService(MemoryServiceConfig(Path(directory) / "memory.sqlite3"))
        source_map = seed_service(service, records)
        for case in cases:
            case_start = time.perf_counter()
            key = "benchmark-" + str(case["id"])
            response = service.recall(
                persona_id="yogurt", user_id=DEFAULT_USER_ID, surface=case["surface"],
                conversation_key=key, message=case["query"],
            )
            latency_ms = (time.perf_counter() - case_start) * 1000
            ranked = _ranked_ids(service, case["query"], source_map, case["surface"], key)
            returned_id: Optional[str] = None
            if response.get("memories"):
                memory = response["memories"][0]
                returned_id = source_map.get((str(memory["kind"]), int(memory["source_id"])))
            expected = _expected_ids(case)
            rank = next((number for number, item in enumerate(ranked, start=1) if item in expected), None)
            decision = str(response["decision"])
            decision_correct = (decision == "allow") if case["should_recall"] else (decision != "allow")
            reasons = response.get("reason", [])
            if not isinstance(reasons, list):
                reasons = [str(reasons)]
            result = {
                "id": case["id"], "query": case["query"], "category": case["category"],
                "tags": case.get("tags", []), "should_recall": case["should_recall"],
                "expected_memory_ids": sorted(expected), "returned_memory_id": returned_id,
                "ranked_memory_ids": ranked[:3], "decision": decision,
                "decision_correct": decision_correct,
                "top1_correct": bool(rank == 1), "recall_at_3": bool(rank is not None and rank <= 3),
                "reciprocal_rank": round(1.0 / rank, 4) if rank else 0.0,
                "reasons": reasons, "latency_ms": round(latency_ms, 3),
            }
            result["failure_type"] = _failure_reason(case, decision, returned_id, ranked, reasons)
            results.append(result)
    categories: Dict[str, Dict[str, Any]] = {}
    for category in sorted({item["category"] for item in results}):
        subset = [item for item in results if item["category"] == category]
        category_metrics = _metrics(subset)
        category_metrics.update({
            "correct": sum(item["decision_correct"] for item in subset),
            "false_recalls": sum(not item["should_recall"] and item["decision"] == "allow" for item in subset),
            "misses": sum(item["should_recall"] and item["decision"] != "allow" for item in subset),
        })
        categories[category] = category_metrics
    latencies = sorted(item["latency_ms"] for item in results)
    p95_index = max(0, int((len(latencies) - 1) * 0.95))
    failures = [item for item in results if item["failure_type"] != "no_failure"]
    safety = {
        "stale_inactive_safety": _percent(
            sum(item["decision"] != "allow" for item in results if item["category"] == "stale" and "old" in item["tags"]),
            sum(1 for item in results if item["category"] == "stale" and "old" in item["tags"]),
        ),
        "low_info_pre_gate": _percent(
            sum(item["decision"] == "skip" for item in results if item["category"] == "low_info"),
            sum(1 for item in results if item["category"] == "low_info"),
        ),
        "high_risk_suppression": _percent(
            sum(item["decision"] != "allow" for item in results if item["category"] == "high_risk"),
            sum(1 for item in results if item["category"] == "high_risk"),
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "dataset": {"seed_sha256": _sha256(seed_path), "cases_sha256": _sha256(cases_path), "seed_records": len(records)},
        "summary": _metrics(results),
        "categories": categories,
        "latency_ms": {"total": round((time.perf_counter() - start) * 1000, 3), "average": round(statistics.mean(latencies), 3), "p50": round(statistics.median(latencies), 3), "p95": latencies[p95_index]},
        "failures": failures,
        "failure_counts": dict(sorted(Counter(item["failure_type"] for item in failures).items())),
        "safety": safety,
        "results": results,
    }


def recommendation(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    categories = report["categories"]
    if summary["false_recall_rate"] > 0.05:
        return "NEEDS TUNING: prioritize gate/ranking review; do not add vector retrieval yet."
    paraphrase = categories.get("paraphrase", {}).get("recall_at_3", 0.0)
    cross_language = categories.get("cross_language", {}).get("recall_at_3", 0.0)
    exact = categories.get("exact", {}).get("recall_at_3", 0.0)
    if exact >= 0.8 and min(paraphrase, cross_language) < 0.65:
        return "RETRIEVAL GAP: vector may be worth an isolated candidate-generator experiment."
    return "PASS: collect more benchmark data before changing retrieval technology."


def markdown_report(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Memory V5 Recall Benchmark Report", "",
        "## Environment", "", "- Retrieval: V5-B Exact + SQLite FTS5 + V4.2 Recall Gate", "- Mode: offline isolated SQLite, shadow-only", "- Dataset: %s" % report["dataset_version"],
        "- Seed SHA256: `%s`" % report["dataset"]["seed_sha256"], "- Cases SHA256: `%s`" % report["dataset"]["cases_sha256"], "",
        "## Overall metrics", "", "| Cases | Top1 | Recall@3 | MRR | False Recall | No-Recall Precision | Decision Rate |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| {cases} | {top1_accuracy:.2%} | {recall_at_3:.2%} | {mrr:.2%} | {false_recall_rate:.2%} | {no_recall_precision:.2%} | {recall_decision_rate:.2%} |".format(**summary), "",
        "## Category breakdown", "", "| Category | Cases | Correct | False recalls | Misses | Top1 | Recall@3 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category, values in report["categories"].items():
        lines.append("| {0} | {cases} | {correct} | {false_recalls} | {misses} | {top1_accuracy:.2%} | {recall_at_3:.2%} |".format(category, **values))
    lines.extend(["", "## Latency", "", "- Total: {total:.3f} ms; average: {average:.3f} ms; P50: {p50:.3f} ms; P95: {p95:.3f} ms".format(**report["latency_ms"]), "", "## Representative failures", ""])
    if not report["failures"]:
        lines.append("None.")
    else:
        for item in report["failures"][:12]:
            lines.append("- `%s`: %s; expected=%s, returned=%s, reason=%s" % (item["id"], item["query"], ",".join(item["expected_memory_ids"]) or "none", item["returned_memory_id"] or "none", item["failure_type"]))
    lines.extend(["", "## Recommendation", "", recommendation(report), "", "The report contains benchmark query text and opaque memory IDs only; it exports no canonical memory text, messages, or conversation transcripts."])
    return "\n".join(lines) + "\n"


def _load_baseline(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid benchmark baseline")
    return payload


def has_regression(report: Dict[str, Any], baseline: Dict[str, Any]) -> bool:
    thresholds = baseline.get("thresholds", {})
    summary = report["summary"]
    if summary["false_recall_rate"] > float(thresholds.get("max_false_recall_rate", 1.0)):
        return True
    for name in ("stale_inactive_safety", "low_info_pre_gate", "high_risk_suppression"):
        required = float(thresholds.get("min_%s" % name, 0.0))
        if report["safety"].get(name, 1.0) < required:
            return True
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="Run the offline Yogurt Memory V5 recall benchmark.")
    parser.add_argument("--seed", type=Path, default=repo / "social_layer/tests/fixtures/memory_recall_seed.jsonl")
    parser.add_argument("--cases", type=Path, default=repo / "social_layer/tests/fixtures/memory_recall_cases.json")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--baseline", type=Path, default=repo / "social_layer/tests/baselines/memory_recall_v5b.json")
    args = parser.parse_args(argv)
    report = run_benchmark(args.seed, args.cases)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    if args.report_output:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "latency_ms": report["latency_ms"], "recommendation": recommendation(report)}, ensure_ascii=False, indent=2))
    if args.fail_on_regression:
        baseline = _load_baseline(args.baseline)
        return 2 if has_regression(report, baseline) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
