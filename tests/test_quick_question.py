import unittest
from modlingo.syntax import spans,rebuild,validate_rebuilt

class QuickQuestionTests(unittest.TestCase):
    def test_question_choices_and_branch_text_are_separate(self):
        source='pause 1/quickQuestion Help me?#Yes#No(break)speak Abigail "Thank you!"\\pause 100(break)switchEvent keep_original_id/end'
        parts,notes=spans(source,'event')
        self.assertEqual(notes,[])
        self.assertEqual([p.text for p in parts],['Help me?','Yes','No','Thank you!'])
        target=rebuild(source,parts,{'0':'帮帮我？','1':'好的','2':'不了','3':'谢谢你！'})
        self.assertTrue(validate_rebuilt(source,target,'event'))
        self.assertIn('(break)switchEvent keep_original_id',target)
        self.assertIn('\\pause 100',target)
        self.assertFalse(validate_rebuilt(source,target.replace('keep_original_id','changed'),'event'))

    def test_question_without_prompt_preserves_answer_order(self):
        source='quickQuestion #Accept#Decline(break)pause 1(break)pause 2'
        parts,notes=spans(source,'event')
        self.assertEqual(notes,[])
        self.assertEqual([p.text for p in parts],['Accept','Decline'])
        self.assertTrue(validate_rebuilt(source,rebuild(source,parts,{'0':'接受','1':'拒绝'}),'event'))

    def test_quoted_branch_is_not_selected_as_header_on_invalid_format(self):
        for source in ['quickQuestion "Question#Yes#No"(break)speak Abigail "First"(break)speak Abigail "Last"',
                       'quickQuestion Question#Yes#No(break)speak Abigail "Last"',
                       'quickQuestion Question#Yes(break)',
                       'quickQuestion Question(break)pause 1']:
            parts,notes=spans(source,'event')
            self.assertEqual(parts,[])
            self.assertTrue(notes)

    def test_unknown_branch_command_is_preserved_and_reported(self):
        source='quickQuestion Help?#Yes(break)customCommand InternalID "Must not translate"'
        parts,notes=spans(source,'event')
        self.assertEqual([p.text for p in parts],['Help?','Yes'])
        self.assertTrue(any('customCommand' in note for note in notes))
        target=rebuild(source,parts,{'0':'帮助？','1':'是'})
        self.assertIn('customCommand InternalID "Must not translate"',target)
        self.assertTrue(validate_rebuilt(source,target,'event'))

    def test_structural_injection_is_rejected(self):
        source='quickQuestion Help?#Yes(break)pause 1'
        parts,_=spans(source,'event')
        for injection in ['帮助(break)pause 99','帮助/warp SecretRoom','帮助\\pause 99','"帮助"']:
            try:target=rebuild(source,parts,{'0':injection,'1':'是'})
            except ValueError:continue
            self.assertFalse(validate_rebuilt(source,target,'event'))

    def test_branch_quoted_slash_is_not_an_outer_command(self):
        source='pause 1/quickQuestion Drink?#Yes(break)message "Tea/coffee?"/end'
        parts,notes=spans(source,'event')
        self.assertEqual(notes,[])
        self.assertEqual([p.text for p in parts],['Drink?','Yes','Tea/coffee?'])
        target=rebuild(source,parts,{'0':'喝点什么？','1':'好','2':'茶/咖啡？'})
        self.assertTrue(validate_rebuilt(source,target,'event'))

    def test_only_changed_parser_paths_get_new_cache_version(self):
        from modlingo.syntax import kind_for,parser_version
        self.assertEqual(kind_for('quickQuestion Help?#Yes(break)pause 1'),'event')
        self.assertEqual(parser_version('Hello farmer'),'spans-2')
        self.assertEqual(parser_version('quickQuestion Help?#Yes(break)pause 1'),'spans-3-quickquestion')

    def test_entry_validation_protects_branch_without_outer_slashes(self):
        from modlingo.core import validate_translation
        source='quickQuestion Help?#Yes(break)switchEvent KeepID'
        parts,_=spans(source,'event')
        target=rebuild(source,parts,{'0':'帮助？','1':'是'})
        self.assertEqual(validate_translation(source,target),[])
        self.assertTrue(validate_translation(source,target.replace('KeepID','ChangedID')))
