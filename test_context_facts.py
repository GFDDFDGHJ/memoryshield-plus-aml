"""Source-scoped context regressions. In-memory invented data, no providers."""
import sqlite3
import re
import unittest

import facts as F


class ContextFactsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        F.ensure_schema(self.conn)
        self.conn.execute("CREATE TABLE items (rid INTEGER PRIMARY KEY,user_id TEXT,session_id TEXT,role TEXT,content TEXT,created_at REAL,seq INTEGER,status TEXT)")
        self.seq = 0

    def tearDown(self):
        self.conn.close()

    def add(self, text, session="s", role="user", user="u"):
        self.seq += 1
        cur = self.conn.execute("INSERT INTO items VALUES(NULL,?,?,?,?,?,?,'active')", (user,session,role,text,1700000000+self.seq,self.seq))
        F.store_facts(self.conn,user,session,cur.lastrowid,text,1700000000+self.seq,self.seq,role)
        return cur.lastrowid

    def context(self, query, budget=12000, limit=6, user="u"):
        return F.get_digests(self.conn,user,query,limit,budget_chars=budget)

    def test_topical_summary_excludes_unrelated_sessions(self):
        for idx in range(20):
            self.add("The orchard inspection confirmed ripe pears.",f"orchard{idx}")
        self.add("Turbine calibration required a replacement bearing.","turbine")
        result = "\n".join(self.context("Summarize the turbine calibration."))
        self.assertIn("Turbine calibration",result)
        self.assertNotIn("orchard",result)
        self.assertIn("topic-scoped",result)

    def test_explicit_global_summary_keeps_cross_session_sources(self):
        for idx in range(22):
            self.add(f"Decision code alphabet{idx} was retained.",f"s{idx}")
        result = "\n".join(self.context("Summarize across all sessions."))
        for idx in range(22):
            self.assertIn(f'session="s{idx}"',result)
        self.assertIn("included=22/22",result)

    def test_full_numeric_context_is_compact_and_update_sensitive(self):
        for idx in range(240):
            self.add(f"Trial {idx:03d}: pump pressure was {3000+idx}.125 kPa after 0.75 seconds at valve opening 25%.")
        first = self.context("Calculate average pump pressure across all trials.")
        joined = "\n".join(first)
        self.assertLessEqual(sum(map(len,first)),12000)
        self.assertIn("included=240/240",joined)
        self.assertIn("complete for selected source scope",joined)
        self.assertIn("kPa after 0.75 seconds at valve opening 25%",joined)
        for idx in range(240):
            self.assertIn(str(3000+idx)+".125",joined)
        # Decode the actual emitted compact representation, not merely the
        # template helper, and compare every observation to its original row.
        lines = joined.splitlines()
        template_at = next(index for index,line in enumerate(lines) if line.startswith("Literal source template: "))
        template = lines[template_at].removeprefix("Literal source template: ")
        columns = re.findall(r"\|\s*(n\d+)",lines[template_at+1])
        restored = {}
        for line in lines[template_at+2:]:
            cells = [cell.strip() for cell in line.split("|")]
            match = re.fullmatch(r"(\d+)@(\d+)/(\d+)",cells[0])
            if not match:
                continue
            original = template
            for column,value in zip(columns,cells[1:]):
                original = original.replace("{"+column+"}",value)
            restored[int(match.group(1))] = original
        expected = {row["rid"]:row["content"] for row in self.conn.execute("SELECT rid,content FROM items")}
        self.assertEqual(restored,expected)
        self.conn.execute("UPDATE items SET content=replace(content,'3007.125','9907.625') WHERE rid=8")
        second = "\n".join(self.context("Calculate average pump pressure across all trials."))
        self.assertNotEqual(joined,second)
        self.assertIn("9907.625",second)
        self.assertNotIn("3007.125",second)

    def test_repeated_numeric_observations_keep_each_source(self):
        for _ in range(30):
            self.add("Pump pressure was 12.5 kPa after 0.75 seconds.")
        result = "\n".join(self.context("Calculate mean pump pressure."))
        self.assertIn("included=30/30",result)
        for idx in range(1,31):
            self.assertIn(f"{idx}@{idx}/0",result)

    def test_literal_numeric_template_roundtrips_sign_precision_and_conditions(self):
        text = "At -3.50 C and load +2.00 kg, pressure was 1.25e-3 kPa; delay 0.75 s."
        template,values = F._numeric_template(text)
        for idx,value in enumerate(values,1):
            template = template.replace("{n"+str(idx)+"}",value)
        self.assertEqual(template,text)

    def test_literal_placeholder_names_cannot_collide_with_source_text(self):
        text = "Set the literal token {n1} and {n_1}; pressure is 12.5 kPa."
        template,values = F._numeric_template(text)
        slots = F._numeric_slots(text,len(values))
        self.assertNotIn("n1",slots)
        self.assertNotIn("n_1",slots)
        for slot,value in zip(slots,values):
            template = template.replace("{"+slot+"}",value)
        self.assertEqual(template,text)

    def test_numeric_context_does_not_invent_statistics(self):
        for idx in range(6):
            self.add(f"Pump input {idx}.5 kg produced {idx+7}.25 N at 12.5 C.")
        result = "\n".join(self.context("Predict pump force from the empirical observations."))
        self.assertIn("0.5 kg produced 7.25 N at 12.5 C",result)
        self.assertIn("5.5 kg produced 12.25 N at 12.5 C",result)
        self.assertNotIn("regression coefficient",result)

    def test_tight_budget_discloses_incomplete_observation_scope(self):
        for idx in range(80):
            self.add(f"Pump observation {idx}: " + chr(65+idx%20)*100 + " pressure depends on a separately recorded operating condition.")
        rows = self.context("Summarize pump observations.",budget=1800)
        self.assertTrue(rows)
        self.assertLessEqual(sum(map(len,rows)),1800)
        self.assertIn("truncated",rows[0])
        self.assertNotIn("coverage=complete",rows[0])

    def test_ordinary_digest_lookup_is_bounded_and_user_isolated(self):
        self.add("The optical nozzle has been calibrated.")
        self.add("Private optical nozzle token UNKNOWNSECRET.",user="other")
        rows = self.context("optical nozzle",budget=2000)
        self.assertLessEqual(sum(map(len,rows)),2000)
        self.assertNotIn("UNKNOWNSECRET","\n".join(rows))

    def test_summary_does_not_depend_on_cached_sample(self):
        for idx in range(30):
            self.add(f"Pressure observation {idx}: {idx+1}.5 kPa.")
        F.update_digest(self.conn,"u","s",max_lines=1)
        joined = "\n".join(self.context("Summarize pressure observations."))
        self.assertIn("included=30/30",joined)
        self.assertIn("8.5",joined)

    def test_global_execution_constraints_need_no_shared_topic(self):
        for text in ("Always use metric units.","Always include uncertainty estimates.","Never disclose private addresses."):
            self.add(text)
        rules = F.relevant_rules(self.conn,"u","Draft the pump incident report.",12)
        self.assertEqual(len(rules),3)
        self.assertEqual({row["scope"] for row in rules},{"global"})

    def test_local_conditional_rule_is_not_injected_into_unrelated_execution(self):
        self.add("If coolant pressure exceeds 37.5 kPa, use a 0.75 second delay.")
        self.assertEqual(F.relevant_rules(self.conn,"u","Draft a birthday invitation.",12),[])
        self.assertEqual(F.relevant_rules(self.conn,"u","What rules apply to birthday invitations?",12),[])
        self.assertEqual(len(F.relevant_rules(self.conn,"u","Explain the coolant pressure limit.",12)),1)
        self.assertEqual(len(F.relevant_rules(self.conn,"u","List all standing rules.",12)),1)

    def test_local_business_directive_does_not_become_global_from_always(self):
        self.add("Always use the violet folder for invoices.")
        self.assertEqual(F.relevant_rules(self.conn,"u","Draft a birthday invitation.",12),[])
        found = F.relevant_rules(self.conn,"u","What rule applies to invoices?",12)
        self.assertEqual(found[0]["scope"],"local")

    def test_topical_summary_retains_an_entity_bridge(self):
        self.add("Elena leads the Osprey integration program.","owner")
        self.add("Osprey runs on the Cairn platform.","bridge")
        self.add("Cairn is operated by the Coastal Operations division.","division")
        self.add("The orchard has ripe pears.","unrelated")
        context = "\n".join(self.context("Summarize the operating division of the platform used by the program Elena leads."))
        self.assertIn("Osprey runs on the Cairn platform",context)
        self.assertIn("Coastal Operations",context)
        self.assertNotIn("orchard",context)

    def test_more_than_four_simultaneous_constraints_respect_limit(self):
        for text in ("Always use metric units.","Always cite sources.","Always include uncertainty.","Always protect privacy.","Always mark assumptions.","Always retain dates.","Always use headings."):
            self.add(text)
        self.assertEqual(len(F.relevant_rules(self.conn,"u","Draft a pump report.",12)),7)
        self.assertEqual(len(F.relevant_rules(self.conn,"u","Draft a pump report.",3)),3)

    def test_rule_keeps_adjacent_exception_with_decimals(self):
        self.add("If pressure exceeds 37.5 kPa, use a 0.75 second delay. Exception: during calibration, use 1.25 seconds instead. The orchard is unrelated.")
        result = F.relevant_rules(self.conn,"u","What pressure rule applies?",4)
        self.assertIn("Exception: during calibration, use 1.25 seconds",result[0]["text"])
        self.assertNotIn("orchard",result[0]["text"])

    def test_first_person_state_never_cross_overwrites_speakers(self):
        self.add("I live in Marigold City.")
        self.add("I live in Juniper City.",role="assistant")
        active = self.conn.execute("SELECT value,source_role FROM facts WHERE subject='self' AND predicate='location' AND status='active'").fetchall()
        self.assertEqual({(row[0],row[1]) for row in active},{("Marigold City","user"),("Juniper City","assistant")})
        rows = F.match_facts(self.conn,"u","Where do I live in Marigold City?",10)
        self.assertTrue(rows)
        self.assertEqual({row["role"] for row in rows},{"user"})

    def test_same_role_change_and_history_keep_attribution(self):
        self.add("I live in Marigold City.")
        self.add("I moved to Rowan City.")
        self.add("I live in Juniper City.",role="assistant")
        history = F.change_history(self.conn,"u","How did my location change?",6)
        text = "\n".join(row["text"] for row in history)
        self.assertIn("Marigold City",text)
        self.assertIn("Rowan City",text)
        self.assertNotIn("Juniper City",text)
        self.assertTrue(all(row["role"]=="user" for row in history))

    def test_mentioning_an_assistant_does_not_change_the_first_person_owner(self):
        self.add("I work at Willow Engineering.")
        self.add("I work at Cedar Robotics.",role="assistant")
        rows = F.match_facts(self.conn,"u","What employer did I tell the assistant I work at?",10)
        self.assertTrue(rows)
        self.assertEqual({row["role"] for row in rows},{"user"})

    def test_assistant_care_background_remains_attributed(self):
        self.add("我有糖尿病。",role="assistant")
        self.add("我对花粉过敏。",role="assistant")
        profile = F.build_profile(self.conn,"u","请考虑健康背景提出关怀建议。")
        self.assertIn("糖尿病",profile)
        self.assertIn("assistant-stated",profile)
        self.assertIn("role=assistant",profile)

    def test_old_schema_migration_keeps_unknown_role(self):
        other = sqlite3.connect(":memory:")
        other.row_factory = sqlite3.Row
        # Freeze the pre-attribution table shape rather than creating the new
        # schema and merely changing the values of its role column.
        other.executescript("""CREATE TABLE facts (
            fid INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,source_rid INTEGER,subject TEXT NOT NULL,
            predicate TEXT NOT NULL,value TEXT NOT NULL,kind TEXT NOT NULL,
            confidence REAL NOT NULL,status TEXT NOT NULL DEFAULT 'active',
            previous_value TEXT,created_at REAL NOT NULL,seq INTEGER NOT NULL)""")
        other.execute("INSERT INTO facts(user_id,session_id,subject,predicate,value,kind,confidence,created_at,seq) VALUES('u','s','self','location','Legacy City','fact',.9,1,1)")
        F.ensure_schema(other)
        row = other.execute("SELECT * FROM facts").fetchone()
        self.assertEqual(row["source_role"],"unknown")
        result = F.match_facts(other,"u","Legacy City",1)
        self.assertEqual(result[0]["role"],"unknown")
        other.close()

    def test_old_unknown_role_is_repaired_only_from_its_retained_source(self):
        rid = self.add("I live in Rowan City.",role="assistant")
        self.conn.execute("UPDATE facts SET source_role='unknown' WHERE source_rid=?",(rid,))
        rows = F.match_facts(self.conn,"u","Where does the assistant live?",4)
        self.assertEqual(rows[0]["role"],"assistant")
        self.assertEqual(self.conn.execute("SELECT source_role FROM facts").fetchone()[0],"assistant")

    def test_legacy_user_state_is_retired_when_new_user_fact_arrives(self):
        self.add("I live in Willow City.")
        self.conn.execute("UPDATE facts SET source_role='unknown'")
        self.add("I moved to Aspen City.")
        rows = self.conn.execute("SELECT value,status,previous_value,source_role FROM facts ORDER BY seq").fetchall()
        self.assertEqual([(row[0],row[1]) for row in rows],[("Willow City","stale"),("Aspen City","active")])
        self.assertEqual(rows[1][2],"Willow City")
        self.assertEqual({row[3] for row in rows},{"user"})

    def legacy_cross_role_state(self, *, retract=False):
        self.add("I live in Willow City.")
        self.add("I live in Cedar City.",role="assistant")
        if retract:
            self.add("I no longer live in Willow City.")
        self.conn.execute("UPDATE facts SET source_role='unknown',status='stale' WHERE value='Willow City'")
        self.conn.execute("UPDATE facts SET source_role='unknown',status='active',previous_value='Willow City' WHERE value='Cedar City'")

    def test_read_repairs_cross_role_legacy_retirement_without_new_add(self):
        self.legacy_cross_role_state()
        rows = F.match_facts(self.conn,"u","Where do I currently live?",10)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["value"],"Willow City")
        self.assertEqual(rows[0]["status"],"active")
        self.assertNotIn("Cedar City",rows[0]["text"])

    def test_legacy_role_replay_does_not_reactivate_a_real_retraction(self):
        self.legacy_cross_role_state(retract=True)
        rows = F.match_facts(self.conn,"u","Where did I live in Willow City?",10)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["status"],"stale")
        self.assertIn("no active value recorded",rows[0]["text"])
        self.assertNotIn("Cedar City",rows[0]["text"])


if __name__ == "__main__":
    unittest.main()
