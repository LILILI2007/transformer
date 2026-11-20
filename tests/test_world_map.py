import json
from pathlib import Path
import tempfile
import unittest
from modlingo.discovery import scan_mods
from modlingo.extraction import extract,world_map_path

class WorldMapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'Pack';self.folder.mkdir()
        (self.folder/'manifest.json').write_text(json.dumps({'UniqueID':'Test.Map','Name':'Map fixture','Version':'1','ContentPackFor':{'UniqueID':'Pathoschild.ContentPatcher'}}),encoding='utf-8')

    def bundle(self,changes):
        (self.folder/'content.json').write_text(json.dumps({'Changes':changes}),encoding='utf-8')
        return extract(scan_mods(Path(self.temp.name))[0])

    def patch(self,**kwargs):return dict({'Action':'EditData','Target':'Data/WorldMap'},**kwargs)

    def area(self,identifier='Area',text='Scroll text'):
        return {'Id':identifier,'ScrollText':text,'Condition':'PLAYER_HAS_MAIL Current InternalID',
                'PixelArea':{'X':1,'Y':2,'Width':3,'Height':4},'Tooltips':[{'Id':'Tip','Text':'Tooltip text','KnownCondition':'TRUE','LeftNeighbor':'OtherTip'}]}

    def test_nested_arrays_use_ids_and_only_explicit_visible_fields(self):
        b=self.bundle([self.patch(Entries={'Region':{'MapAreas':[self.area()]}})])
        self.assertEqual([r.path for r in b.records],[['Region','MapAreas','Area','ScrollText'],['Region','MapAreas','Area','Tooltips','Tip','Text']])
        self.assertEqual([r.source for r in b.records],['Scroll text','Tooltip text'])
        self.assertEqual(b.notes,[])

    def test_id_contexts_remain_stable_after_reordering(self):
        first=self.area();second=self.area('Second','Other scroll')
        a=self.bundle([self.patch(Entries={'Region':{'MapAreas':[first,second]}})])
        b=self.bundle([self.patch(Entries={'Region':{'MapAreas':[second,first]}})])
        self.assertEqual(sorted((u.context,u.source) for u in a.units),sorted((u.context,u.source) for u in b.units))

    def test_target_field_and_case_insensitive_model_names(self):
        b=self.bundle([self.patch(TargetField=['Region','mapareas','Area','ToolTips','Tip'],Entries={'text':'Nested tooltip'})])
        self.assertEqual(b.records[0].path,['Region','MapAreas','Area','Tooltips','Tip','Text'])

    def test_fields_model_path_works(self):
        b=self.bundle([self.patch(TargetField=['Region','MapAreas'],Fields={'Area':{'ScrollText':'Revised scroll'}})])
        self.assertEqual(b.records[0].path,['Region','MapAreas','Area','ScrollText'])

    def test_duplicate_missing_or_dynamic_ids_are_not_guessed(self):
        for areas in ([self.area(),self.area()], [{'ScrollText':'Unknown'}], [self.area('{{ID}}')]):
            b=self.bundle([self.patch(Entries={'Region':{'MapAreas':areas}})])
            self.assertEqual(b.records,[])
            self.assertTrue(b.notes)

    def test_explicit_index_and_dynamic_paths_stay_pending(self):
        for selector in ('#0','{{Area}}'):
            b=self.bundle([self.patch(TargetField=['Region','MapAreas',selector],Entries={'ScrollText':'Text'})])
            self.assertEqual(b.records,[]);self.assertTrue(b.notes)

    def test_nonvisual_custom_text_is_not_translated(self):
        b=self.bundle([self.patch(Entries={'Region':{'CustomFields':{'Text':'Internal value'},'BaseTexture':[{'Id':'Base','Texture':'EnglishPath'}]}})])
        self.assertEqual(b.records,[])

    def test_i18n_is_preserved_and_mixed_text_is_pending(self):
        b=self.bundle([self.patch(TargetField=['Region','MapAreas','Area'],Entries={'ScrollText':'Hello {{i18n:Name}}'})])
        self.assertEqual(b.records,[]);self.assertEqual(len(b.pending_captures),1)
        self.assertEqual(b.pending_captures[0]['path'],['Region','MapAreas','Area','ScrollText'])

    def test_world_map_normalization_does_not_rewrite_ids(self):
        self.assertEqual(world_map_path(['Region','mapareas','CaseSensitiveID','scrolltext']),['Region','MapAreas','CaseSensitiveID','ScrollText'])
        self.assertIsNone(world_map_path(['Region','MapAreas','Area','CustomFields','Text']))

    def test_other_dynamic_tokens_require_actual_game_source(self):
        b=self.bundle([self.patch(TargetField=['Region','MapAreas','Area'],Entries={'ScrollText':'Hello {{WeatherName}}'})])
        self.assertEqual(b.records,[]);self.assertTrue(b.notes)

    def test_similarly_named_unknown_asset_is_not_world_map(self):
        b=self.bundle([self.patch(Target='Data/WorldMapUnknown',Entries={'Title':'Internal'})])
        self.assertEqual(b.records,[]);self.assertTrue(b.notes)
