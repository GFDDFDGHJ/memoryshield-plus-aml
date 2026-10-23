"""Explicit personal-name field exclusions; invented sources only."""
import unittest
import query_plan as Q


class NameExclusionTests(unittest.TestCase):
    def test_explicit_name_exclusion_keeps_positive_task(self):
        for text in ('Summarize the Ferrite project, omitting personal names.',
                     'Summarize the Ferrite project without my name or nickname.',
                     '总结铁氧体项目，但是不要透露我的姓名和昵称。'):
            plan=Q.plan(text)
            self.assertIn('name',plan['excluded_categories'])
            self.assertIn('项目' if '项目' in text else 'project',plan['positive_query'])

    def test_ordinary_name_question_and_quoted_exclusion_remain_ordinary(self):
        for text in ('What is my name now?', 'Did I say "omit my name" before?',
                     'What does a variable name mean?', '我之前说过“不要透露我的姓名”吗？',
                     'What is my name, and did you omit my name earlier?',
                     'Do you usually omit my name in public summaries?',
                     'Did I ask you to omit my name before?', '你之前有没有省略我的姓名？'):
            self.assertEqual(Q.plan(text)['excluded_categories'],[])

    def test_assigned_personal_name_values_are_filtered_in_all_source_forms(self):
        for text in ('My name is Neris.', 'My registered name is Neris.',
                     'Her full name is Neris Salvo.', 'My nickname: Pip.',
                     '[FACT] self / name: Neris [SOURCE source_rid=23]',
                     '[CURRENT] self.name: Neris [SOURCE source_rid=23]',
                     '[PROFILE] facts: self.name=Neris [SOURCE source_rid=23]',
                     '姓名是林夏。', '我的昵称是小林。',
                     'My registered name Neris has been revoked.'):
            with self.subTest(text=text):self.assertTrue(Q.excluded_text(text,['name']))

    def test_schema_names_protective_rules_and_nonvalues_remain(self):
        for text in ('The column name is sample_id.', 'The file name is readings.csv.',
                     'The project name is Ferrite.', 'Dataset name: DS-FERRITE-7.',
                     "The file's full name is ferrite-run.csv.",
                     "The column's full name is sample_code.", "The project's registered name is Ferrite.",
                     'Never disclose my name.', 'My name is confidential.', 'My Name Is Confidential.',
                     'The name field is not required.', 'My name was not updated.',
                     '字段名字是sample_id。', '以后不要透露我的姓名。'):
            with self.subTest(text=text):self.assertFalse(Q.excluded_text(text,['name']))


class NameExclusionIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.setUp(self)

    def tearDown(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.tearDown(self)

    def test_summary_preserves_technical_decision_and_omits_labelled_name(self):
        self.memory.add_messages('target','invented-project',[
            dict(role='user',content='My name is Neris. Ferrite project selected a ceramic seal because it tolerates 340 C.',timestamp=1735689600000),
            dict(role='user',content='Ferrite project validation passed on 2025-08-12.',timestamp=1735689601000)])
        rows=self.memory.search('target','Summarize the Ferrite project without my name.',100)
        content='\n'.join(row['content'] for row in rows)
        self.assertIn('ceramic seal',content)
        self.assertIn('2025-08-12',content)
        self.assertNotIn('Neris',content)

    def test_possessive_technical_name_is_not_removed_with_personal_names(self):
        source="Ferrite data schema: The file's full name is ferrite-run.csv."
        self.memory.add_messages('target','invented-schema',[dict(role='user',content=source,timestamp=1735689600000)])
        rows=self.memory.search('target','Summarize the Ferrite data schema without personal names.',100)
        self.assertIn('ferrite-run.csv','\n'.join(row['content'] for row in rows))

    def test_past_omission_question_does_not_execute_a_new_exclusion(self):
        self.memory.add_messages('target','invented-name',[dict(role='user',content='My name is Neris.',timestamp=1735689600000)])
        rows=self.memory.search('target','What is my name, and did you omit my name earlier?',100)
        self.assertIn('Neris','\n'.join(row['content'] for row in rows))


if __name__=='__main__':unittest.main()
