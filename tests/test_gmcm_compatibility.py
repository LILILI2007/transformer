import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from modlingo.bridge_io import plugin_status,ensure_plugin
from modlingo.discovery import scan_mods
from modlingo.extraction import extract,source_hash
from modlingo.syntax import spans,rebuild,validate_rebuilt
from modlingo.core import MASK_TOKEN,ProjectError

class GmcmCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mods=self.root/'Mods';self.mods.mkdir()
        self.folder=self.mods/'Fixture';self.folder.mkdir()

    def item(self):
        (self.folder/'manifest.json').write_text(json.dumps({'UniqueID':'Test.Fixture','Name':'Friends Forever','Version':'1.0.0','EntryDll':'Fixture.dll'}))
        return scan_mods(self.mods)[0]

    def test_names_are_independent_display_records_and_capture_is_deduplicated(self):
        item=self.item();original=(self.folder/'manifest.json').read_bytes()
        capture={'mod_id':item.mod_id,'mod_version':item.version,'context':'gmcm/mod-name','adapter':'gmcm',
                 'source':'Friends Forever','source_hash':source_hash('Friends Forever')}
        bundle=extract(item,[capture,capture])
        names=[record for record in bundle.records if record.context=='gmcm/mod-name']
        self.assertEqual(len(names),1);self.assertEqual(names[0].adapter,'gmcm')
        self.assertEqual(bundle.category,'部分支持')
        self.assertEqual((self.folder/'manifest.json').read_bytes(),original)

    def test_invalid_i18n_does_not_block_independent_runtime_capture(self):
        item=self.item();(self.folder/'i18n').mkdir()
        broken=b'{"invalid":"unterminated'
        (self.folder/'i18n/default.json').write_bytes(broken)
        capture={'mod_id':item.mod_id,'mod_version':item.version,'context':'gmcm/AddBoolOption/name','adapter':'gmcm',
                 'source':'Enable friendship','source_hash':source_hash('Enable friendship')}
        bundle=extract(item,[capture])
        self.assertIsNone(bundle.project)
        self.assertTrue(any(record.source=='Enable friendship' for record in bundle.records))
        self.assertTrue(any('i18n 文件异常' in note for note in bundle.notes))
        self.assertEqual((self.folder/'i18n/default.json').read_bytes(),broken)

    def test_query_branches_and_actions_are_preserved_exactly(self):
        source='$query PLAYER_HAS_SEEN_EVENT Current 5978924#Hello farmer!|Welcome back.$action AddMail Current Test.Mail'
        parts,notes=spans(source)
        self.assertEqual(notes,[])
        self.assertEqual([part.text for part in parts],['Hello farmer!','Welcome back.'])
        target=rebuild(source,parts,{'0':'农夫你好！','1':'欢迎回来。'})
        self.assertTrue(validate_rebuilt(source,target))
        self.assertFalse(validate_rebuilt(source,target.replace('5978924','1')))
        self.assertFalse(validate_rebuilt(source,target.replace('Test.Mail','Other.Mail')))
        protected=MASK_TOKEN.findall(source)
        self.assertTrue(any('PLAYER_HAS_SEEN_EVENT' in value for value in protected))
        self.assertTrue(any('AddMail Current Test.Mail' in value for value in protected))

    def test_item_list_with_trailing_space_is_protected(self):
        source='Enjoy dinner! [213 618 606 237 230 ]$1'
        parts,notes=spans(source)
        self.assertEqual(notes,[])
        target=rebuild(source,parts,{'0':'享用晚餐吧！ '})
        self.assertTrue(validate_rebuilt(source,target))
        self.assertIn('[213 618 606 237 230 ]',target)

    def test_action_ends_at_branch_and_preserves_quoted_arguments(self):
        source='$query PLAYER_HAS_MAIL Current Test#Hello!$action AddMail Test received|Welcome back.'
        parts,notes=spans(source)
        self.assertEqual([part.text for part in parts],['Hello!','Welcome back.'])
        self.assertEqual(notes,[])
        source='Hello!$action Custom.Dialogue NPC "Untranslated action # payload| stays" Portraits/NPC/image#Welcome!'
        parts,notes=spans(source)
        self.assertEqual([part.text for part in parts],['Hello!','Welcome!'])
        target=rebuild(source,parts,{'0':'你好！','1':'欢迎！'})
        self.assertTrue(validate_rebuilt(source,target))
        self.assertIn('"Untranslated action # payload| stays" Portraits/NPC/image',target)

    def test_plugin_repair_and_upgrade_preserve_player_dictionaries(self):
        source=self.root/'package';source.mkdir()
        (source/'manifest.json').write_text(json.dumps({'UniqueID':'ModLingo.Bridge','EntryDll':'ModLingo.Bridge.dll','Version':'0.5.1'}))
        (source/'ModLingo.Bridge.dll').write_bytes(b'own-fixture-dll')
        target=self.mods/'ModLingo.Bridge';(target/'translations').mkdir(parents=True)
        dictionary=target/'translations/player.json';dictionary.write_bytes(b'preserve-exactly')
        self.assertFalse(plugin_status(self.mods)['ready'])
        with patch('modlingo.batch.check_game_stopped'):
            self.assertTrue(ensure_plugin(self.mods,source)['ready'])
            old=json.loads((target/'manifest.json').read_text());old['Version']='0.5.0'
            (target/'manifest.json').write_text(json.dumps(old))
            (target/'ModLingo.Bridge.dll').write_bytes(b'old')
            self.assertEqual(ensure_plugin(self.mods,source)['version'],'0.5.1')
        self.assertEqual(dictionary.read_bytes(),b'preserve-exactly')

    def test_plugin_cannot_overwrite_a_foreign_mod(self):
        source=self.root/'package';source.mkdir()
        (source/'manifest.json').write_text(json.dumps({'Version':'0.5.1'}))
        (source/'ModLingo.Bridge.dll').write_bytes(b'own')
        target=self.mods/'ModLingo.Bridge';target.mkdir()
        (target/'manifest.json').write_text('{"UniqueID":"Foreign.Mod"}')
        with patch('modlingo.batch.check_game_stopped'):
            with self.assertRaises(ProjectError):ensure_plugin(self.mods,source)
        self.assertFalse((target/'ModLingo.Bridge.dll').exists())
