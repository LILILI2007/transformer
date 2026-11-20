"""Durable prepaid reservations for the explicitly authorized DeepSeek tests.

Amounts are integer nanoyuan. Unknown/cancelled requests keep their entire
reservation. This ledger bounds these test calls, not unrelated account usage.
"""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
import uuid
from .core import ProjectError

NANO=1_000_000_000
DEADLINE=datetime.fromisoformat('2026-10-17T20:00:00+08:00').timestamp()
PRICE_URL='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'

class BudgetStop(ProjectError):pass
class BudgetWait(ProjectError):pass

class TestLedger:
    __test__=False
    def __init__(self,path,round_cny=3,total_cny=15,max_rounds=5,deadline=DEADLINE,clock=time.time):
        if not 0<round_cny<=3 or not 0<total_cny<=15 or type(max_rounds) is not int or not 1<=max_rounds<=5:
            raise BudgetStop('预算配置不能超过每轮3元、累计15元、最多5轮')
        deadline=min(deadline,DEADLINE)
        self.clock=clock;path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),timeout=10,isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),round_nano INTEGER,total_nano INTEGER,max_rounds INTEGER,deadline REAL);
          CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY,purpose TEXT,start REAL,end REAL,state TEXT);
          CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,round_id TEXT,reserved INTEGER,charge INTEGER,actual INTEGER,state TEXT,input_bound INTEGER,output_bound INTEGER,usage TEXT);
        ''')
        self.db.execute('INSERT OR IGNORE INTO policy VALUES(1,?,?,?,?)',
                        (round(round_cny*NANO),round(total_cny*NANO),max_rounds,deadline))
        # Reopening with different constructor values cannot reset authorization.
        self.round_limit,self.total_limit,self.maximum,self.deadline=self.db.execute('SELECT round_nano,total_nano,max_rounds,deadline FROM policy').fetchone()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield;self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise

    def begin(self,purpose,seconds=1200,price_verified_at=None):
        now=self.clock()
        if not price_verified_at or not 0<=now-price_verified_at<=3600:
            raise BudgetStop('未取得本轮有效官方价格核对记录，未发送请求')
        if not 0<seconds<=1200:raise BudgetStop('每轮测试最多 1200 秒')
        with self.transaction():
            if now>=self.deadline:raise BudgetStop('已到北京时间 20:00，停止付费测试')
            self.db.execute("UPDATE rounds SET state='expired' WHERE state='active' AND end<=?",(now,))
            if self.db.execute("SELECT 1 FROM rounds WHERE state='active'").fetchone():
                raise BudgetStop('已有测试轮次进行中，拒绝并行启动另一轮')
            if self.db.execute('SELECT COUNT(*) FROM rounds').fetchone()[0]>=self.maximum:
                raise BudgetStop('已达到最多 5 轮测试上限')
            if self.db.execute('SELECT COALESCE(SUM(charge),0) FROM tickets').fetchone()[0]>=self.total_limit:
                raise BudgetStop('累计测试预算已耗尽')
            identity=uuid.uuid4().hex
            self.db.execute('INSERT INTO rounds VALUES(?,?,?,?,?)',(identity,str(purpose)[:160],now,min(now+seconds,self.deadline),'active'))
        return TestRound(self,identity)

    def snapshot(self,identity=None):
        where=' WHERE round_id=?' if identity else '';args=(identity,) if identity else ()
        count,charged,actual,unknown=self.db.execute('SELECT COUNT(*),COALESCE(SUM(charge),0),COALESCE(SUM(actual),0),COALESCE(SUM(CASE WHEN state!=\'settled\' THEN charge ELSE 0 END),0) FROM tickets'+where,args).fetchone()
        return {'requests_reserved':count,'conservative_cny':charged/NANO,'known_estimated_bill_cny':actual/NANO,
                'unconfirmed_reserved_cny':unknown/NANO,'rounds':self.db.execute('SELECT COUNT(*) FROM rounds').fetchone()[0],
                'price_source':PRICE_URL,'note':'已核算费用按已核对价格估算；用量不明请求按预留上界占用预算。'}

    def close(self):self.db.close()

class TestRound:
    def __init__(self,ledger,identity):
        self.ledger,self.id=ledger,identity
        self.started_mono=time.monotonic()
        self.seconds=max(0,self.ledger.db.execute('SELECT end FROM rounds WHERE id=?',(identity,)).fetchone()[0]-ledger.clock())

    def remaining(self):
        row=self.ledger.db.execute('SELECT end,state FROM rounds WHERE id=?',(self.id,)).fetchone()
        return max(0,min(row[0]-self.ledger.clock(),self.seconds-(time.monotonic()-self.started_mono))) if row and row[1]=='active' else 0

    def check(self):
        if self.remaining()<=0:raise BudgetStop('测试轮次已结束或达到时间/当日截止上限')

    def reserve(self,messages,max_output_tokens):
        self.check()
        if type(max_output_tokens) is not int or max_output_tokens<1:raise BudgetStop('输出 token 上限无效')
        # Byte-level upper bound + protocol margin; never treat the batching
        # heuristic as a tokenizer or assume an input-cache hit.
        inputs=len(json.dumps(messages,ensure_ascii=False).encode('utf-8'))+2048
        amount=inputs*2000+max_output_tokens*8000
        identity=uuid.uuid4().hex
        db=self.ledger.db
        with self.ledger.transaction():
            self.check()
            total=db.execute('SELECT COALESCE(SUM(charge),0) FROM tickets').fetchone()[0]
            current=db.execute('SELECT COALESCE(SUM(charge),0) FROM tickets WHERE round_id=?',(self.id,)).fetchone()[0]
            if current+amount>self.ledger.round_limit or total+amount>self.ledger.total_limit:
                reserved=db.execute("SELECT COALESCE(SUM(charge),0) FROM tickets WHERE round_id=? AND state='reserved'",(self.id,)).fetchone()[0]
                if reserved and current-reserved+amount<=self.ledger.round_limit and total-reserved+amount<=self.ledger.total_limit:
                    raise BudgetWait('等待当前在途费用完成核算')
                raise BudgetStop('费用预留不足：本轮最多 3 元、累计最多 15 元；停止新增请求')
            db.execute('INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?,?)',(identity,self.id,amount,amount,None,'reserved',inputs,max_output_tokens,'{}'))
        return identity

    def settle(self,ticket,usage):
        row=self.ledger.db.execute('SELECT reserved,input_bound,output_bound,state FROM tickets WHERE id=? AND round_id=?',(ticket,self.id)).fetchone()
        if not row or row[3]=='settled':return
        inputs=usage.get('prompt_tokens');outputs=usage.get('completion_tokens')
        if any(type(n) is not int or n<0 for n in (inputs,outputs)):
            self.ledger.db.execute("UPDATE tickets SET state='uncertain' WHERE id=?",(ticket,));return
        hits=usage.get('prompt_cache_hit_tokens',0)
        if type(hits) is not int or not 0<=hits<=inputs:hits=0
        charge=(inputs-hits)*2000+hits*40+outputs*8000
        # Oct 17, 2026 is Saturday: verified off-peak prices apply all day.
        actual=(inputs-hits)*1000+hits*20+outputs*4000
        safe_usage={key:value for key,value in usage.items() if type(value) is int and value>=0}
        with self.ledger.transaction():
            self.ledger.db.execute("UPDATE tickets SET charge=?,actual=?,state='settled',usage=? WHERE id=?",(charge,actual,json.dumps(safe_usage),ticket))
            if inputs>row[1] or outputs>row[2] or charge>row[0]:
                self.ledger.db.execute("UPDATE rounds SET state='provider_bound_breach' WHERE id=?",(self.id,))
        if inputs>row[1] or outputs>row[2] or charge>row[0]:
            raise BudgetStop('服务商用量超出预留假设，已停止本轮，必须核查计费记录')

    def finish(self):
        self.ledger.db.execute("UPDATE rounds SET state='finished' WHERE id=? AND state='active'",(self.id,))

    def snapshot(self):return self.ledger.snapshot(self.id)
