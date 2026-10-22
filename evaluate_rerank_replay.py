"""Capture/replay semantic ranks while keeping a frozen synthetic evaluator.

The evaluator still blocks network, credentials and non-temporary databases.
Only complete query + ordered document hashes identify cached model orders.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--evaluator", type=Path, default=Path(__file__).with_name("evaluate_capabilities.py"))
    parser.add_argument("--rerank-cache", type=Path)
    parser.add_argument("--capture-rerank-inputs", type=Path)
    parser.add_argument("--replay-report", type=Path)
    options, arguments = parser.parse_known_args()
    cache = json.loads(options.rerank_cache.read_text(encoding="utf-8")) if options.rerank_cache else {}

    class Replay:
        def __init__(self):
            self.requests, self.hits, self.calls = {}, 0, 0

        def rerank(self, query, documents):
            request = {"query": query, "documents": list(documents)}
            key = hashlib.sha256(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            self.requests[key] = request
            self.calls += 1
            if key in cache:
                self.hits += 1
                return cache[key]
            return None

    replay = Replay()
    original_import = importlib.import_module

    def import_with_replay(name, package=None):
        module = original_import(name, package)
        if name == "store" and not getattr(module.MemoryStore, "_replay_installed", False):
            original_rank = module.MemoryStore._rerank_rows

            def rank(memory, query, ranked):
                previous = getattr(memory, "_reranker", None)
                memory._reranker = replay
                try:
                    return original_rank(memory, query, ranked)
                finally:
                    memory._reranker = previous

            module.MemoryStore._rerank_rows = rank
            module.MemoryStore._replay_installed = True
        return module

    previous_argv = sys.argv
    try:
        sys.argv = [str(options.evaluator)] + arguments
        with patch("importlib.import_module", import_with_replay):
            try:
                runpy.run_path(str(options.evaluator), run_name="__main__")
            except SystemExit as result:
                if result.code not in (None, 0):
                    raise
    finally:
        sys.argv = previous_argv
    if options.capture_rerank_inputs:
        options.capture_rerank_inputs.write_text(json.dumps({"requests": replay.requests}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"provider_requests_in_evaluation": 0, "calls": replay.calls, "cache_hits": replay.hits,
              "unique_requests": len(replay.requests), "missing_keys": sorted(set(replay.requests) - set(cache)),
              "wrapper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "cache_sha256": hashlib.sha256(options.rerank_cache.read_bytes()).hexdigest() if options.rerank_cache else None}
    if options.replay_report:
        options.replay_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"rerank_replay": report}))


if __name__ == "__main__":
    main()
