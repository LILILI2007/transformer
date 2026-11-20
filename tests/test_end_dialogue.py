import unittest
from modlingo.syntax import spans,rebuild,validate_rebuilt,kind_for,parser_version
from modlingo.core import validate_translation

class EndDialogueTests(unittest.TestCase):
    def test_game_resolved_capture_exposes_literal_closing_without_translating_i18n_reference(self):
        import json,tempfile
        from pathlib import Path
        from modlingo.discovery import scan_mods
        from modlingo.extraction import extract,source_hash
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'Pack';folder.mkdir()
            (folder/'manifest.json').write_text(json.dumps({'UniqueID':'Test.Closing','Name':'Fixture','Version':'1','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}}),encoding='utf-8')
            raw='pause 1/message "{{i18n:opening}}"/end dialogue Abigail "Literal closing"'
            (folder/'content.json').write_text(json.dumps({'Changes':[{'Action':'EditData','Target':'Data/Events/Town','Entries':{'Event':raw}}]}),encoding='utf-8')
            item=scan_mods(Path(temp))[0]
            self.assertFalse(extract(item).records)
            source=raw.replace('{{i18n:opening}}','Resolved opening')
            capture={'mod_id':item.mod_id,'mod_version':'1','adapter':'asset','asset':'Data/Events/Town','path':['Event'],'context':'Data/Events/Town/Event','source':source,'source_hash':source_hash(source)}
            bundle=extract(item,[capture])
            self.assertEqual([p.text for r in bundle.records for p in r.parts if p.role=='end-dialogue'],['Literal closing'])

    def test_only_closing_dialogue_is_text(self):
        for mode in ('dialogue','dialogueWarpOut'):
            source='pause 1/end '+mode+' Abigail "See you tomorrow!$h"'
            parts,notes=spans(source,'event')
            self.assertEqual(notes,[])
            self.assertEqual([p.text for p in parts],['See you tomorrow!$h'])
            self.assertEqual(parts[0].role,'end-dialogue')
            target=rebuild(source,parts,{'0':'明天见！$h'})
            self.assertTrue(validate_rebuilt(source,target,'event'))
            self.assertFalse(validate_rebuilt(source,target.replace('Abigail','Emily'),'event'))

    def test_standalone_and_version_isolation(self):
        source='end dialogue Abigail "Hello!"'
        self.assertEqual(kind_for(source),'event')
        self.assertEqual(parser_version(source),'spans-5-end-dialogue')
        self.assertEqual(parser_version('textAboveHead Abigail "Hello!"'),'spans-4-bubble')
        self.assertEqual(parser_version('Hello!'),'spans-2')
        target=rebuild(source,spans(source,'event')[0],{'0':'你好！'})
        self.assertEqual(validate_translation(source,target),[])
        self.assertTrue(validate_translation(source,target.replace('dialogue','dialogueWarpOut')))

    def test_unknown_arity_and_escapes_remain_untranslated(self):
        for source in ('end dialogue Abigail Hello','end dialogue "Abigail" "Hello"',
                       'end dialogue Abigail "Hello" extra',r'end dialogue Abigail "Bad\escape"'):
            parts,notes=spans(source,'event');self.assertEqual(parts,[]);self.assertTrue(notes)

    def test_other_end_modes_are_not_human_text(self):
        for source in ('end','end warpOut','end newDay','end invisible Abigail','end position 1 2'):
            self.assertEqual(spans(source,'event'),([],[]))

    def test_slashes_quotes_and_tokens_remain_structurally_safe(self):
        source=r'end dialogue {{Actor}} "Tea/coffee? She said \"hello\" to {{Player}}.$h"'
        parts,notes=spans(source,'event');self.assertEqual(notes,[])
        target=rebuild(source,parts,{'0':'茶/咖啡？她向 {{Player}} 问好。$h'})
        self.assertTrue(validate_rebuilt(source,target,'event'))
        with self.assertRaises(ValueError):rebuild(source,parts,{'0':'你好！'})

    def test_branch_closing_dialogue_and_later_text(self):
        source='quickQuestion Help?#Yes(break)end dialogue Abigail "Thanks!"/message "Later"'
        parts,notes=spans(source,'event');self.assertEqual(notes,[])
        self.assertEqual([p.text for p in parts],['Help?','Yes','Thanks!','Later'])
        target=rebuild(source,parts,{'0':'帮忙？','1':'好的','2':'谢谢！','3':'之后'})
        self.assertTrue(validate_rebuilt(source,target,'event'))
