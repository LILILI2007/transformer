import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from modlingo.core import ProjectError
from modlingo.provider import APIConfig
from modlingo.syntax import spans
from modlingo.test_budget import TestLedger
from modlingo.test_snapshot import freeze_sources
from modlingo.test_structures import select_fields, select_failed_fields, prepare_fields, run_fields


class StructuredRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = 'pause 1/quickQuestion Help?#Yes#No(break)message "Thanks!"(break)pause 1/end'
        self.row = {'mod_id': 'Test.Fixture', 'name': 'Fixture', 'version': '1',
                    'asset': 'Data/Events/Town', 'path': ['TestEvent'],
                    'source': self.source, 'category': 'quick_question',
                    'context': 'Data/Events/Town/TestEvent',
                    'fragments': len(spans(self.source, 'event')[0])}

    def test_materialization_preserves_original_source_and_identity(self):
        mods = prepare_fields([self.row], self.root/'Mods')
        manifest = json.loads((mods/'0/manifest.json').read_text(encoding='utf-8'))
        cp = json.loads((mods/'0/content.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['UniqueID'], 'Test.Fixture')
        self.assertEqual(cp['Changes'][0]['Entries']['TestEvent'], self.source)
        with self.assertRaises(ProjectError):
            prepare_fields([self.row], mods)
        self.assertFalse(any(mods.rglob('zh.json')))

    def test_duplicate_records_are_refused_before_writing(self):
        with self.assertRaises(ProjectError):
            prepare_fields([self.row, self.row], self.root/'Mods')
        self.assertFalse((self.root/'Mods').exists())

    def test_selection_uses_verified_original_snapshot(self):
        mods = prepare_fields([self.row], self.root/'Mods')
        snapshot = self.root/'snapshot'
        freeze_sources(mods, snapshot)
        rows, summary = select_fields(snapshot, 16)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source'], self.source)
        self.assertEqual(summary['sample_counts'], {'quick_question': 1})
        self.assertFalse(summary['full_coverage'])
        with self.assertRaises(ProjectError):select_fields(snapshot, 65)

    def test_guarded_cold_engine_installs_every_selected_field(self):
        async def run():
            ledger = TestLedger(self.root/'budget.sqlite3', clock=lambda:1000, deadline=10000)
            self.addCleanup(ledger.close)
            budget = ledger.begin('offline structured fixture', price_verified_at=1000)
            calls = []
            async def handler(request):
                calls.append(request)
                items = json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
                result = {key:'中文'+value['text'] for key,value in items.items()}
                return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],
                     'usage':{'prompt_tokens':10,'completion_tokens':10}})
            config = APIConfig('http://127.0.0.1:3333/v1','fixture')
            branch = dict(self.row, adapter='i18n', asset='', path=[], context='dialogue.question',
                          source='$q 1/2 next#Help?#$r 1 0 yes#Yes please',
                          category='dialogue_branch')
            branch['fragments'] = len(spans(branch['source'])[0])
            result = await run_fields(config, budget, [self.row, branch], self.root/'round',
                         transport=httpx.MockTransport(handler), use_processes=False)
            self.assertTrue(result['all_sample_passed'])
            self.assertEqual(result['completed_fields'], 2)
            self.assertEqual(result['installed_fields'], 2)
            self.assertEqual(result['requests'], len(calls))
            self.assertEqual(budget.snapshot()['requests_reserved'], len(calls))
            self.assertEqual(result['metrics']['cache_hits'], 0)
            self.assertFalse(result['cold_full_acceptance'])
            self.assertFalse(result['game_verified'])
            self.assertEqual(budget.snapshot()['unconfirmed_reserved_cny'], 0)
            self.assertEqual(result['metrics']['test_budget'],budget.snapshot())
        asyncio.run(run())

    def test_deadline_prevents_verification_http(self):
        async def run():
            clock = [1000]
            ledger = TestLedger(self.root/'budget.sqlite3', clock=lambda:clock[0], deadline=10000)
            self.addCleanup(ledger.close)
            budget = ledger.begin('offline timeout fixture', price_verified_at=1000)
            clock[0] = 3000
            calls = []
            async def handler(request):calls.append(request);return httpx.Response(200)
            with self.assertRaises(ProjectError):
                await run_fields(APIConfig('http://127.0.0.1:3333/v1','fixture'),budget,[self.row],
                                 self.root/'round',transport=httpx.MockTransport(handler),use_processes=False)
            self.assertEqual(calls, [])
            self.assertEqual(budget.snapshot()['requests_reserved'], 0)
            self.assertTrue((self.root/'round/verification-http.json').exists())
        asyncio.run(run())

    def previous_failure(self):
        mods=prepare_fields([self.row],self.root/'Mods')
        snapshot=self.root/'snapshot';freeze_sources(mods,snapshot)
        previous=self.root/'round-4-test';previous.mkdir()
        report=previous/'report.json'
        report.write_text(json.dumps({'failure':[{'mod_id':self.row['mod_id'],'skipped':[{'key':self.row['context']}]}]}),encoding='utf-8')
        (previous/'result.json').write_text(json.dumps({'kind':'structured_cold_sample_not_full_acceptance','report_path':str(report)}),encoding='utf-8')
        (previous/'structured-sources.json').write_text(json.dumps([dict(self.row,adapter='asset')]),encoding='utf-8')
        return snapshot,previous

    def test_failed_field_selection_checks_originals_and_uses_no_targets(self):
        snapshot,previous=self.previous_failure()
        selected,summary=select_failed_fields(snapshot,self.root,4)
        self.assertEqual(selected[0]['source'],self.source)
        self.assertFalse(summary['previous_paid_cache_used'])
        original=json.loads((previous/'structured-sources.json').read_text(encoding='utf-8'))
        original[0]['source']='Changed source'
        (previous/'structured-sources.json').write_text(json.dumps(original),encoding='utf-8')
        with self.assertRaises(ProjectError):select_failed_fields(snapshot,self.root,4)

    def test_failed_field_selection_refuses_external_reports(self):
        snapshot,previous=self.previous_failure()
        result=previous/'result.json'
        data=json.loads(result.read_text(encoding='utf-8'));data['report_path']=str(self.root/'outside.json')
        result.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ProjectError):select_failed_fields(snapshot,self.root,4)

    def test_exhausted_runner_does_not_read_credentials_or_request_price(self):
        import contextlib
        import io
        from unittest.mock import patch
        from scripts import run_limited_test as runner
        ledger=TestLedger(self.root/'finished.sqlite3',clock=lambda:1000,deadline=10000)
        for _ in range(5):ledger.begin('offline fixture',price_verified_at=1000).finish()
        with patch.object(runner,'ROOT',self.root),patch.object(runner,'TestLedger',return_value=ledger),\
             patch.object(runner.time,'time',return_value=1000),patch.object(runner,'verify_prices') as price,\
             patch.object(runner,'load') as credential,patch('sys.argv',['runner','--stage','structures','--sample','16']),\
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),1)
        price.assert_not_called();credential.assert_not_called()
        result=json.loads((self.root/'latest-result.json').read_text(encoding='utf-8'))
        self.assertIn('5',result['reason'])
