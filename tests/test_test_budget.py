import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
import threading
from unittest.mock import patch
import httpx
from modlingo.test_budget import TestLedger,BudgetStop
from modlingo.test_pricing import parse_prices
from modlingo.async_provider import Session,Budget
from modlingo.provider import Translator,APIConfig
from modlingo.engine import Engine
from modlingo.discovery import scan_mods
from modlingo.cache import Cache

class TestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'budget.sqlite3'
        self.clock=[1000.0]
        self.ledger=TestLedger(self.path,clock=lambda:self.clock[0],deadline=10000)
        self.addCleanup(self.ledger.close)

    def begin(self,seconds=1200):return self.ledger.begin('offline fixture',seconds,price_verified_at=self.clock[0])

    def test_persistent_policy_and_unknown_requests_survive_restart(self):
        round=self.begin();ticket=round.reserve([],8192);round.settle(ticket,{})
        before=self.ledger.snapshot();round.finish()
        other=TestLedger(self.path,clock=lambda:self.clock[0]);self.addCleanup(other.close)
        self.assertEqual(other.snapshot(),before)
        self.assertGreater(before['unconfirmed_reserved_cny'],0)
        self.assertEqual(other.maximum,5)

    def test_no_overlapping_rounds_and_max_five(self):
        first=self.begin()
        with self.assertRaises(BudgetStop):self.begin()
        first.finish()
        for _ in range(4):self.begin().finish()
        with self.assertRaises(BudgetStop):self.begin()

    def test_price_verification_and_20minute_limit(self):
        with self.assertRaises(BudgetStop):self.ledger.begin('unverified')
        with self.assertRaises(BudgetStop):self.begin(1201)
        round=self.begin();self.clock[0]+=1201
        with self.assertRaises(BudgetStop):round.reserve([],256)

    def test_at_deadline_nothing_is_sent(self):
        self.clock[0]=10000
        with self.assertRaises(BudgetStop):self.begin()

    def test_usage_settlement_counts_cache_and_known_bill(self):
        round=self.begin();ticket=round.reserve([],1024)
        round.settle(ticket,{'prompt_tokens':1000,'completion_tokens':500,'prompt_cache_hit_tokens':100})
        result=round.snapshot()
        self.assertAlmostEqual(result['conservative_cny'],.005804)
        self.assertAlmostEqual(result['known_estimated_bill_cny'],.002902)
        self.assertEqual(result['unconfirmed_reserved_cny'],0)
        round.settle(ticket,{'prompt_tokens':0,'completion_tokens':0})
        self.assertEqual(round.snapshot(),result)

    def test_provider_bound_breach_freezes_round(self):
        round=self.begin();ticket=round.reserve([],256)
        with self.assertRaises(BudgetStop):round.settle(ticket,{'prompt_tokens':10,'completion_tokens':257})
        with self.assertRaises(BudgetStop):round.reserve([],256)

    def test_lower_limits_cannot_be_raised_by_reopening(self):
        path=Path(self.temp.name)/'small.sqlite3'
        small=TestLedger(path,round_cny=.01,total_cny=.02,clock=lambda:1000,deadline=10000)
        small.close()
        reopened=TestLedger(path,clock=lambda:1000);self.addCleanup(reopened.close)
        self.assertEqual(reopened.round_limit,10_000_000)
        self.assertEqual(reopened.total_limit,20_000_000)

    def test_concurrent_reservations_never_overspend(self):
        async def run():
            ledger=TestLedger(Path(self.temp.name)/'limited.sqlite3',round_cny=.012,total_cny=.012,clock=lambda:1000,deadline=10000)
            self.addCleanup(ledger.close);round=ledger.begin('concurrency',price_verified_at=1000)
            calls=[]
            async def handler(request):
                calls.append(request)
                await asyncio.sleep(.01)
                return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
            config=APIConfig('http://127.0.0.1:3333/v1','fixture',max_output_tokens=256)
            async with Session(config,32,transport=httpx.MockTransport(handler),test_budget=round) as session:
                results=await asyncio.gather(*(session.post(Translator(config),[],Budget()) for _ in range(32)),return_exceptions=True)
            self.assertEqual(len(calls),1)
            self.assertEqual(sum(isinstance(row,BudgetStop) for row in results),31)
            self.assertLessEqual(round.snapshot()['conservative_cny'],.012)
        asyncio.run(run())

    def test_cancellation_preserves_reservation_and_cleans_slots(self):
        async def run():
            round=self.begin();entered=asyncio.Event()
            async def handler(request):entered.set();await asyncio.sleep(20)
            config=APIConfig('http://127.0.0.1:3333/v1','fixture')
            async with Session(config,transport=httpx.MockTransport(handler),test_budget=round) as session:
                task=asyncio.create_task(session.post(Translator(config),[],Budget()))
                await entered.wait();task.cancel();await asyncio.gather(task,return_exceptions=True)
                self.assertEqual(session.active,0)
            self.assertGreater(round.snapshot()['unconfirmed_reserved_cny'],0)
        asyncio.run(run())

    def test_temporary_reservations_wait_instead_of_cancelling_paid_work(self):
        async def run():
            ledger=TestLedger(Path(self.temp.name)/'wait.sqlite3',round_cny=.012,total_cny=.012,clock=lambda:1000,deadline=10000)
            self.addCleanup(ledger.close);round=ledger.begin('wait',price_verified_at=1000)
            calls=[];stats=[]
            async def handler(request):
                calls.append(request);await asyncio.sleep(.01)
                return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}],'usage':{'prompt_tokens':10,'completion_tokens':5}})
            config=APIConfig('http://127.0.0.1:3333/v1','fixture',max_output_tokens=256)
            async with Session(config,8,transport=httpx.MockTransport(handler),test_budget=round,on_stats=stats.append) as session:
                await asyncio.gather(*(session.post(Translator(config),[],Budget()) for _ in range(8)))
            self.assertEqual(len(calls),8)
            self.assertGreater(max(row['waiting_budget'] for row in stats),0)
            self.assertEqual(max(row['in_flight'] for row in stats),1)
            self.assertEqual(round.snapshot()['unconfirmed_reserved_cny'],0)
            self.assertLess(round.snapshot()['conservative_cny'],.012)
        asyncio.run(run())

    def test_network_retries_reserve_independently(self):
        async def run():
            round=self.begin()
            async def handler(request):raise httpx.ConnectError('fixture')
            config=APIConfig('http://127.0.0.1:3333/v1','fixture')
            async with Session(config,transport=httpx.MockTransport(handler),test_budget=round) as session:
                with self.assertRaises(ValueError):await session.post(Translator(config),[],Budget())
            self.assertEqual(round.snapshot()['requests_reserved'],3)
            self.assertGreater(round.snapshot()['unconfirmed_reserved_cny'],.1)
        asyncio.run(run())

    def test_active_request_deadline_keeps_cost_and_stops_retry(self):
        async def run():
            round=self.begin(.05);calls=[]
            async def handler(request):calls.append(request);await asyncio.sleep(2)
            config=APIConfig('http://127.0.0.1:3333/v1','fixture')
            async with Session(config,transport=httpx.MockTransport(handler),test_budget=round) as session:
                with self.assertRaises(BudgetStop):await session.post(Translator(config),[],Budget())
                self.assertEqual(session.active,0)
            self.assertEqual(len(calls),1)
            self.assertGreater(round.snapshot()['unconfirmed_reserved_cny'],0)
        asyncio.run(run())

    def test_cancelled_engine_writes_recoverable_report(self):
        async def run():
            mods=Path(self.temp.name)/'Mods';folder=mods/'A';(folder/'i18n').mkdir(parents=True)
            (folder/'manifest.json').write_text('{"UniqueID":"Test.A","Name":"A","Version":"1"}')
            (folder/'i18n/default.json').write_text('{"hello":"Hello farmer"}')
            entered=asyncio.Event();round=self.begin()
            async def handler(request):entered.set();await asyncio.sleep(2)
            config=APIConfig('http://127.0.0.1:3333/v1','fixture')
            storage=Path(self.temp.name)/'cache'
            with patch('modlingo.engine.check_game_stopped'),patch('modlingo.batch.check_game_stopped'):
                engine=Engine(scan_mods(mods),mods,config,threading.Event(),storage,transport=httpx.MockTransport(handler),test_budget=round,use_processes=False)
                task=asyncio.create_task(engine.run());await entered.wait();task.cancel()
                path,report=await task
            self.assertTrue(path.exists());self.assertIn('取消',report['paused_reason'])
            cache=Cache(storage)
            try:self.assertEqual(cache.db.execute('SELECT state FROM attempts').fetchall(),[('pending_retry',)])
            finally:cache.close()
            self.assertFalse((folder/'i18n/zh.json').exists())
        asyncio.run(run())

    def test_changed_price_fails_closed(self):
        rows='<tr><th>deepseek-flash</th><th>deepseek-v4-pro</th></tr>'
        for price in ('0.02','0.04','1','2','4','8'):rows+='<tr><td>'+price+'元</td><td>99元</td></tr>'
        self.assertIn('verified_at',parse_prices('<table>'+rows+'</table>'))
        with self.assertRaises(BudgetStop):parse_prices('<table>'+rows.replace('8元','9元')+'</table>')
