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

    def test_partial_line_casefold_and_windows_path_aliases_map_to_original_offsets(self):
        for query,target in (('parsetuliperror','ParseTulipError'),('strasseerror','StraßeError'),
                             ('c:/repo/src/main.ts','C:\\repo\\src\\MAIN.ts')):
            with self.subTest(query=query):
                source='prefix_'*500+'; '+target+'; '+'suffix_'*500
                windows=code_windows(source,query,120,context_lines=0)
                self.assertIn(target,''.join(w['text'] for w in windows))
                self.assertExact(source,windows,120)

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

    def test_long_python_complete_decorated_multiline_function_import_and_test(self):
        source=('from nonexistent_codec import unpack as wire_unpack\r\n'
                'from irrelevant_private import UNUSED_PRIVATE_ALIAS\r\n'+
                '# unrelated preamble\r\n'*200+
                '@trace_call\r\nasync def read_widget(\r\n'
                '    payload: bytes,\r\n    *, strict: bool = True,\r\n) -> dict:\r\n'
                '    widget = wire_unpack(payload)\r\n'
                '    if strict and not widget:\r\n        raise WidgetValidationError\r\n'
                '    widget["ready"] = True\r\n    return finish_widget(widget)\r\n'+
                '# unrelated separation\r\n'*30+
                'def test_widget_roundtrip():\r\n    result = read_widget(b"example")\r\n'
                '    assert result["ready"] is True\r\n'
                'def irrelevant_private():\r\n    return "UNRELATED-PRIVATE-BODY"\r\n')
        windows=code_windows(source,'WidgetValidationError',1600,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertExact(source,windows,1600)
        for value in ('@trace_call\r\nasync def read_widget(','from nonexistent_codec import unpack as wire_unpack\r\n',
                      'return finish_widget(widget)\r\n','assert result["ready"] is True\r\n'):
            self.assertIn(value,literal)
        self.assertNotIn('UNUSED_PRIVATE_ALIAS',literal)
        self.assertNotIn('UNRELATED-PRIVATE-BODY',literal)

    def test_complete_method_keeps_class_signature_without_other_methods(self):
        source='# filler\n'*300+'class WidgetParser:\n    @trace_call\n    def decode(self, raw):\n'
        source+='        pieces = raw.split(":")\n        if not pieces:\n            raise WidgetDecodeError\n'
        source+='        return complete_widget(pieces)\n    def unrelated(self):\n        return "PRIVATE-SIBLING-METHOD"\n'
        windows=code_windows(source,'WidgetDecodeError',900,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('class WidgetParser:\n',literal)
        self.assertIn('    @trace_call\n    def decode(self, raw):\n',literal)
        self.assertIn('return complete_widget(pieces)\n',literal)
        self.assertNotIn('PRIVATE-SIBLING-METHOD',literal)
        self.assertExact(source,windows,900)

    def test_python_triple_quoted_braces_and_fake_definitions_do_not_end_scope(self):
        source='# filler\n'*250+'def read_note():\n    note = """}\ndef fake():\n"""\n'
        source+='    value = clean_note(note)\n    return NoteTailSentinel(value)\n'
        windows=code_windows(source,'read_note',700,context_lines=0)
        self.assertIn('return NoteTailSentinel(value)\n',''.join(w['text'] for w in windows))
        self.assertExact(source,windows,700)

    def test_referenced_multiline_import_is_exact_and_unused_import_is_absent(self):
        source=('from widget_codec import (\n    decode_packet as decode_wire,\n    PacketShape,\n)\n'
                'from private_codec import SecretUnreferenced\n'+'# filler\n'*250+
                'def decode_record(payload: PacketShape):\n    return decode_wire(payload)\n')
        windows=code_windows(source,'decode_record',500,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('from widget_codec import (\n    decode_packet as decode_wire,\n    PacketShape,\n)\n',literal)
        self.assertNotIn('SecretUnreferenced',literal)
        self.assertExact(source,windows,500)

    def test_related_callers_are_same_file_direct_and_not_shadowed(self):
        source=('# filler\n'*250+'def decode_record(raw):\n    if not raw:\n        raise WidgetInputError\n'
                '    return finish_record(raw)\n'+
                '# separate\n'*25+
                'def test_good_call():\n    value = decode_record(b"ok")\n    assert value.ready\n'
                'def test_shadowed_call(decode_record):\n    decode_record()\n    return "SHADOW-PRIVATE-TAIL"\n'
                'def unrelated_attribute_call(other):\n    other.decode_record()\n    return "ATTRIBUTE-PRIVATE-TAIL"\n')
        windows=code_windows(source,'WidgetInputError',1300,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('assert value.ready\n',literal)
        self.assertNotIn('SHADOW-PRIVATE-TAIL',literal)
        self.assertNotIn('ATTRIBUTE-PRIVATE-TAIL',literal)
        self.assertExact(source,windows,1300)

    def test_same_named_function_in_another_explicit_file_is_not_a_dependency(self):
        source=('# File: src/actual.py\n'+'# filler\n'*250+
                'def decode_record(raw):\n    raise ActualRecordError\n    return raw\n'
                '# File: tests/another_module.py\n'
                'def test_other_module():\n    decode_record(b"other")\n    return "CROSS-FILE-PRIVATE-TAIL"\n')
        windows=code_windows(source,'ActualRecordError',900,context_lines=0)
        self.assertIn('# File: src/actual.py\n',''.join(w['text'] for w in windows))
        self.assertNotIn('CROSS-FILE-PRIVATE-TAIL',''.join(w['text'] for w in windows))
        self.assertExact(source,windows,900)

    def test_other_markdown_code_fence_is_not_a_dependency(self):
        source=('```python\n'+'# filler\n'*250+
                'def decode_record(raw):\n    raise ActualRecordError\n    return raw\n```\n'
                '```python\ndef test_other_file():\n    decode_record(b"other")\n'
                '    return "OTHER-FENCE-PRIVATE-TAIL"\n```\n')
        windows=code_windows(source,'ActualRecordError',900,context_lines=0)
        self.assertNotIn('OTHER-FENCE-PRIVATE-TAIL',''.join(w['text'] for w in windows))
        self.assertExact(source,windows,900)

    def test_excluded_middle_of_body_blocks_complete_function_and_dependencies(self):
        source=('from widget_codec import decode_wire\n'+'# filler\n'*200+
                'def decode_record(raw):\n    hidden = "WITHHELD-PRIVATE-LINE"\n'
                '    value = decode_wire(raw)\n    return VisibleTargetError(value)\n')
        left=source.index('    hidden');right=source.index('    value')
        windows=code_windows(source,'VisibleTargetError',1200,context_lines=0,excluded_ranges=[(left,right)])
        literal=''.join(w['text'] for w in windows)
        self.assertNotIn('WITHHELD-PRIVATE-LINE',literal)
        self.assertNotIn('def decode_record',literal)
        self.assertNotIn('from widget_codec',literal)
        self.assertTrue(all('complete-static-function' not in w['reason'] for w in windows))
        self.assertIn('VisibleTargetError',literal)
        self.assertExact(source,windows,1200)

    def test_excluded_gap_between_import_and_function_is_not_a_bridge(self):
        source=('from widget_codec import decode_wire\n'+'# filler\n'*200+
                'PRIVATE-BRIDGE-LINE\n'+'# filler\n'*20+
                'def decode_record(raw):\n    return decode_wire(raw)\n')
        left=source.index('PRIVATE');right=source.index('\n',left)+1
        windows=code_windows(source,'decode_record',700,context_lines=1,excluded_ranges=[(left,right)])
        literal=''.join(w['text'] for w in windows)
        self.assertIn('return decode_wire(raw)\n',literal)
        self.assertNotIn('from widget_codec',literal)
        self.assertNotIn('PRIVATE-BRIDGE-LINE',literal)
        self.assertExact(source,windows,700)

    def test_withheld_final_statement_cannot_make_prefix_a_complete_function(self):
        source='# filler\n'*200+'def decode_record(raw):\n    value = ordinary_step(raw)\n'
        source+='    return "WITHHELD-CRITICAL-TAIL"\n'
        left=source.index('    return');right=len(source)
        windows=code_windows(source,'decode_record',500,context_lines=1,excluded_ranges=[(left,right)])
        literal=''.join(w['text'] for w in windows)
        self.assertNotIn('WITHHELD-CRITICAL-TAIL',literal)
        self.assertTrue(all('complete-static-function' not in w['reason'] for w in windows))
        self.assertIn('[SOURCE GAP chars=',render_code_windows(source,windows))
        self.assertExact(source,windows,500)

    def test_oversized_function_keeps_match_and_signature_with_explicit_gaps(self):
        source='# filler\n'*200+'def decode_record(\n    raw: bytes,\n) -> dict:\n'
        source+='    value = ordinary_step(raw)\n'*100+'    return TargetTailError(value)\n'
        windows=code_windows(source,'TargetTailError',180,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        self.assertIn('def decode_record(\n    raw: bytes,\n) -> dict:\n',literal)
        self.assertIn('return TargetTailError(value)\n',literal)
        self.assertTrue(all('complete-static-function' not in w['reason'] for w in windows))
        self.assertIn('[SOURCE GAP chars=',render_code_windows(source,windows))
        self.assertExact(source,windows,180)

    def test_typescript_complete_function_import_call_body_and_actual_fences(self):
        source=('File: src/widget.ts\r\n```typescript\r\n'
                'import { decodeWire as unpackWire } from "./wire";\r\n'+
                '// filler\r\n'*200+
                'export async function readWidget(\r\n  payload: Uint8Array,\r\n): Promise<Widget> {\r\n'
                '  const punctuation = "} is quoted";\r\n  /* { a comment } */\r\n'
                '  const widget = unpackWire(payload);\r\n  if (!widget) {\r\n    throw new WidgetReadError();\r\n  }\r\n'
                '  return finishWidget(widget);\r\n}\r\n'+
                '// separate\r\n'*25+
                'function testWidget() {\r\n  const widget = readWidget(example);\r\n  assertWidgetReady(widget);\r\n}\r\n```\r\n')
        windows=code_windows(source,'WidgetReadError',1700,context_lines=0)
        literal=''.join(w['text'] for w in windows)
        for value in ('import { decodeWire as unpackWire } from "./wire";\r\n',
                      'export async function readWidget(\r\n','return finishWidget(widget);\r\n}\r\n',
                      'assertWidgetReady(widget);\r\n','```typescript\r\n','```\r\n'):
            self.assertIn(value,literal)
        self.assertExact(source,windows,1700)

    def test_javascript_arrow_and_class_method_keep_only_their_complete_body(self):
        cases=(('const readWidget = async (raw) => {\n  const value = clean(raw);\n  return ArrowTail(value);\n};\n',
                'readWidget','return ArrowTail(value);\n};\n'),
               ('class WidgetReader {\n  async readWidget(raw) {\n    const value = clean(raw);\n    return MethodTail(value);\n  }\n'
                '  privateOther() {\n    return "PRIVATE-METHOD-TAIL";\n  }\n}\n',
                'readWidget','return MethodTail(value);\n  }\n'))
        for code,query,tail in cases:
            with self.subTest(query=query,tail=tail):
                source='// filler\n'*200+code
                windows=code_windows(source,query,900,context_lines=0)
                literal=''.join(w['text'] for w in windows)
                self.assertIn(tail,literal)
                self.assertNotIn('PRIVATE-METHOD-TAIL',literal)
                self.assertExact(source,windows,900)

    def test_ambiguous_or_incomplete_brace_syntax_uses_literal_fallback(self):
        for body in ('const pattern = /}/;\n','const template = `result ${value}`;\n','/* unclosed comment\n'):
            source='// filler\n'*200+'function readWidget() {\n'+body+'  return AmbiguousTarget;\n}\n'
            windows=code_windows(source,'AmbiguousTarget',200,context_lines=0)
            self.assertIn('AmbiguousTarget',''.join(w['text'] for w in windows))
            self.assertTrue(all('complete-static-function' not in w['reason'] for w in windows))
            self.assertExact(source,windows,200)

    def test_static_parser_segment_limit_and_single_window_budget_are_respected(self):
        source='#'+('x'*200001)+'\ndef readWidget():\n    return SegmentLimitTarget\n'
        windows=code_windows(source,'SegmentLimitTarget',100,context_lines=0)
        self.assertTrue(all('complete-static-function' not in w['reason'] for w in windows))
        self.assertExact(source,windows,100)
        source='import math\n'+'# filler\n'*250+'def readWidget():\n    return math.sqrt(4)\n'
        windows=code_windows(source,'readWidget',150,max_windows=1,context_lines=0)
        self.assertIn('return math.sqrt(4)\n',''.join(w['text'] for w in windows))
        self.assertExact(source,windows,150,max_windows=1)

    def test_many_function_limit_opts_out_without_losing_literal_match(self):
        source=''.join(f'def unit_{index}():\n    return result_{index}\n' for index in range(257))
        windows=code_windows(source,'result_256',100,context_lines=0)
        self.assertIn('result_256',''.join(w['text'] for w in windows))
        self.assertTrue(all('complete-static-function' not in w['reason'] for w in windows))
        self.assertExact(source,windows,100)


if __name__=='__main__':unittest.main()
