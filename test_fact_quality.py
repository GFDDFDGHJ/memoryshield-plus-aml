"""Synthetic evidence-quality regressions; SQLite memory only, no providers."""
from __future__ import annotations

import sqlite3
import unittest

import facts as F


class EvidenceQualityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        F.ensure_schema(self.conn)
        self.conn.execute(
            "CREATE TABLE items (rid INTEGER PRIMARY KEY, user_id TEXT, session_id TEXT,"
            " role TEXT, content TEXT, created_at REAL, seq INTEGER, status TEXT)"
        )
        self.seq = 0

    def tearDown(self):
        self.conn.close()

    def add(self, text, user="user", session="session", role="user", recorded=None):
        self.seq += 1
        stamp = recorded if recorded is not None else 1700000000 + self.seq
        row = self.conn.execute(
            "INSERT INTO items(user_id,session_id,role,content,created_at,seq,status)"
            " VALUES(?,?,?,?,?,?,'active')", (user, session, role, text, stamp, self.seq),
        )
        F.store_facts(self.conn, user, session, row.lastrowid, text, stamp, self.seq, role)
        return row.lastrowid

    def digest(self, session="session", user="user", max_lines=12):
        F.update_digest(self.conn, user, session, max_lines)
        return self.conn.execute(
            "SELECT content FROM digests WHERE user_id=? AND session_id=?", (user, session),
        ).fetchone()[0]

    def test_chinese_evidence_covers_early_middle_and_late_in_source_order(self):
        stages = ["启动访谈", "绘制原型", "客户评审", "整改权限", "暂停上线",
                  "完成修复", "重新联调", "通过验收", "归档结束"]
        for index, stage in enumerate(stages):
            self.add(stage + "，这是项目在这一阶段明确记录的原文。", recorded=1800000000 - index)
        digest = self.digest(max_lines=3)
        self.assertIn("启动访谈", digest)
        self.assertIn("暂停上线", digest)
        self.assertIn("归档结束", digest)
        self.assertLess(digest.index("启动访谈"), digest.index("暂停上线"))
        self.assertLess(digest.index("暂停上线"), digest.index("归档结束"))
        self.assertIn("3 of 9 source segments", digest)
        self.assertIn("source_rid=1 session=session seq=1 role=user recorded_at=", digest)
        self.assertIn("not inferred event dates", digest)

    def test_numeric_observations_keep_conditions_units_and_repetitions(self):
        observation = "When the pump was on, pressure was 37.5 kPa after 0.75 seconds."
        self.add(observation)
        self.add(observation)
        self.add("When the pump was off, pressure was 12.5 kPa after 0.75 seconds.")
        digest = self.digest()
        self.assertEqual(digest.count(observation), 2)
        self.assertIn("12.5 kPa after 0.75 seconds", digest)
        self.assertIn("source_rid=1", digest)
        self.assertIn("source_rid=2", digest)
        self.assertNotIn("numbers seen:", digest)

    def test_sampling_retains_three_temporal_anchors_for_even_budgets(self):
        for size in range(4, 25):
            for budget in range(3, size):
                chosen = F._evidence_indices(size, budget)
                self.assertEqual(len(chosen), budget)
                self.assertEqual(len(set(chosen)), budget)
                self.assertEqual(chosen, sorted(chosen))
                self.assertTrue({0, size // 2, size - 1}.issubset(chosen))

    def test_short_sessions_and_thirteenth_session_are_retrievable(self):
        for index in range(15):
            session = f"session-{index:02d}"
            self.add(f"Observation keyword{index}: calibrated sensor.", session=session)
            self.digest(session)
        found = F.get_digests(self.conn, "user", "keyword13", 1)
        self.assertEqual(len(found), 1)
        self.assertIn("keyword13", found[0])
        combined = F.get_digests(self.conn, "user", "summarize across all sessions", 2)
        self.assertLessEqual(len(combined), 2)
        for index in range(15):
            self.assertIn(f"session=session-{index:02d}", "\n".join(combined))

    def test_digest_matching_ignores_metadata_and_isolates_users(self):
        self.add("Only the orchard was inspected.")
        self.digest()
        self.add("SECRET OTHER USER", user="other")
        self.digest(user="other")
        self.assertEqual(F.get_digests(self.conn, "user", "recorded metadata", 2), [])
        self.assertNotIn("SECRET OTHER USER", " ".join(F.get_digests(self.conn, "user", "overall summary", 2)))

    def test_conditional_and_plain_rules_preserve_decimals_and_units(self):
        text = "If pressure exceeds 37.5 kPa, use a 0.75 second delay."
        rules = [fact.value for fact in F.extract_facts(text) if fact.kind == "rule"]
        self.assertEqual(rules, [text.rstrip(".")])
        plain = [fact.value for fact in F.extract_facts("Always use a 7.5% discount.") if fact.kind == "rule"]
        self.assertEqual(plain, ["Always use a 7.5% discount"])

    def test_explicit_chinese_user_rules_without_promoting_narration(self):
        directives = "以后回答都要使用中文。请使用国际单位制。不要透露私人地址。"
        values = [fact.value for fact in F.extract_facts(directives) if fact.kind == "rule"]
        self.assertEqual(len(values), 3)
        for text in ("我以后回答问题都要先想一下。", "我一直不太舒服，医生说必须休息。", "我不要再担心这些事了。"):
            self.assertEqual([fact for fact in F.extract_facts(text) if fact.kind == "rule"], [])
        self.add("以后回答都要使用中文。", role="assistant")
        self.assertEqual(F.relevant_rules(self.conn, "user", "中文回答", 3), [])

    def test_relevant_old_rule_keeps_condition_and_source(self):
        rule = "If chemical pressure exceeds 37.5 kPa, use a 0.75 second delay."
        source_rid = self.add(rule)
        for text in ("Always include a greeting.", "Always include a title.",
                     "Always use metric units.", "Always cite a source.",
                     "Always use short sentences.", "Always include a closing."):
            self.add(text)
        found = F.relevant_rules(self.conn, "user", "chemical pressure delay", 1)
        self.assertEqual(len(found), 1)
        self.assertIn(rule.rstrip("."), found[0]["text"])
        self.assertEqual(found[0]["source_rid"], source_rid)
        self.assertEqual(found[0]["role"], "user")
        self.assertEqual(found[0]["seq"], 1)
        self.assertEqual(F.relevant_rules(self.conn, "other", "chemical pressure", 3), [])

    def test_change_history_selects_related_chain_and_source_order(self):
        self.add("I live in London.", recorded=1800000000)
        self.add("I moved to Berlin.", recorded=1700000000)
        self.add("I moved to Madrid.", recorded=1600000000)
        self.add("I work at Orchard.")
        self.add("I work at Harbour.")
        history = F.change_history(self.conn, "user", "How did my city location change over time?", 6)
        self.assertEqual([row["value"] for row in history], ["London", "Berlin", "Madrid"])
        self.assertEqual([row["seq"] for row in history], [1, 2, 3])
        self.assertIn("[HISTORICAL]", history[0]["text"])
        self.assertIn("[CURRENT]", history[-1]["text"])
        self.assertNotIn("Harbour", " ".join(row["text"] for row in history))
        endpoints = F.change_history(self.conn, "user", "location timeline", 2)
        self.assertEqual([row["value"] for row in endpoints], ["London", "Madrid"])

    def test_old_truncated_rule_can_recover_unambiguous_original_evidence(self):
        rule = "If order total exceeds 100, use a 7.5% discount."
        self.add(rule)
        self.conn.execute("UPDATE facts SET value='If order total exceeds 100, use a 7'")
        found = F.relevant_rules(self.conn, "user", "discount", 1)
        self.assertEqual(len(found), 1)
        self.assertIn(rule, found[0]["text"])

    def test_current_query_does_not_return_history_or_unrelated_latest_change(self):
        self.add("I live in London.")
        self.add("I moved to Madrid.")
        self.add("I work at Orchard.")
        self.add("I work at Harbour.")
        current = F.change_history(self.conn, "user", "Where do I live now?", 6)
        self.assertEqual([row["value"] for row in current], ["Madrid"])
        self.assertEqual(F.change_history(self.conn, "user", "How do I prune a tree?", 6), [])
        self.assertEqual(F.change_history(self.conn, "other", "location history", 6), [])

    def test_explicit_budget_and_no_guessing_rule_replacement(self):
        self.add("Always answer in English.")
        self.add("From now on answer in Mandarin.")
        rules = F.relevant_rules(self.conn, "user", "answer rules", 2)
        self.assertEqual(len(rules), 2)
        self.assertTrue(all(rule["status"] == "active" for rule in rules))
        self.assertEqual(F.relevant_rules(self.conn, "user", "rules", 0), [])
        self.assertEqual(F.change_history(self.conn, "user", "history", 0), [])
        self.assertEqual(F.get_digests(self.conn, "user", "summary", 0), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
