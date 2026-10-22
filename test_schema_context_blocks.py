"""Generic cross-message data-schema scope; invented source records only."""
import sqlite3
import unittest

import facts as F


class SchemaContextBlockTests(unittest.TestCase):
    def setUp(self):
        self.rows=[]

    def add(self,text,*,session='lab',role='user'):
        seq=len(self.rows)+1
        row=dict(rid=seq,user_id='u',session_id=session,role=role,content=text,
                 seq=seq,created_at=1740787200+seq,timestamp_origin='message.timestamp')
        self.rows.append(row)
        return seq

    def selected(self,query='Infer the valve load-to-force relationship from all trials.'):
        return F.context_units_for_query(self.rows,query)

    def test_key_value_observations_inherit_declared_fields_across_sources(self):
        header=self.add('Valve experiments: inlet denotes applied load in kg; thrust denotes measured force in N. Every reading uses the dry configuration.')
        observations=[self.add(f'sample={index}; inlet={index+2}.50; thrust={index*3+7}.25') for index in range(47)]
        selected=self.selected()
        self.assertEqual({unit['source_rid'] for unit in selected},{header,*observations})
        text='\n'.join(unit['text'] for unit in selected)
        self.assertIn('applied load in kg',text)
        self.assertIn('measured force in N',text)
        self.assertIn('Every reading uses the dry configuration',text)
        self.assertIn('inlet=48.50; thrust=145.25',text)

    def test_positional_table_has_column_declaration_and_consecutive_data(self):
        header=self.add('Valve experiment columns:\nload kg | force N | wait ms')
        observations=[self.add(f'{index+1} | {index+3}.25 | {700+index}') for index in range(47)]
        rids=F.context_source_rids(self.rows,'Infer the valve force from every load observation.')
        self.assertEqual(rids,{header,*observations})

    def test_table_identifiers_and_categorical_conditions_remain_observations(self):
        header=self.add('Valve experiment columns: sample | load_kg | force_N | condition')
        observations=[self.add(f'V{index:03d} | {index+1}.50 | {index+3}.25 | '+('dry' if index%2 else 'wet')) for index in range(12)]
        self.assertEqual(F.context_source_rids(self.rows,'Infer valve load and force from the experiment.'),{header,*observations})

    def test_tab_delimited_source_columns_are_not_flattened_into_prose(self):
        header=self.add('Valve experiment columns:\nload_kg\tforce_N\twait_ms')
        observations=[self.add(f'{index+1}.5\t{index+4}.25\t{750+index}') for index in range(6)]
        units=self.selected()
        self.assertEqual({unit['source_rid'] for unit in units},{header,*observations})
        self.assertTrue(any('\t' in unit['text'] for unit in units))

    def test_short_condition_labels_on_numeric_field_rows_are_not_discarded(self):
        header=self.add('Valve measurements: inlet is load kg and thrust is force N.')
        observations=[self.add(f'inlet={index}; thrust={index+5}; '+('dry mode.' if index%2 else 'wet mode.')) for index in range(12)]
        selected=self.selected()
        self.assertEqual({unit['source_rid'] for unit in selected},{header,*observations})
        text='\n'.join(unit['text'] for unit in selected)
        self.assertIn('dry mode',text)
        self.assertIn('wet mode',text)

    def test_new_nondata_topic_ends_the_observation_block(self):
        self.add('Valve measurements: inlet is load kg and thrust is force N.')
        expected=[self.add(f'inlet={index}; thrust={index+5}') for index in range(4)]
        private=self.add('My private address is 19 Birch Lane and my cabinet code is 8172.')
        later=self.add('inlet=999; thrust=888')
        rids={unit['source_rid'] for unit in self.selected()}
        self.assertTrue(set(expected)<=rids)
        self.assertNotIn(private,rids)
        self.assertNotIn(later,rids)

    def test_speaker_change_stops_the_block(self):
        self.add('Valve measurements: inlet is load kg and thrust is force N.')
        expected=[self.add(f'inlet={index}; thrust={index+5}') for index in range(3)]
        other=self.add('inlet=555; thrust=777',role='assistant')
        later=self.add('inlet=999; thrust=888')
        rids={unit['source_rid'] for unit in self.selected()}
        self.assertTrue(set(expected)<=rids)
        self.assertNotIn(other,rids)
        self.assertNotIn(later,rids)

    def test_changed_field_schema_stops_the_block(self):
        self.add('Valve measurements: inlet is load kg and thrust is force N.')
        expected=[self.add(f'inlet={index}; thrust={index+5}') for index in range(3)]
        unrelated=self.add('street_number=19; cabinet_code=8172')
        later=self.add('inlet=999; thrust=888')
        rids={unit['source_rid'] for unit in self.selected()}
        self.assertTrue(set(expected)<=rids)
        self.assertNotIn(unrelated,rids)
        self.assertNotIn(later,rids)

    def test_an_unrelated_session_does_not_inherit_the_header(self):
        self.add('Valve measurements: inlet is load kg and thrust is force N.')
        other=[self.add(f'inlet={index}; thrust={index+5}',session='hobby') for index in range(6)]
        self.assertFalse(set(other)&F.context_source_rids(self.rows,'Infer valve load and force.'))

    def test_personal_numeric_narration_is_not_a_table_row(self):
        for text in ('I live at 19 Birch Lane, apartment 8.','My number is 553817 and my cabinet code is 88211.','The pressure was 12.5 kPa while I waited 750 ms.'):
            self.assertIsNone(F._numeric_record_shape(text))

    def test_columns_must_be_declared_not_just_numeric_rows_in_same_session(self):
        self.add('The valve was inspected and replaced after a load test.')
        other=[self.add(f'account={index}; cabinet={index+5}') for index in range(6)]
        self.assertFalse(set(other)&F.context_source_rids(self.rows,'Summarize valve load tests.'))

    def test_batch_and_incremental_append_have_the_same_final_selection(self):
        self.add('Valve measurements: inlet is load kg and thrust is force N.')
        for index in range(20):
            self.add(f'inlet={index}; thrust={index+5}')
            F.context_units_for_query(self.rows,'Infer valve load and force.')
        before=[(unit['source_rid'],unit['text']) for unit in self.selected()]
        after=[(unit['source_rid'],unit['text']) for unit in F.context_units_for_query(list(self.rows),'Infer valve load-to-force relationship from all trials.')]
        self.assertEqual(before,after)

    def test_selector_rejects_input_that_crosses_user_boundaries(self):
        self.add('Valve experiments: inlet is load kg; thrust is force N.')
        other=dict(self.rows[0],rid=99,user_id='other')
        with self.assertRaises(ValueError): F.context_units_for_query(self.rows+[other],'Summarize valve experiments.')

    def test_context_budget_packs_the_complete_data_block_without_inventing_fit(self):
        self.add('Valve experiments: inlet is load kg; thrust is force N.')
        for index in range(47): self.add(f'sample={index}; inlet={index+2}.50; thrust={index*3+7}.25')
        units=self.selected()
        output=F._pack_context(units,6,12000,'topic-scoped')
        self.assertLessEqual(sum(map(len,output)),12000)
        text='\n'.join(output)
        self.assertIn('included=48/48',text)
        self.assertIn('inlet=48.50; thrust=145.25',text)
        self.assertNotIn('coefficient',text)

    def test_legacy_everything_summary_phrase_remains_context_intent(self):
        scope,_=F.context_scope('Tell me everything we discussed.')
        self.assertEqual(scope,'global')

    def test_free_form_measurements_inherit_the_setup_without_matching_topic_words(self):
        header=self.add('Valve experiment preparation: equipment was warmed and measurements will follow in fixed order.')
        observations=[self.add(f'Sample {chr(65+index)}: applied mass was {index+2}.50 kg, waiting time was 750 ms, and temperature was 18.2 C.') for index in range(10)]
        closing=self.add('These measurements must be checked for the same operating conditions.')
        private=self.add('My private address is 19 Birch Lane and my cabinet code is 8172.')
        result=self.selected('Summarize the valve experiment measurements.')
        rids={unit['source_rid'] for unit in result}
        self.assertEqual(rids,{header,*observations,closing})
        self.assertNotIn(private,rids)
        self.assertIn('applied mass was 11.50 kg','\n'.join(unit['text'] for unit in result))

    def test_unit_measurements_adjacent_to_chinese_text_are_not_lost(self):
        header=self.add('阀门实验准备记录：设备已经预热，随后按固定顺序进行测量。')
        observations=[self.add(f'记录{chr(65+index)}：载荷为{index+2}.5kg、延迟750ms、温度18.2°C。') for index in range(10)]
        closing=self.add('实验收尾记录：请检查这些测量的条件是否相同。')
        private=self.add('我的私人地址为松林路19号，柜门密码为8172。')
        result=self.selected('总结阀门实验中的测量。')
        rids={unit['source_rid'] for unit in result}
        self.assertEqual(rids,{header,*observations,closing})
        self.assertNotIn(private,rids)
        self.assertIn('载荷为11.5kg、延迟750ms、温度18.2°C','\n'.join(unit['text'] for unit in result))

    def test_numeric_prose_with_address_numbers_does_not_become_measurement_run(self):
        self.add('Valve experiment preparation: measurements follow.')
        private=[self.add(f'Contact {index}: my address is {index+19} Birch Lane and cabinet code is {8172+index}.') for index in range(5)]
        self.assertFalse(set(private)&F.context_source_rids(self.rows,'Summarize valve experiment measurements.'))

    def test_withdrawal_of_previous_measurements_is_quoted_before_numbers(self):
        for index in range(120):
            self.add(f'Valve reading {index}: pressure={120+index/10:.1f} kPa; nozzle=standard. The operator confirmed this is a distinct observation.')
        invalid=self.add('The previous readings are invalid because the sensor had been disconnected; do not use them for any calculation.')
        units=self.selected('Calculate average valve pressure across all trials.')
        self.assertIn(invalid,{unit['source_rid'] for unit in units})
        output=F._pack_context(units,6,12000,'topic-scoped')
        text='\n'.join(output)
        self.assertIn('previous readings are invalid',text[:8000])
        self.assertIn('do not use them for any calculation',text[:8000])
        self.assertLess(text.find('previous readings are invalid'),text.find('Valve reading'))

    def test_new_topic_breaks_the_back_reference_scope(self):
        for index in range(4): self.add(f'Valve reading {index}: pressure={120+index}.5 kPa.')
        self.add('The unrelated garden plan is about planting seedlings.')
        invalid=self.add('The previous readings are invalid; do not use them for any calculation.')
        self.assertNotIn(invalid,F.context_source_rids(self.rows,'Calculate average valve pressure across all trials.'))

    def test_another_speaker_cannot_silently_withdraw_the_selected_block(self):
        for index in range(4): self.add(f'Valve reading {index}: pressure={120+index}.5 kPa.')
        invalid=self.add('The previous readings are invalid; do not use them for any calculation.',role='assistant')
        self.assertNotIn(invalid,F.context_source_rids(self.rows,'Calculate average valve pressure across all trials.'))


if __name__=='__main__': unittest.main()
