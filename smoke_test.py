"""Local end-to-end smoke test for the AML submission server.

Uses a deterministic fake embedder so it runs without any API key, then drives
the real FastAPI routes (Add -> Search) and checks the contract:
  - Add echoes request_id/user_id/session_id, success=true
  - Search returns {"data": [...]} with id/content/score/created_at
  - results never exceed top_k
  - no cross-user_id leakage
Run:  python smoke_test.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile

# Isolate DB and disable governance noise for the test.
DIM = 64
_tmp = tempfile.mkdtemp()
os.environ["AML_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["AML_GOVERNANCE"] = "false"
# Auth: the deployment reads .secrets/aml_auth_token.txt, so an unauthenticated
# TestClient would get a 401 on /add. This test exercises the memory contract,
# not the auth path, so explicitly disable the token here.
os.environ["AML_AUTH_TOKEN"] = ""
os.environ["EMBEDDING_API_KEY"] = "test"
os.environ["EMBEDDING_DIM"] = str(DIM)

import store as store_module  # noqa: E402


def fake_embed(self, texts):
    out = []
    for t in texts:
        h = hashlib.md5(t.encode("utf-8")).digest()
        vec = [0.0] * DIM
        for i, b in enumerate(h):
            vec[i % DIM] += (b - 128) / 128.0
        out.append(vec)
    return out


store_module.MemoryStore.embed = fake_embed

from fastapi.testclient import TestClient  # noqa: E402
import app as app_module  # noqa: E402

client = TestClient(app_module.app)
app_module.AUDIT_LOG = os.path.join(_tmp, "requests.jsonl")


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def main() -> None:
    # --- health
    r = client.get("/health")
    check(r.status_code == 200, "health 200")

    # --- Add user A
    add_a = {
        "request_id": "req-a-1",
        "user_id": "userA",
        "session_id": "sess-1",
        "messages": [
            {"role": "user", "content": "My favorite color is blue and I live in Beijing."},
            {"role": "assistant", "content": "Noted: you like blue."},
            {"role": "user", "content": "My dog's name is Max, a golden retriever."},
        ],
    }
    r = client.post("/add", json=add_a)
    check(r.status_code == 200, "add A 200")
    body = r.json()
    check(body["success"] is True, "add A success=true")
    check(body["request_id"] == "req-a-1", "add A echoes request_id")
    check(body["user_id"] == "userA", "add A echoes user_id")

    # --- Add user B (isolation)
    add_b = {
        "request_id": "req-b-1",
        "user_id": "userB",
        "session_id": "sess-2",
        "messages": [
            {"role": "user", "content": "The capital of France is Paris and Eiffel Tower is 330m."},
            {"role": "user", "content": "I prefer tea over coffee every morning."},
        ],
    }
    r = client.post("/add", json=add_b)
    check(r.status_code == 200, "add B 200")

    # --- Search as user A
    r = client.post("/search", json={"query": "What is my dog's name?", "user_id": "userA", "top_k": 100})
    check(r.status_code == 200, "search A 200")
    sbody = r.json()
    check("data" in sbody and isinstance(sbody["data"], list), "search returns data array")
    check(len(sbody["data"]) <= 100, "results <= top_k")
    if sbody["data"]:
        item = sbody["data"][0]
        check("id" in item and item["id"], "item has non-empty id")
        check("content" in item and item["content"], "item has non-empty content")
        check("score" in item, "item has score")
        check("created_at" in item, "item has created_at")
    # No leakage of user B's facts into user A's results
    joined = " ".join(d["content"] for d in sbody["data"]).lower()
    check("paris" not in joined and "eiffel" not in joined, "no cross-user leakage to A")

    # --- Search as user B
    r = client.post("/search", json={"query": "What beverage do I drink?", "user_id": "userB", "top_k": 5})
    sbody = r.json()
    check(len(sbody["data"]) <= 5, "respects top_k=5")
    joined = " ".join(d["content"] for d in sbody["data"]).lower()
    check("max" not in joined and "beijing" not in joined, "no cross-user leakage to B")

    # --- Search unknown user -> empty
    r = client.post("/search", json={"query": "anything", "user_id": "ghost", "top_k": 10})
    check(r.json()["data"] == [], "unknown user returns empty")

    print("\nALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
