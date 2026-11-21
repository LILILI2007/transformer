import json
from pathlib import Path
import shutil
import subprocess
import unittest

from modlingo import resource_rules as rules

ROOT=Path(__file__).resolve().parent.parent
CASES=[
    {'op':'path','asset':'data/minecarts','path':['Network','destinations','Case.ID','displayname'],
        'expected':['Network','Destinations','Case.ID','DisplayName']},
    {'op':'path','asset':'Data/Minecarts','path':['Network','Destinations','Case.ID','CustomFields','DisplayName'],'expected':None},
    {'op':'path','asset':'Data/MinecartsUnknown','path':['Network','LockedMessage'],'expected':None},
    {'op':'path','asset':'Data/Minecarts','path':['Network','Destinations','{{Id}}','DisplayName'],'expected':None},
    {'op':'path','asset':'Data/Minecarts','path':['Network','Destinations','A B','DisplayName'],'expected':None},
    {'op':'path','asset':'Data/WorldMap','path':['Region','mapareas','Area','ToolTips','Tip','text'],
        'expected':['Region','MapAreas','Area','Tooltips','Tip','Text']},
    {'op':'path','asset':'Data/WorldMap','path':['Region','CustomFields','Text'],'expected':None},
    {'op':'path','asset':'Data/Objects','path':['Id','Name'],'expected':None},
    {'op':'path','asset':'Data/Objects','path':['Id','Price'],'expected':None},
    {'op':'path','asset':'Data/Buildings','path':['Id','Name'],'expected':['Id','Name']},
    {'op':'path','asset':'Data/ObjectsExtension','path':['Id','DisplayName'],'expected':None},
    {'op':'path','asset':'Data/Objects','path':['Id','Description'],'expected':['Id','Description']},
    {'op':'path','asset':'Strings/UI','path':['Button'],'expected':['Button']},
    {'op':'asset','asset':'data\\minecarts','expected':'Data/Minecarts'},
    {'op':'asset','asset':'characters/dialogue/CaseSensitive','expected':'Characters/Dialogue/CaseSensitive'},
    {'op':'asset','asset':'Data/QuestsCustom','expected':None},
    {'op':'asset','asset':'Strings/{{Dynamic}}','expected':None},
    {'op':'list','asset':'Data/Minecarts','path':['N','Destinations'],'children':[{'Id':'B'},{'id':'A'}],'expected':['B','A']},
    {'op':'list','asset':'Data/Minecarts','path':['N','Destinations'],'children':[{'Id':'A'},{'Id':'A'}],'expected':None},
    {'op':'list','asset':'Data/Minecarts','path':['N','Destinations'],'children':[{'DisplayName':'Dock'}],'expected':None},
    {'op':'list','asset':'Data/Minecarts','path':['N','Destinations'],'children':[{'Id':1}],'expected':None},
    {'op':'list','asset':'Data/WorldMap','path':['N','MapAreas','Area','Tooltips'],'children':[{'Id':'Tip'}],'expected':['Tip']},
    {'op':'list','asset':'Data/WorldMap','path':['N','CustomFields'],'children':[{'Id':'Tip'}],'expected':None},
    {'op':'list','asset':'Strings/UI','path':['Group'],'children':['A','B'],'expected':['0','1']},
    {'op':'reference','value':'{{ I18N : place.Name }}','expected':'i18n'},
    {'op':'reference','value':'[LocalizedText Strings\\Locations:MineCart_Destination_BusStop]','expected':'localized'},
    {'op':'reference','value':'{{Custom.Name}}','expected':'unresolved'},
    {'op':'reference','value':'[PlayerName]','expected':'unresolved'},
    {'op':'reference','value':'[LocalizedText Strings/UI:Name argument]','expected':'unresolved'},
    {'op':'reference','value':'Farm','expected':'literal'},
    {'op':'reference','value':None,'expected':'unresolved'},
    {'op':'language','change':{'Action':'EditData','When':{'Language':'fr, ja'}},'expected':'When.Language=fr,ja'},
    {'op':'language','change':{'Action':'EditData','When':{'Language':'en, fr'}},'expected':''},
    {'op':'language','change':{'Action':'EditData','When':{'Language':'{{Language}}'}},'expected':''},
    {'op':'language','change':{'Action':'EditData','TargetLocale':'ja'},'expected':'TargetLocale=ja'},
    {'op':'language','change':{'Action':'Load','TargetLocale':'ja'},'expected':''},
    {'op':'identity','expected':{'version':rules.VERSION,'hash':rules.HASH}}
]


def evaluate(case):
    op=case['op']
    if op=='path':return rules.text_path(case['asset'],case['path'])
    if op=='asset':return rules.canonical_asset(case['asset']) if rules.asset_rule(case['asset']) else None
    if op=='list':return rules.list_ids(case['asset'],case['path'],case['children'])
    if op=='reference':return rules.reference_kind(case['value'])
    if op=='language':return rules.unrelated_language(case['change'])
    if op=='identity':return {'version':rules.VERSION,'hash':rules.HASH}
    raise AssertionError(op)


class ResourceContractTests(unittest.TestCase):
    def test_golden_rule_boundaries(self):
        for case in CASES:
            with self.subTest(case=case):self.assertEqual(evaluate(case),case['expected'])

    def test_python_bridge_use_identical_production_contract(self):
        import os
        dotnet=None
        for candidate in (os.environ.get('MODLINGO_DOTNET'),shutil.which('dotnet')):
            if candidate and subprocess.run([candidate,'--list-sdks'],capture_output=True,text=True).stdout.strip():
                dotnet=candidate;break
        if not dotnet:self.skipTest('需要 .NET 6 SDK 执行跨语言契约检查')
        project=ROOT/'bridge/contract/Contract.csproj'
        built=subprocess.run([dotnet,'build',str(project),'-c','Release','--nologo'],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)
        cases=list(CASES)
        # All asset families, positive and hostile fields, dynamic/invalid IDs.
        for rule in rules.RULES['assets']:
            asset=rule['name']+('Sample' if rule['name'].endswith('/') else '')
            for field in ('DisplayName','Name','Text','Price','Condition','description'):
                for identifier in ('Case.ID','{{Dynamic}}','A B'):
                    cases.append({'op':'path','asset':asset,'path':[identifier,field]})
        result=subprocess.run([dotnet,str(ROOT/'bridge/contract/bin/Release/net6.0/Contract.dll')],
            input=json.dumps(cases),capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        rows=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(rows),len(cases))
        for case,actual in zip(cases,rows):
            with self.subTest(case=case):self.assertEqual(actual,evaluate(case))


if __name__=='__main__':unittest.main()
