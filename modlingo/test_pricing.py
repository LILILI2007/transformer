"""Unauthenticated official-page price check, fail closed on price changes."""
from html.parser import HTMLParser
import hashlib
import re
import time
import httpx
from .test_budget import PRICE_URL,BudgetStop

class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.table=None;self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.table=[]
        elif tag=='tr' and self.table is not None:self.row=[]
        elif tag in ('th','td') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('th','td') and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        elif tag=='tr' and self.row is not None:
            self.table.append(self.row);self.row=None
        elif tag=='table' and self.table is not None:
            self.tables.append(self.table);self.table=None

def parse_prices(html):
    parser=Tables();parser.feed(html)
    for table in parser.tables:
        if not any(any('deepseek-flash' in cell for cell in row) for row in table):continue
        values=[]
        for row in table:
            money=[cell for cell in row if re.fullmatch(r'\d+(?:\.\d+)?\s*元',cell)]
            if money:values.append(re.sub(r'\s','',money[0]))
        if values!=['0.02元','0.04元','1元','2元','4元','8元']:
            raise BudgetStop('官方 Flash 价格与已实现费率不符，停止付费测试并更新费率')
        return {'source':PRICE_URL,'verified_at':time.time(),'html_sha256':hashlib.sha256(html.encode()).hexdigest(),
                'peak_per_million_cny':{'cache_hit':0.04,'input':2,'output':8},
                'off_peak_per_million_cny':{'cache_hit':0.02,'input':1,'output':4}}
    raise BudgetStop('未能解析官方人民币价格表，未发送付费请求')

def verify_prices():
    try:
        response=httpx.get(PRICE_URL,timeout=20,follow_redirects=True)
        response.raise_for_status()
        if response.url.host!='api-docs.deepseek.com':raise BudgetStop('价格页面重定向至非官方站点')
        return parse_prices(response.text)
    except httpx.HTTPError:raise BudgetStop('官方价格核对失败，未发送付费请求') from None
