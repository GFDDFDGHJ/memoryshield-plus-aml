"""Invented literal scheduling controls; no DB, models, or official fixtures."""
from collections import Counter
import re
import unittest

import facts as F


class SynthesisPrefixTests(unittest.TestCase):
    def source(self, rid, session, text, role='user'):
        return dict(rid=rid, source_rid=rid, user_id='synthetic', seq=rid+1,
                    session_id=session, role=role, content=text,
                    created_at=1750000000+rid*60,
                    timestamp_origin='message.timestamp')

    def units(self, rows):
        result=[]
        for row in rows:
            for segment,text in enumerate(F._source_sentences(row['content'])):
                result.append(dict(row,segment=segment,text=text))
        return result

    def packed(self, units, budget=12000, scope='global'):
        plan={}
        blocks=F._pack_context(units,6,budget,scope,selection_plan=plan)
        self.assertLessEqual(sum(map(len,blocks)),budget)
        return '\n\n'.join(blocks),plan

    def varied_history(self):
        # Every record has one unique sentence. Distinctive body evidence
        # should get a source turn even when other sources have unique titles.
        rows=[]
        background='The archive covers cabinets and ordinary paper storage.'
        for index in range(144):
            rows.append(self.source(index,'archive-'+str(index%12),
                                    'Entry reference '+str(index)+'. '+(background+' ')*24))
        claims=[]
        for domain in range(5):
            for step in range(4):
                claim=('The '+('orchard','harbour','studio','library','garden')[domain]
                       +' arrangement '+('was accepted by both teams','was later abandoned',
                                         'now uses a shared afternoon slot','saves the caretaker a late return')[step]+'.')
                pad=[background]*32
                pad.insert((5,13,21,29)[step],claim)
                rows.append(self.source(len(rows),'subject-'+str(domain),' '.join(pad)))
                claims.append(claim)
        return rows,claims

    def test_buried_sources_get_representatives_before_repeated_framing(self):
        rows,claims=self.varied_history()
        text,plan=self.packed(self.units(rows))
        self.assertTrue(all(claim in text for claim in claims))
        self.assertTrue(all(claim in text[:8000] for claim in claims))
        self.assertLessEqual(len(plan['complete_indices']),len(self.units(rows)))

    def test_source_turns_keep_short_facts_without_length_scoring(self):
        rows,_=self.varied_history()
        rows.append(self.source(len(rows),'short-state',
                                'The archive covers cabinets and ordinary paper storage. '*19+
                                'I quit. '+
                                'The archive covers cabinets and ordinary paper storage. '*19))
        text,_=self.packed(self.units(rows))
        self.assertIn('I quit.',text[:8000])

    def test_complete_view_fast_path_keeps_all_original_segments_and_order(self):
        padding='The ordinary appendices list the cabinets used for paper storage.'
        rows=[self.source(0,'one',(padding+' ')*56+'I quit.')]
        units=self.units(rows)
        text,plan=self.packed(units,10000)
        self.assertEqual(plan['complete_indices'],set(range(len(units))))
        self.assertLess(text.index(padding),text.index('I quit.'))
        self.assertEqual(text.count(padding),56)

    def test_repeated_real_observations_keep_every_original_pointer(self):
        rows=[self.source(index,'measurements','Recorded pressure was 18.250 kPa at 400 ms.')
              for index in range(241)]
        units=self.units(rows)
        text,plan=self.packed(units,50000)
        self.assertEqual(plan['complete_indices'],set(range(241)))
        pointers=re.findall(r'^([0-9]+)@([0-9]+)/0 \|',text,re.M)
        self.assertEqual(len(pointers),241)
        self.assertEqual(Counter(int(rid) for rid,seq in pointers),Counter(range(241)))

    def test_required_policy_schema_and_validity_still_precede_optional_sources(self):
        rows,_=self.varied_history()
        units=self.units(rows)
        protected=[('Always answer future questions in Catalan.','context_dependency'),
                   ('Columns are load_N and delay_ms.','context_schema'),
                   ('Earlier measurements were invalid and must not be fitted.','context_qualification')]
        for sentence,flag in protected:
            unit=dict(self.source(1000+len(units),'protected',sentence),segment=0,text=sentence)
            unit[flag]=True
            units.append(unit)
        text,_=self.packed(units)
        for sentence,flag in protected:
            self.assertIn(sentence,text[:2000])

    def test_first_person_filler_is_quoted_without_becoming_a_derived_conclusion(self):
        rows,_=self.varied_history()
        filler='I am noting another routine filing update.'
        rows.append(self.source(len(rows),'filing',(filler+' ')*38+'Nothing further was reported.'))
        units=self.units(rows)
        text,plan=self.packed(units)
        for line in text.splitlines():
            if re.match(r'^\d+@\d+/\d+ \|',line):
                self.assertTrue(any(line.endswith(unit['text']) for unit in units))
        self.assertNotIn('[FACT]',text)
        self.assertNotIn('current state:',text.lower())

    def test_source_attribution_survives_global_reordering(self):
        rows=[self.source(0,'alpha','I accepted the smaller room.','user'),
              self.source(1,'alpha','I prefer the larger room.','assistant')]
        text,plan=self.packed(self.units(rows))
        self.assertRegex(text,r'0@1/0 \| role="user" \| 0 \| I accepted the smaller room\.')
        self.assertRegex(text,r'1@2/0 \| role="assistant" \| 60 \| I prefer the larger room\.')
        self.assertEqual(plan['complete_indices'],{0,1})

    def test_unknown_topic_does_not_open_a_global_source_plan(self):
        rows=[self.source(0,'room','I accepted the smaller room.'),
              self.source(1,'private','My cabinet passcode is PRIVATE-69512.')]
        self.assertEqual(F.context_units_for_query(rows,'Summarize the Zorrel turbine project.'),[])

    def test_narrow_technical_source_selection_does_not_import_private_topics(self):
        rows=[self.source(0,'technical','The Osprey turbine now uses the ceramic seal.'),
              self.source(1,'private','My cabinet passcode is PRIVATE-69512.')]
        units=F.context_units_for_query(rows,'Summarize the Osprey turbine seal choice.')
        text,_=self.packed(units,scope='topic-scoped')
        self.assertIn('ceramic seal',text)
        self.assertNotIn('PRIVATE-69512',text)


if __name__=='__main__':unittest.main()
