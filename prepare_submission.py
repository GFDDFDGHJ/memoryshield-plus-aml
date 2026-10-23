"""Package reviewed source files only; never include runtime data or secrets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

FILES = (
    ".gitignore", "README.md", "requirements.txt", "app.py", "config.py",
    "store.py", "facts.py", "governance.py", "smoke_test.py", "test_facts.py",
    "test_store_regressions.py", "test_api_contract.py", "start.ps1",
    "test_fact_quality.py", "test_store_quality.py", "evaluate_quality.py",
    "test_context_facts.py", "test_prefix_retrieval.py", "evaluate_context_quality.py",
    "test_temporal_context.py", "test_scope_consistency.py", "evaluate_capabilities.py",
    "test_schema_context_blocks.py",
    "test_capability_generalization.py", "test_context_planning.py",
    "evidence_links.py", "test_evidence_links.py", "test_context_continuation.py",
    "test_search_continuation.py", "evaluate_extended_context.py",
    "prepare_capability_vectors.py", "EVALUATION.md",
    "reranker.py", "query_plan.py", "test_reranker.py", "test_query_plan.py",
    "test_rerank_store.py", "evaluate_synthesis_intent.py", "evaluate_method_dependencies.py",
    "evaluate_rerank_replay.py",
    "source_dependencies.py", "test_source_dependencies.py", "test_ranking_windows.py",
    "test_execution_context.py", "test_fact_chronology.py",
    "state_evidence.py", "test_state_evidence.py",
    "context_data.py", "test_context_data.py", "test_data_link_boundaries.py",
    "evaluate_data_context.py",
    "test_rule_scope_domains.py", "test_seed_background.py",
    "test_synthesis_prefix.py", "test_name_exclusions.py",
    "test_fact_negations.py", "test_synthesis_scope.py",
    "test_assertion_context.py", "test_numbered_rule_blocks.py", "test_summary_coverage.py",
    "test_rule_execution_blocks.py", "test_state_qualification_scope.py",
    "evaluate_state_transitions.py", "evaluate_long_source_ranking.py",
    "evaluate_synthesis_progression.py", "evaluate_global_priority_controls.py",
    "watcher.ps1", "run-watcher.cmd",
    "prepare_submission.py",
)
ROOT = Path(__file__).resolve().parent


def main():
    payloads = {name: (ROOT / name).read_bytes() for name in FILES}
    manifest = {
        "version": "v3.14.0",
        "repository": "https://github.com/GFDDFDGHJ/memoryshield-plus-aml",
        "base_commit": "aed97db67b7dbd6d4d35678b4c0c609cf2050014",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    archive = ROOT / "submission-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted({**payloads, "submission-manifest.json": manifest_bytes}.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 23, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    (ROOT / "submission-manifest.json").write_bytes(manifest_bytes)
    print("archive", archive)
    print("files", len(payloads))
    print("archive_sha256", hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
