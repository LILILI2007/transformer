import unittest
from modlingo.core import validate_translation
from modlingo.syntax import spans,rebuild,validate_rebuilt,kind_for,parser_version


class BubbleTests(unittest.TestCase):
    def test_only_quoted_bubble_is_human_text(self):
        source='pause 1/textAboveHead Abigail "Hello farmer!"/pause 100/end'
        parts,notes=spans(source,'event')
        self.assertEqual(notes,[])
        self.assertEqual([p.text for p in parts],['Hello farmer!'])
        target=rebuild(source,parts,{'0':'你好，农夫！'})
        self.assertTrue(validate_rebuilt(source,target,'event'))
        self.assertFalse(validate_rebuilt(source,target.replace('Abigail','Emily'),'event'))

    def test_standalone_bubble_uses_structural_validation(self):
        source='textAboveHead Abigail "Hello!"'
        self.assertEqual(kind_for(source),'event')
        parts,notes=spans(source,'event')
        target=rebuild(source,parts,{'0':'你好！'})
        self.assertEqual(validate_translation(source,target),[])
        self.assertTrue(validate_translation(source,target.replace('Abigail','Emily')))

    def test_placeholder_actor_and_text_parameters_stay_intact(self):
        source='textAboveHead {{ActorID}} "Hello {{PlayerName}}!"'
        parts,notes=spans(source,'event')
        self.assertEqual(notes,[])
        target=rebuild(source,parts,{'0':'你好 {{PlayerName}}！'})
        self.assertTrue(validate_rebuilt(source,target,'event'))
        self.assertIn('{{ActorID}}',target)
        with self.assertRaises(ValueError):rebuild(source,parts,{'0':'你好！'})

    def test_unknown_arity_and_escapes_are_preserved(self):
        for source in ['textAboveHead "Abigail" "Hello"', 'textAboveHead Abigail Hello',
                       'textAboveHead Abigail "Hello" 500', 'textAboveHead Abigail "Hello" "Extra"',
                       r'textAboveHead Abigail "Bad\escape"']:
            parts,notes=spans(source,'event')
            self.assertEqual(parts,[])
            self.assertTrue(notes)

    def test_quoted_slash_and_escaped_quotes_roundtrip(self):
        source=r'textAboveHead Abigail "Tea/coffee? She said \"hello\"."'
        parts,notes=spans(source,'event')
        self.assertEqual(notes,[])
        self.assertEqual(parts[0].text,'Tea/coffee? She said "hello".')
        target=rebuild(source,parts,{'0':'茶/咖啡？她说“你好”。'})
        self.assertTrue(validate_rebuilt(source,target,'event'))

    def test_only_bubble_fields_change_cache_version(self):
        self.assertEqual(parser_version('Hello farmer'),'spans-2')
        self.assertEqual(parser_version('quickQuestion Help?#Yes(break)pause 1'),'spans-3-quickquestion')
        self.assertEqual(parser_version('pause 1/textAboveHead Abigail "Hello"'),'spans-4-bubble')
