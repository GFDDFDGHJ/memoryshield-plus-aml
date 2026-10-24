"""Invented standalone literal-window controls; no DB, credentials or network."""
import unittest
try:
    from coding.code_windows import code_tokens, code_windows, render_code_windows
except ModuleNotFoundError:
    from code_windows import code_tokens, code_windows, render_code_windows


class CodeTokenTests(unittest.TestCase):
    def test_identifiers_case_qualified_names_paths_and_error_codes_are_not_split_only(self):
        tokens=code_tokens('Load_HTTP pkg.Client.open src/auth/login.py C:\\repo\\src\\main.ts E_CONN_RESET 0xC0000005 i')
        for token in ('Load_HTTP','load_http','pkg.Client.open','src/auth/login.py',
                      'C:\\repo\\src\\main.ts','C:/repo/src/main.ts','E_CONN_RESET','0xC0000005','i'):
            self.assertIn(token,tokens)

    def test_unicode_identifiers_and_literal_cjk_bigrams(self):
        tokens=code_tokens('解析配置 ОшибкаСети café_client')
        self.assertTrue({'解析配置','解析','配置','ОшибкаСети','ошибкасети','café_client'}<=tokens)


class CodeWindowTests(unittest.TestCase):
    def assertExact(self,source,windows,budget,max_windows=6):
        self.assertLessEqual(sum(len(w['text']) for w in windows),budget)
        self.assertLessEqual(len(windows),max_windows)
        at=0
        for window in windows:
            self.assertGreaterEqual(window['start'],at)
            self.assertGreater(window['end'],window['start'])
            self.assertEqual(window['text'],source[window['start']:window['end']])
            self.assertEqual(window['start_line'],source[:window['start']].count('\n')+1)
            self.assertEqual(window['end_line'],source[:window['end']-1].count('\n')+1)
            at=window['end']

    def test_complete_short_source_is_original_including_tabs_and_crlf(self):
        source='def Load_HTTP():\r\n\tvalue = "two  spaces"\r\n\treturn value\r\n'
        windows=code_windows(source,'Load_HTTP',1000)
        self.assertEqual([w['text'] for w in windows],[source])
        self.assertExact(source,windows,1000)

    def test_fitting_seven_line_function_keeps_critical_tail_for_header_only_match(self):
        source=('def parse_packet(raw):\n'
                '    payload = raw.strip()\n'
                '    if not payload:\n'
                '        raise ValueError("empty")\n'
                '    decoded = payload.split(":")\n'
                '    checksum = decoded[-1]\n'
                '    return verify_critical_tail(checksum)\n')
        windows=code_windows(source,'parse_packet',6000,context_lines=0)
        self.assertEqual(len(windows),1)
        self.assertEqual(windows[0]['text'],source)
        self.assertEqual(windows[0]['reason'],'complete-source')
        self.assertEqual(windows[0]['end_line'],7)
        self.assertExact(source,windows,6000)

    def test_long_python_preserves_indentation_and_attaches_enclosing_headers(self):
        source='# ordinary comment\n'*90+'class ArchiveReader:\n    def read_packet(self):\n'
        source+='        # ordinary local note\n'*18
        source+='        raise E_PACKET_MISMATCH("two  spaces")\n'
        source+='    def unrelated(self):\n        return "UNRELATED-METHOD-TAIL"\n'
        windows=code_windows(source,'Locate E_PACKET_MISMATCH',600,context_lines=0)
        self.assertExact(source,windows,600)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('class ArchiveReader:',literal)
        self.assertIn('    def read_packet(self):',literal)
        self.assertIn('        raise E_PACKET_MISMATCH("two  spaces")',literal)
        self.assertNotIn('UNRELATED-METHOD-TAIL',literal)

    def test_prior_closed_function_header_is_not_attached(self):
        source='// filler\n'*90+'function first() {\n  return 1;\n}\nconst E_OUTSIDE_CASE = 2;\n'
        windows=code_windows(source,'E_OUTSIDE_CASE',120,context_lines=0)
        self.assertNotIn('function first()', ''.join(w['text'] for w in windows))
        self.assertExact(source,windows,120)

    def test_javascript_brace_scope_and_quoted_braces(self):
        source='// filler\n'*80+'function readPacket() {\n  const punctuation = "}";\n'
        source+='  // a long ordinary note\n'*16+'  throw new PacketShapeError("bad");\n}\n'
        windows=code_windows(source,'PacketShapeError',350,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('function readPacket() {',literal)
        self.assertIn('  throw new PacketShapeError("bad");',literal)
        self.assertExact(source,windows,350)

    def test_real_fence_edges_are_retained_without_fabricated_syntax(self):
        source='File: src/packet.py\n```python\n'+'# filler\n'*100+'def parse_packet():\n    raise PacketTokenError\n```\n'
        windows=code_windows(source,'PacketTokenError',300,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('```python\n',literal)
        self.assertTrue(literal.endswith('```\n'))
        self.assertEqual(literal.count('```'),2)
        rendered=render_code_windows(source,windows)
        self.assertIn('[SOURCE GAP chars=',rendered)
        self.assertIn('lines=',rendered)
        self.assertExact(source,windows,300)

    def test_unterminated_fence_never_gets_a_generated_closing_fence(self):
        source='```python\n'+'# filler\n'*100+'raise UnclosedFenceError\n'
        windows=code_windows(source,'UnclosedFenceError',100,context_lines=0)
        self.assertNotIn('```', ''.join(w['text'] for w in windows))
        self.assertNotIn('```',render_code_windows(source,windows))

    def test_file_scope_boundary_blocks_earlier_function_header(self):
        source='# filler\n'*80+'# File: src/older.py\ndef previous():\n    return 1\n'
        source+='# File: src/newer.py\nMISSING_IMPORT_SENTINEL\n'
        windows=code_windows(source,'MISSING_IMPORT_SENTINEL',180,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('# File: src/newer.py',literal)
        self.assertNotIn('def previous()',literal)

    def test_excluded_secret_ranges_block_neighbours_headers_and_matching_secrets(self):
        source='def read_packet():\n    SECRET_KEY = "PRIVATE-SENTINEL"\n    return PacketTarget\n'
        start=source.index('    SECRET_KEY');end=source.index('    return')
        windows=code_windows(source,'PacketTarget PRIVATE-SENTINEL',1000,excluded_ranges=[(start,end)])
        self.assertExact(source,windows,1000)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('    return PacketTarget',literal)
        self.assertNotIn('PRIVATE-SENTINEL',literal)
        self.assertNotIn('def read_packet()',literal)
        self.assertTrue(all(not w['start']<end or not w['end']>start for w in windows))

    def test_hidden_gap_is_explicit_and_does_not_count_as_code(self):
        source='first line\nPRIVATE-LINE\nlast line\n'
        start,end=source.index('PRIVATE'),source.index('last')
        windows=code_windows(source,'first last',1000,excluded_ranges=[(start,end)],context_lines=0)
        rendered=render_code_windows(source,windows)
        self.assertNotIn('PRIVATE-LINE',rendered)
        self.assertIn(f'[SOURCE GAP chars={start}:{end}',rendered)
        self.assertEqual(sum(w['text'].count('\n') for w in windows),2)

    def test_long_single_line_returns_exact_query_centred_substring(self):
        source='prefix_'*500+'; E_TARGET_MINIFIED(); '+'suffix_'*500
        windows=code_windows(source,'E_TARGET_MINIFIED',120,context_lines=0)
        self.assertExact(source,windows,120)
        self.assertIn('E_TARGET_MINIFIED',windows[0]['text'])
        self.assertEqual((windows[0]['start_line'],windows[0]['end_line']),(1,1))
        self.assertIn('partial-line',windows[0]['reason'])

    def test_unknown_multilingual_prose_remains_literal_without_fact_extraction(self):
        source='Первый комментарий.\n注释保留原文。\n'+'其他普通说明。\n'*80
        windows=code_windows(source,'完全未知的术语',80)
        self.assertExact(source,windows,80)
        self.assertTrue(windows)
        self.assertTrue(all(w['reason'].startswith('fallback') or 'adjacent-literal-line' in w['reason'] for w in windows))

    def test_explicit_short_variable_match_is_not_discarded(self):
        source='# filler\n'*100+'i = 7\n'
        windows=code_windows(source,'Where is `i` assigned?',50,context_lines=0)
        self.assertIn('i = 7', ''.join(w['text'] for w in windows))

    def test_character_window_limits_and_source_order_hold(self):
        source=''.join(f'def target_{index}():\n    return value_{index}\n' for index in range(30))
        windows=code_windows(source,'target_29 target_1 target_15',180,max_windows=2,context_lines=0)
        self.assertExact(source,windows,180,max_windows=2)

    def test_invalid_ranges_and_fabricated_windows_are_rejected(self):
        for ranges in ([(-1,1)],[(0,500)],[(True,2)],[(2,1)]):
            with self.assertRaises(ValueError):code_windows('some code','code',excluded_ranges=ranges)
        with self.assertRaises(ValueError):render_code_windows('abc',[dict(start=0,end=2,text='xx')])
        with self.assertRaises(ValueError):render_code_windows('abcd',[dict(start=2,end=4,text='cd'),dict(start=0,end=2,text='ab')])
        self.assertEqual(code_windows('abc','abc',0),[])
        self.assertEqual(code_windows('abc','abc',excluded_ranges=[(0,3)]),[])


if __name__=='__main__':unittest.main()
