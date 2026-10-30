"""Small opt-in network check against invented data and a disposable database.

Uses the process OPENAI_API_KEY / DASHSCOPE_API_KEY; never prints credentials,
provider response bodies, or source messages. Does not contact deployed URLs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-small-network-test", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.allow_small_network_test:
        parser.error("This check makes a few paid provider calls; supply --allow-small-network-test")
    if not os.environ.get("OPENAI_API_KEY") or not (
        os.environ.get("EMBEDDING_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    ):
        parser.error("Configure process OPENAI_API_KEY and a DashScope embedding key")
    report = {"candidate_version": "v3.16.0", "invented_fixture_only": True,
              "production_db_used": False, "deployed_service_changed": False}
    with tempfile.TemporaryDirectory(prefix="aml-gpt-add-network-") as temp:
        os.environ.update(AML_DB_PATH=str(Path(temp) / "fixture.db"),
                          AML_GOVERNANCE="false", AML_RERANK="false",
                          AML_VERSION="v3.16.0", EMBEDDING_MODE="standard")
        from fastapi.testclient import TestClient
        import app
        app.AUDIT_LOG = str(Path(temp) / "audit.jsonl")
        payload = {"request_id": "invented-gpt-one", "user_id": "invented-user-one",
                   "session_id": "invented-session", "messages": [
                       {"role": "user", "timestamp": 1793376000000,
                        "content": "Mira chose the cobalt ceramic sample for fictional project Aster. Review is on 17 November 2026."},
                       {"role": "assistant", "timestamp": 1793376001000,
                        "content": "For project Aster, inspect the sample after cooling; if cracks appear, repeat the measurement before approval."}]}
        headers = {"Authorization": "Bearer " + app.CONFIG.auth_token} if app.CONFIG.auth_token else {}
        started = time.monotonic()
        try:
            with TestClient(app.app, raise_server_exceptions=False) as client:
                added = client.post("/add", json=payload, headers=headers)
                report["add_http_status"] = added.status_code
                if added.status_code != 200:
                    report["ok"] = False
                else:
                    report["id_echo_ok"] = all(added.json().get(k) == payload[k]
                                               for k in ("request_id", "user_id", "session_id"))
                    replay = client.post("/add", json=payload, headers=headers)
                    report["replay_same_receipt"] = replay.json() == added.json()
                    found = client.post("/search", json={"user_id": payload["user_id"],
                        "query": "What sample was chosen for project Aster?", "top_k": 10}, headers=headers)
                    other = client.post("/search", json={"user_id": "invented-user-two",
                        "query": "project Aster sample", "top_k": 10}, headers=headers)
                    report["search_ok"] = found.status_code == 200 and any(
                        "cobalt ceramic sample" in row["content"] for row in found.json()["data"])
                    report["isolation_ok"] = other.status_code == 200 and other.json()["data"] == []
                    with app.store._connect() as conn:
                        usage = conn.execute("SELECT model,prompt_tokens,completion_tokens,call_count FROM add_llm_usage").fetchall()
                        report["committed_usage"] = [dict(row) for row in usage]
                        report["selected_span_count"] = conn.execute("SELECT count(*) FROM llm_memory_spans").fetchone()[0]
                        report["raw_message_count"] = conn.execute("SELECT count(*) FROM items").fetchone()[0]
                    report["estimated_gpt_usd_at_standard_rates"] = round(sum(
                        (row["prompt_tokens"] * .15 + row["completion_tokens"] * .60) / 1e6 for row in usage), 8)
                    report["ok"] = all(report.get(k) for k in (
                        "id_echo_ok", "replay_same_receipt", "search_ok", "isolation_ok")) and len(usage) == 1 and report["selected_span_count"] > 0
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            app.store._client.close()
            if app.store._add_extractor._client is not None:
                app.store._add_extractor._client.close()
            app.store._pool.shutdown(wait=True)
    if args.report:
        args.report.write_bytes((json.dumps(report, indent=2) + "\n").encode())
    print(json.dumps(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
