"""Run retrieval regressions and GPT Add tests using invented data, no network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent


def main() -> int:
    # The pre-GPT retrieval tests expect an in-process fake embedding backend.
    # Give them a fake Add extractor too. The new tests exercise the real
    # extractor implementation against explicit fake OpenAI responses instead.
    modern = ["test_add_memory.py", "test_add_index.py", "test_gpt_add_integration.py",
              "test_selection_resilience.py", "test_add_failure_observability.py",
              "test_finish_diagnostics.py"]
    legacy = ["smoke_test.py", "test_facts.py"] + sorted(
        path.name for path in ROOT.glob("test_*.py")
        if path.name != "test_facts.py" and path.name not in modern
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="aml-offline-v317-") as temp:
        base = Path(temp)
        hooks = base / "offline_hooks"
        hooks.mkdir()
        (hooks / "sitecustomize.py").write_bytes(b'''from add_memory import ExtractionResult
import add_memory
class OfflineExtractor:
    def __init__(self, *args, **kwargs): pass
    def extract(self, messages):
        return ExtractionResult([], 1, 0, "gpt-4o-mini", 1)
add_memory.AddMemoryExtractor = OfflineExtractor
''')
        for index, script in enumerate(legacy + modern):
            env = dict(os.environ, OPENAI_API_KEY="offline-never-dispatch",
                       EMBEDDING_API_KEY="offline-never-dispatch",
                       AML_AUTH_TOKEN="offline-contract-token",
                       AML_VERSION="v3.17.0",
                       AML_ADD_SELECTION_FAILURE_POLICY="raw_on_unmatched",
                       AML_DB_PATH=str(base / f"test-{index}.db"),
                       AML_GOVERNANCE="false", AML_QUARANTINE="false",
                       AML_RERANK="false", EMBEDDING_MODE="standard",
                       PYTHONIOENCODING="utf-8")
            env["PYTHONPATH"] = str(ROOT)
            if script in legacy:
                env["PYTHONPATH"] = str(hooks) + os.pathsep + str(ROOT)
            run = subprocess.run([sys.executable, str(ROOT / script)],
                                 cwd=ROOT, env=env, capture_output=True,
                                 text=True, encoding="utf-8", timeout=90)
            row = {"script": script, "passed": run.returncode == 0,
                   "stdout": run.stdout, "stderr": run.stderr}
            results.append(row)
            print(json.dumps({"script": script, "passed": row["passed"]}), flush=True)
            if not row["passed"]:
                print((run.stdout + run.stderr)[-4000:], flush=True)
    report = {"candidate_version": "v3.17.0", "offline_only": True,
              "retrieval_regression_groups": len(legacy),
              "gpt_add_test_groups": len(modern),
              "all_passed": all(row["passed"] for row in results),
              "results": results}
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_bytes((json.dumps(report, indent=2) + "\n").encode())
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
