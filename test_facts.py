"""Offline unit tests for the fact/digest layer (no network, no API key).

Run:  python test_facts.py

Covers the behaviours the leaderboard dimensions actually depend on:
  * rules are only mined from imperative / authoritative text (G2)
  * conditional instructions keep trigger and action (G2)
  * a later value retires the earlier one on the same triple (D1/D2)
  * session digests exist and are surfaced for aggregate queries (F1/G4)
  * per-user isolation still holds
"""
from __future__ import annotations

import hashlib
import gc
import os
import sqlite3
import sys
import tempfile

TEST_DIR = tempfile.TemporaryDirectory(prefix="aml_facts_test_")
os.environ["AML_DB_PATH"] = os.path.join(TEST_DIR.name, "facts.db")
os.environ["AML_AUTH_TOKEN"] = ""
os.environ["AML_GOVERNANCE"] = "false"
os.environ["EMBEDDING_API_KEY"] = "test"
os.environ["EMBEDDING_DIM"] = "64"

import facts as F  # noqa: E402
import store as store_module  # noqa: E402

DIM = 64


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

FAILED = []


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def kinds(text, role="user"):
    return F.extract_facts(text, relaxed=(role == "system"))


def semantic_regressions() -> None:
    """Verify rendered evidence and state transitions in a disposable database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    F.ensure_schema(conn)
    seq = 0

    def add(user, text, role="user"):
        nonlocal seq
        seq += 1
        F.store_facts(conn, user, "session", None, text, float(seq), seq, role)

    def current(user, predicate, subject="self"):
        return conn.execute(
            "SELECT value,previous_value FROM facts WHERE user_id=? AND subject=?"
            " AND predicate=? AND status='active' ORDER BY fid",
            (user, subject, predicate),
        ).fetchall()

    try:
        add("negative", "Never use Markdown. Do not include links.")
        profile = F.build_profile(conn, "negative")
        check("Never use Markdown" in profile and "Do not include links" in profile,
              "profile preserves never and do not prohibitions")
        matched = F.match_facts(conn, "negative", "Markdown", 5)
        check(any("Never use Markdown" in row["text"] for row in matched),
              "matched rule retains its prohibition")

        add("conditional", "If the user asks for a summary, keep it under 100 words.")
        add("conditional", "I am 31 years old.")
        profile = F.build_profile(conn, "conditional")
        check("If the user asks for a summary, keep it under 100 words" in profile,
              "profile preserves the complete conditional rule")
        add("nested", "If you discuss prices, remember to include the currency.", "system")
        rules = conn.execute("SELECT value FROM facts WHERE user_id='nested' AND kind='rule'").fetchall()
        check(len(rules) == 1 and rules[0]["value"].startswith("If you discuss prices,"),
              "an imperative inside a condition is not also stored unconditionally")
        add("negative-conditional", "If the user asks for a summary, never include personal names.")
        rule = current("negative-conditional", "when the user asks for a summary", "rule")
        check(len(rule) == 1 and "never include personal names" in rule[0]["value"],
              "conditional prohibition keeps both trigger and negation")
        add("conditional-bullet", "- If you discuss prices, never omit the currency.", "system")
        rules = conn.execute("SELECT value FROM facts WHERE user_id='conditional-bullet' AND kind='rule'").fetchall()
        check(len(rules) == 1 and rules[0]["value"].startswith("If you discuss prices,"),
              "a system bullet containing a condition creates one complete rule")
        add("hypothetical", "I live in Berlin.")
        add("hypothetical", "If I live in Paris, never use Markdown.")
        check([r["value"] for r in current("hypothetical", "location")] == ["Berlin"],
              "a self-description inside a condition does not overwrite actual state")
        add("hypothetical", "If I no longer live in Berlin, never suggest local events.")
        check([r["value"] for r in current("hypothetical", "location")] == ["Berlin"],
              "a hypothetical retraction does not revoke actual state")
        # Existing conditional rows stored the trigger only in their predicate.
        conn.execute("UPDATE facts SET value='keep it under 100 words' WHERE user_id='conditional' AND kind='rule'")
        check("When the user asks for a summary, keep it under 100 words" in F.build_profile(conn, "conditional"),
              "existing conditional rows retain their trigger when rendered")

        add("move", "I live in Beijing. Actually I just moved to Shanghai.")
        active = current("move", "location")
        check(len(active) == 1 and active[0]["value"] == "Shanghai"
              and active[0]["previous_value"] == "Beijing",
              "later explicit move wins within the same message")
        add("return", "I live in Paris. I moved to London. I live in Paris.")
        active = current("return", "location")
        check(len(active) == 1 and active[0]["value"] == "Paris"
              and active[0]["previous_value"] == "London",
              "a repeated value can become current again in the same message")
        add("overlap", "我住在New York。")
        active = current("overlap", "location")
        check(len(active) == 1 and active[0]["value"] == "New York"
              and active[0]["previous_value"] is None,
              "overlapping location patterns keep the complete highest-confidence value")
        add("overlap", "我住在New York。我搬到了Los Angeles。")
        active = current("overlap", "location")
        check(len(active) == 1 and active[0]["value"] == "Los Angeles"
              and active[0]["previous_value"] == "New York",
              "resolving overlapping patterns preserves later real updates")

        add("retract", "I live in Paris. I work at Acme.")
        add("retract", "I no longer work at Acme.")
        check(current("retract", "work") == []
              and [r["value"] for r in current("retract", "location")] == ["Paris"],
              "leaving an employer retracts work without changing location")
        add("retract", "I no longer live in Paris.")
        check(current("retract", "location") == [],
              "no longer living somewhere retracts that location")
        history = F.match_facts(conn, "retract", "Paris", 5)
        check(bool(history) and all("[HISTORICAL]" in r["text"]
              and "no active value recorded" in r["text"] for r in history),
              "a retracted location is clearly historical with no invented replacement")
        add("other-current", "I live in London.")
        add("other-current", "I no longer live in Paris.")
        check([r["value"] for r in current("other-current", "location")] == ["London"],
              "retracting an older value leaves an unrelated current value intact")
        add("same-message-retract", "I live in Paris. I no longer live in Paris.")
        check(current("same-message-retract", "location") == [],
              "a later retraction takes effect within the same message")
        add("comma-retract", "I live in Paris. I work at Acme.")
        add("comma-retract", "I no longer live in Paris, and I am retired.")
        check(current("comma-retract", "location") == [],
              "a comma ends the named location in a retraction")
        add("comma-retract", "I no longer work at Acme, and I enjoy retirement.")
        check(current("comma-retract", "work") == [],
              "a comma ends the named employer in a retraction")

        add("history", "I live in Paris.")
        add("history", "I moved to London.")
        add("separate-user", "I live in Madrid.")
        history = F.match_facts(conn, "history", "Paris", 5)
        check(len(history) == 1 and "[HISTORICAL]" in history[0]["text"]
              and "current value: London" in history[0]["text"]
              and "Madrid" not in history[0]["text"],
              "a query for an old value returns labeled history linked to that user's current value")

        add("favorite", "My favorite color is blue.")
        add("favorite", "My favorite color is red.")
        add("favorite", "My favorite food is pasta.")
        favorite = current("favorite", "color", "favorite")
        check(len(favorite) == 1 and favorite[0]["value"] == "red"
              and favorite[0]["previous_value"] == "blue"
              and [r["value"] for r in current("favorite", "food", "favorite")] == ["pasta"],
              "a new favorite replaces only the same favorite attribute")
        add("favorite", "I like tea. I like chess.")
        check({r["value"] for r in current("favorite", "like", "pref")} == {"tea", "chess"},
              "ordinary likes remain multi-valued")
    finally:
        conn.close()


def main() -> None:
    semantic_regressions()
    # --- narration must not become a rule
    got = [f for f in kinds("I have always been awed by birds.") if f.kind == "rule"]
    check(got == [], "narration is not a rule")

    # --- imperative is a rule
    got = [f for f in kinds("Always answer in JSON.") if f.kind == "rule"]
    check(bool(got), "imperative becomes a rule")

    # --- conditional instruction keeps trigger + action
    got = [f for f in kinds("If the user asks for a summary, keep it under 100 words.")
           if f.kind == "rule"]
    check(bool(got) and any("when" in f.predicate for f in got),
          "conditional rule keeps its trigger")

    # --- system turn: bullets are authoritative, no sentence-start gate
    prompt = "You are a planner.\n- Never suggest more than 3 options.\n- Reply in Chinese."
    got = [f for f in kinds(prompt, role="system") if f.kind == "rule"]
    check(len(got) >= 2, "system bullets become rules")

    # --- a later value retires the earlier one on the same triple
    st = store_module.MemoryStore()
    st.add_messages("u1", "s1", [
        {"role": "user", "content": "I live in Beijing and I work as a designer."},
        {"role": "user", "content": "Actually I moved to Shanghai last month."},
        {"role": "user", "content": "I prefer tea over coffee in the morning."},
    ])
    with st._connect() as conn:
        rows = conn.execute(
            "SELECT value,status,previous_value FROM facts"
            " WHERE user_id='u1' AND subject='self' AND predicate='location'"
            " ORDER BY fid").fetchall()
    values = [(r["value"], r["status"]) for r in rows]
    check(any(v.lower().startswith("shanghai") and s == "active" for v, s in values),
          "new value is active")
    check(any(s == "stale" for _, s in values), "old value retired on the same triple")

    # --- digest exists for the session and mentions the turn count
    with st._connect() as conn:
        d = conn.execute("SELECT content,n_msgs FROM digests WHERE user_id='u1'").fetchall()
    check(len(d) == 1 and d[0]["n_msgs"] == 3, "session digest built with turn count")

    # --- aggregate query surfaces the digest
    res = st.search("u1", "summarize everything across the session", 20)
    check(any(r["id"].startswith("digest") for r in res), "digest surfaced for summary query")

    # --- unrelated query does not drag every digest in
    res = st.search("u1", "what is the capital of France", 10)
    digest_count = sum(1 for r in res if r["id"].startswith("digest"))
    check(digest_count <= 1, "digest not crowded into unrelated queries")

    # --- isolation
    st.add_messages("u2", "s2", [{"role": "user", "content": "My dog is named Rex."}])
    res = st.search("u1", "dog name", 10)
    check(all("Rex" not in r["content"] for r in res), "no cross-user leakage")

    print()
    if FAILED:
        print(f"{len(FAILED)} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL FACT/DIGEST CHECKS PASSED")


if __name__ == "__main__":
    try:
        main()
    finally:
        # sqlite3 connection context managers commit but do not close; collect
        # their cycles before deleting the disposable file on Windows.
        gc.collect()
        TEST_DIR.cleanup()
