import asyncio
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import httpx

from modlingo.async_provider import Session, Budget, translate_recover
from modlingo.core import ProjectError
from modlingo.discovery import scan_mods
from modlingo.engine import Engine
from modlingo.provider import APIConfig, Translator
from modlingo.usage_limits import UsageBudget, UsageLimit, UsageWait, limit_summary


class UsageTests(unittest.TestCase):
    def config(self, **values):
        return replace(APIConfig('http://127.0.0.1:3333/v1', 'mock'), **values)

    def test_invalid_limits_and_prices_are_rejected(self):
        for values in ({'run_request_limit': 0}, {'run_request_limit': True},
                       {'run_token_limit': -1}, {'input_price_cny': math.nan},
                       {'output_price_cny': math.inf}, {'input_price_cny': -1}):
            with self.subTest(values=values), self.assertRaises(ProjectError):
                self.config(**values).endpoint()
        self.assertIn('无法估算', limit_summary(self.config()))
        self.assertIn('2.0000 元', limit_summary(self.config(input_price_cny=.5, output_price_cny=1)))

    def test_reservation_wait_settlement_unknown_and_provider_overrun(self):
        budget = UsageBudget(self.config(run_token_limit=2400))
        ticket = budget.reserve({}, 256)
        with self.assertRaises(UsageWait): budget.reserve({}, 256)
        budget.settle(ticket, {'prompt_tokens': 10, 'completion_tokens': 20})
        ticket = budget.reserve({}, 256)
        budget.settle(ticket, {})
        self.assertEqual(budget.snapshot()['unknown_requests'], 1)
        with self.assertRaises(UsageLimit): budget.reserve({}, 256)
        budget = UsageBudget(self.config())
        ticket = budget.reserve({}, 256)
        budget.settle(ticket, {'input_tokens': 10, 'output_tokens': 400})
        self.assertEqual(budget.snapshot()['provider_overruns'], 1)
        with self.assertRaises(UsageLimit): budget.reserve({}, 256)

    def test_claude_cache_and_reasoning_are_not_lost_or_double_counted(self):
        budget = UsageBudget(self.config(input_price_cny=1, output_price_cny=2))
        ticket = budget.reserve({}, 8192)
        budget.settle(ticket, {'input_tokens': 10, 'cache_read_input_tokens': 20,
                              'cache_creation_input_tokens': 30, 'output_tokens': 50, 'reasoning_tokens': 40})
        result = budget.snapshot()
        self.assertEqual(result['charged_or_reserved_tokens'], 110)
        self.assertAlmostEqual(result['estimated_cny'], .00016)

    def test_concurrent_retries_cannot_exceed_request_cap(self):
        async def run():
            calls = []
            async def handler(request):
                calls.append(request)
                await asyncio.sleep(.01)
                return httpx.Response(503)
            async with Session(self.config(run_request_limit=2), 4, transport=httpx.MockTransport(handler)) as session:
                tasks = [session.post(Translator(session.config), [], Budget()) for _ in range(4)]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(all(isinstance(result, UsageLimit) for result in results))
                self.assertEqual(len(calls), 2)
                self.assertEqual(session.usage_budget.snapshot()['unknown_requests'], 2)
        asyncio.run(run())

    def test_insufficient_token_budget_and_cancellation(self):
        async def run():
            calls = []
            async def handler(request):
                calls.append(request)
                await asyncio.sleep(30)
            config = self.config(run_token_limit=1)
            async with Session(config, transport=httpx.MockTransport(handler)) as session:
                with self.assertRaises(UsageLimit):
                    await session.post(Translator(config), [], Budget())
                self.assertFalse(calls)
            config = self.config()
            async with Session(config, transport=httpx.MockTransport(handler)) as session:
                task = asyncio.create_task(session.post(Translator(config), [], Budget()))
                while not calls: await asyncio.sleep(.001)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                result = session.usage_budget.snapshot()
                self.assertEqual(result['unknown_requests'], 1)
                self.assertEqual(result['active_reservations'], 0)
                self.assertGreater(result['charged_or_reserved_tokens'], 8192)
        asyncio.run(run())

    def test_format_repair_uses_same_run_budget(self):
        async def run():
            calls = []
            async def handler(request):
                calls.append(request)
                return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
            async with Session(self.config(run_request_limit=1), transport=httpx.MockTransport(handler)) as session:
                with self.assertRaises(UsageLimit):
                    await translate_recover(session, [('a', 'Hello'), ('b', 'World')], {}, lambda *a: None, lambda *a: None)
            self.assertEqual(len(calls), 1)
        asyncio.run(run())

    def test_deadline_timeout_never_retries_when_wall_clock_has_not_expired(self):
        from types import SimpleNamespace
        from modlingo.test_budget import BudgetStop
        original_wait = asyncio.wait_for
        async def early_timer(awaitable, timeout):
            if getattr(getattr(awaitable, 'cr_code', None), 'co_name', '') == 'receive':
                awaitable.close()
                raise asyncio.TimeoutError
            return await original_wait(awaitable, timeout)
        authorized = SimpleNamespace(remaining=lambda: .05, check=lambda: None,
                                     reserve=lambda *a: 1, settle=lambda *a: None)
        async def run():
            calls = []
            async def handler(request): calls.append(request)
            async with Session(self.config(), transport=httpx.MockTransport(handler), test_budget=authorized) as session:
                with patch('modlingo.async_provider.asyncio.wait_for', side_effect=early_timer):
                    with self.assertRaises(BudgetStop):
                        await session.post(Translator(session.config), [], Budget())
                self.assertEqual(session.requests, 1)
                self.assertFalse(calls)
        asyncio.run(run())

    def test_engine_preserves_completed_batches_when_limit_pauses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = root / 'Mods'
            folder = mods / 'Fixture'
            (folder / 'i18n').mkdir(parents=True)
            (folder / 'manifest.json').write_text(json.dumps({'UniqueID': 'Test.Limits', 'Name': 'fixture', 'Version': '1'}))
            (folder / 'i18n/default.json').write_text(json.dumps({str(i): 'Hello friend %d' % i for i in range(60)}))
            calls = []
            async def handler(request):
                calls.append(request)
                items = json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
                text = json.dumps({key: '你好朋友' + row['text'] for key, row in items.items()})
                await asyncio.sleep(.02)
                return httpx.Response(200, json={'choices': [{'message': {'content': text}}]})
            with patch('modlingo.engine.check_game_stopped'), patch('modlingo.batch.check_game_stopped'):
                engine = Engine(scan_mods(mods), mods, self.config(run_request_limit=1), threading.Event(),
                                storage=root / 'state', transport=httpx.MockTransport(handler), use_processes=False)
                _, report = asyncio.run(engine.run())
            self.assertEqual(len(calls), 1)
            self.assertIn('请求上限', report['paused_reason'])
            self.assertGreater(report['metrics']['completed'], 0)
            self.assertEqual(report['metrics']['usage_budget']['requests'], 1)
            self.assertGreater(len(json.loads((folder / 'i18n/zh.json').read_text(encoding='utf-8'))), 0)
