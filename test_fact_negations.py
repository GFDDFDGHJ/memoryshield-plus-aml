"""Negative predicate complements must not become positive stored values."""
import unittest
import facts as F
from test_store_regressions import _OfflineStoreTestCase


class NegativeComplementTests(unittest.TestCase):
    def test_unquoted_negation_is_not_an_asserted_replacement_value(self):
        for source in ('My name is not Eira.', 'My name is no longer Eira.',
                       "My dog's name is not Eira.", 'I work at neither Alder nor Birch.',
                       'I changed my name to not Eira.'):
            with self.subTest(source=source):
                facts=[f for f in F.extract_facts(source) if f.kind=='fact' and f.operation=='assert']
                self.assertFalse(any(f.value.casefold().startswith(('not ', 'no longer ', 'neither ')) for f in facts))

    def test_name_prefixes_and_explicit_quoted_literal_remain_assertions(self):
        for source,value in (('My name is Noel.', 'Noel'), ('My name is Nottingham.', 'Nottingham'),
                             ('My name is Nothing.', 'Nothing'), ('My name is "Not Eira".', 'Not Eira')):
            self.assertTrue(any(f.subject=='self' and f.predicate=='name' and f.value==value
                                for f in F.extract_facts(source)),source)

    def test_rules_and_literal_structured_values_keep_their_own_semantics(self):
        self.assertTrue(any(f.kind=='rule' for f in F.extract_facts('Do not disclose my name.')))
        source='Corpus: {"device", "status", "not ready"}'
        self.assertTrue(any(f.subject=='device' and f.predicate=='status' and f.value=='not ready'
                            for f in F.extract_facts(source)))


class NegativeComplementStorageTests(_OfflineStoreTestCase):
    def test_negative_name_complement_does_not_create_a_positive_current_name(self):
        self.memory.add_messages('owner','invented-name',[
            dict(role='user',content='My name is Eira.',timestamp=1735689600000),
            dict(role='user',content='My name is not Eira.',timestamp=1735689601000)])
        with self.memory._connect() as conn:
            values=[row[0] for row in conn.execute("SELECT value FROM facts WHERE user_id='owner' AND predicate='name'")]
        self.assertNotIn('not Eira',values)
        rows=self.memory.search('owner','What is my name now?',100)
        content='\n'.join(row['content'] for row in rows)
        self.assertIn('My name is not Eira.',content)
        self.assertNotIn('[CURRENT] self.name: not Eira',content)


if __name__=='__main__':unittest.main()
