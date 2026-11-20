import unittest
from modlingo.syntax import event_header_end,spans,rebuild,validate_rebuilt,parser_version

class EventHeaderTests(unittest.TestCase):
    def test_literal_header_preserved_without_unknown_command_notes(self):
        source='50s/-1000 -1000/farmer -30 30 0 Penny 12 7 0/message "Hello!"/end'
        parts,notes=spans(source,'event')
        self.assertEqual(notes,[])
        self.assertEqual([p.text for p in parts],['Hello!'])
        target=rebuild(source,parts,{'0':'你好！'})
        self.assertTrue(validate_rebuilt(source,target,'event'))
        for changed in (target.replace('50s','other'),target.replace('-1000 -1000','1 2'),target.replace('Penny','Emily')):
            self.assertFalse(validate_rebuilt(source,changed,'event'))

    def test_actual_custom_names_and_continue_music(self):
        for music in ('continue','none','Overcast','mod.track'):
            source=music+'/-100 -100/farmer 12 12 1 MadDog.HashtagBearFam.Gunnar? 14 15 0/message "Hello"'
            self.assertTrue(event_header_end(source))
            self.assertEqual(spans(source,'event')[1],[])

    def test_incomplete_dynamic_or_invalid_headers_stay_reported(self):
        for source in ('song/1 2/farmer 1 2/message "Hello"',
                       'song/1 2/farmer 1 2 4/message "Hello"',
                       'song/{{X}} 2/farmer 1 2 0/message "Hello"',
                       'song/1 2/{{Actor}} 1 2 0/message "Hello"',
                       'song/1 2/farmer 1 2 0',
                       'song/1 2 3/farmer 1 2 0/message "Hello"'):
            self.assertEqual(event_header_end(source),0)
            self.assertTrue(spans(source,'event')[1])

    def test_command_prefix_and_noninitial_header_are_not_silenced(self):
        self.assertEqual(event_header_end('message/1 2/farmer 1 2 0/end'),0)
        self.assertTrue(spans('pause 1/song/1 2/farmer 1 2 0/end','event')[1])

    def test_unknown_body_command_stays_explicit(self):
        parts,notes=spans('none/1 2/farmer 1 2 0/customCommand ID "Unknown"/message "Hello"','event')
        self.assertEqual([p.text for p in parts],['Hello'])
        self.assertEqual(notes,['未处理事件命令，原样保留：customCommand'])

    def test_header_in_quick_question_branch_is_not_assumed(self):
        source='quickQuestion Help?#Yes(break)song\\1 2\\farmer 1 2 0'
        self.assertTrue(spans(source,'event')[1])

    def test_header_does_not_invalidate_existing_fragment_caches(self):
        self.assertEqual(parser_version('none/1 2/farmer 1 2 0/message "Hello"'),'spans-2')
        self.assertEqual([p.text for p in spans('none/1 2/farmer 1 2 0/message "Hello"','event')[0]],['Hello'])
