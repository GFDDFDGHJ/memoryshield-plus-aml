"""Invented ranking copies; no requests, credentials, database or benchmark."""
import unittest

from ranking_copies import document_copy,prepare_ranking,query_copy

class RankingCopiesTests(unittest.TestCase):
    def test_long_document_retains_exact_critical_tail_instead_of_prefix_only(self):
        text='ordinary cabinet notes\n'*500+'def load_fern(path):\r\n\traise FernLookupError(path)\r\n'
        copy=document_copy(text,'Find load_fern and FernLookupError.',700)
        self.assertIn('def load_fern(path):\r\n',copy)
        self.assertIn('\traise FernLookupError(path)\r\n',copy)
        self.assertLessEqual(len(copy.encode('utf-8')),700)

    def test_long_query_retains_introduction_and_final_constraint(self):
        text='Implement Juniper adapter.\n'+'ordinary context\n'*700+'Never alter the public Juniper API.'
        copy=query_copy(text,500)
        self.assertIn('Implement Juniper adapter.',copy)
        self.assertIn('Never alter the public Juniper API.',copy)
        self.assertIn('RANKING COPY GAP',copy)
        self.assertLessEqual(len(copy.encode('utf-8')),500)

    def test_unicode_and_partial_line_bound_without_source_mutation(self):
        text='甲🌲'*2000+' ParseTulipError '+ '🌲乙'*2000
        copy=document_copy(text,'ParseTulipError',512)
        self.assertIn('ParseTulipError',copy)
        self.assertLessEqual(len(copy.encode('utf-8')),512)
        self.assertIn(copy,text)

    def test_budget_shared_over_full_sixty_four_document_pool(self):
        originals=['# ordinary diary\n'*300+f'def tulip_{index}():\n    return {index}\n' for index in range(64)]
        snapshot=originals[:]
        query,docs=prepare_ranking('Find tulip_63.',originals)
        self.assertEqual(len(docs),64)
        self.assertIn('def tulip_63()',docs[-1])
        self.assertLessEqual(len(query.encode())*len(docs)+sum(len(doc.encode()) for doc in docs),100000)
        self.assertEqual(originals,snapshot)

    def test_four_byte_prefix_and_casefold_expansion_preserve_actual_anchor(self):
        for token,query in [('ParseTulipError','parsetuliperror'),('StraßeError','strasseerror')]:
            text='🌲'*4000+' '+token+' '+'🌳'*4000
            for budget in (512,700,1550,3500):
                with self.subTest(token=token,budget=budget):
                    copy=document_copy(text,query,budget)
                    self.assertIn(token,copy)
                    self.assertIn(copy,text)
                    self.assertLessEqual(len(copy.encode('utf-8')),budget)

    def test_long_query_bounded_leading_pool_keeps_caller_tail_untouched(self):
        text='starting constraint\n'+'x'*5000+'\nfinal constraint'
        query,docs=prepare_ranking(text,['z'*5000]*64)
        self.assertGreaterEqual(len(docs),2)
        self.assertLess(len(docs),64)
        self.assertLessEqual(len(query.encode())*len(docs)+sum(len(doc.encode()) for doc in docs),100000)

    def test_short_inputs_remain_identical(self):
        self.assertEqual(query_copy('Short query'),'Short query')
        self.assertEqual(document_copy('def f():\n    return 5\n','f'),'def f():\n    return 5\n')

if __name__=='__main__':unittest.main()
