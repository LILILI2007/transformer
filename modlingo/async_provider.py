"""Pooled, cancellable transport; one budget includes HTTP and format retries."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import time
import uuid
import httpx
from .core import ProjectError
from .provider import Translator, ModelResponseError, _strict_pairs
from .batching import wire_batches, fits, output_cost
from .adaptive import Adaptive,size_class
from .test_budget import BudgetStop,BudgetWait
from .usage_limits import UsageBudget, UsageLimit, UsageWait
from .failures import TranslationError, Reason, reason as failure_reason

class ServiceError(ProjectError):
    def __init__(self, status, text):
        self.status = status
        self.code = "service_config"
        super().__init__(text)

class Budget:
    def __init__(self, limit=8):
        self.remaining = limit
        self.id=uuid.uuid4().hex[:12]
    def consume(self):
        if self.remaining <= 0:
            raise TranslationError('budget','此批次已达到 8 次请求上限，未完成条目保留待重试')
        self.remaining -= 1

class Session:
    def __init__(self, config, concurrency=4, on_stats=lambda stats: None, transport=None, on_trace=lambda row:None, test_budget=None):
        if type(concurrency) is not int or not 1 <= concurrency <= 32:
            raise ProjectError('并发必须为 1–32')
        if test_budget is not None and transport is None:
            if config.provider!='deepseek' or config.model!='deepseek-flash' or not config.disable_thinking or config.endpoint() not in ('https://api.deepseek.com/chat/completions','https://api.deepseek.com/v1/chat/completions'):
                raise BudgetStop('限额测试只允许官方 DeepSeek Flash 非思考接口')
        self.test_budget=test_budget
        self.usage_budget=UsageBudget(config)
        self.config, self.limit = config, concurrency
        self.maximum = concurrency
        self.condition = asyncio.Condition()
        self.active = self.requests = self.waiting = 0
        self.cost_waiters=0
        self.network_active=0
        self.last_response = None
        self.cooldown = 0
        self.on_stats = on_stats
        self.transport = transport
        self.contexts = {}
        self.batch_items=20
        self.marker_cost=8 if config.provider=='deepseek' else 22
        self.batch_chars=min(6000,max(256,config.max_output_tokens//2))
        self.on_trace=on_trace
        self.adaptive=Adaptive(on_trace)

    def profile_key(self,items):
        context=self.contexts.get(items[0][0],'') if items else ''
        return context.split(' | ',1)[0] or 'default',size_class(max(items,key=lambda item:output_cost(item[1]))[1]) if items else 'short'

    def limit_for(self,items):return self.adaptive.get(self.profile_key(items)).limit

    def shrink(self,count,reason,scope=None,batch=''):
        self.batch_items=self.adaptive.shrink(scope or ('default','short'),count,reason,batch)

    async def __aenter__(self):
        self.client = httpx.AsyncClient(follow_redirects=False, transport=self.transport,
            limits=httpx.Limits(max_connections=max(6,self.maximum), max_keepalive_connections=max(6,self.maximum)),
            timeout=httpx.Timeout(self.config.timeout, connect=min(10, self.config.timeout), write=min(15, self.config.timeout), pool=10))
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    def stats(self):
        self.on_stats({'in_flight': self.network_active, 'requests': self.requests, 'waiting_retry': self.waiting,'waiting_budget':self.cost_waiters,
                       'concurrency': self.limit, 'last_response': self.last_response,
                       'usage_budget': self.usage_budget.snapshot()})

    async def wait(self, seconds):
        self.waiting += 1
        self.stats()
        try:
            if self.test_budget:
                self.test_budget.check();seconds=min(seconds,self.test_budget.remaining())
            await asyncio.sleep(seconds)
            if self.test_budget:self.test_budget.check()
        finally:
            self.waiting -= 1
            self.stats()

    async def post(self, translator, messages, budget, on_dispatch=lambda:None):
        try:
            endpoint, body, headers = translator.request_parts(messages)
        except ProjectError as exc:
            raise ServiceError(0,str(exc)) from exc
        for attempt in range(3):
            while True:
                if self.test_budget:self.test_budget.check()
                delay = self.cooldown - time.monotonic()
                if delay > 0:
                    await self.wait(delay)
                async with self.condition:
                    if self.test_budget:
                        try:await asyncio.wait_for(self.condition.wait_for(lambda: self.active < self.limit),self.test_budget.remaining())
                        except asyncio.TimeoutError:raise BudgetStop('等待连接期间达到测试时间上限') from None
                    else:await self.condition.wait_for(lambda: self.active < self.limit)
                    # A request can report 429 while this one is waiting for a
                    # slot. Recheck the global cooldown before claiming it.
                    if self.cooldown > time.monotonic():
                        continue
                    self.active += 1
                    break
            status, retry_after = None, None
            started=time.monotonic();error='';raw=b'';finish=None;request_number=None;ticket=None;usage_ticket=None;cost_wait_ms=0
            translator.last_usage={}
            try:
                if budget.remaining<=0:budget.consume()
                while usage_ticket is None:
                    try:usage_ticket=self.usage_budget.reserve(body,self.config.max_output_tokens)
                    except UsageWait:await asyncio.sleep(.05)
                if self.test_budget:
                    try:ticket=self.test_budget.reserve(messages,self.config.max_output_tokens)
                    except BudgetWait:
                        self.cost_waiters+=1;self.stats();cost_started=time.monotonic()
                        try:
                            while ticket is None:
                                await asyncio.sleep(min(.05,self.test_budget.remaining()))
                                self.test_budget.check()
                                try:ticket=self.test_budget.reserve(messages,self.config.max_output_tokens)
                                except BudgetWait:pass
                        finally:
                            cost_wait_ms=round((time.monotonic()-cost_started)*1000)
                            self.cost_waiters-=1;self.stats()
                budget.consume();started=time.monotonic()
                self.requests += 1
                request_number=self.requests
                self.network_active+=1
                on_dispatch()
                self.stats()
                async def receive():
                    async with self.client.stream('POST', endpoint, json=body, headers=headers) as response:
                        if 300 <= response.status_code < 400:
                            raise ServiceError(response.status_code,'服务返回重定向，已拒绝转发密钥')
                        if response.status_code >= 400:
                            if response.status_code in (429,529):
                                self.limit = max(1,self.limit//2)
                                delay = self.retry_delay(response.headers.get('Retry-After'), attempt)
                                self.cooldown = max(self.cooldown, time.monotonic() + delay)
                            return response.status_code, response.headers.get('Retry-After'), b''
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 2 * 1024 * 1024:
                                raise ProjectError('API 响应超过 2 MB')
                        return response.status_code, None, bytes(raw)
                request_timeout=min(self.config.timeout,self.test_budget.remaining()) if self.test_budget else self.config.timeout
                status, retry_after, raw = await asyncio.wait_for(receive(), timeout=request_timeout)
                self.last_response = time.time()
                if status < 400:
                    try:
                        payload=json.loads(raw.decode('utf-8'), object_pairs_hook=_strict_pairs)
                        if isinstance(payload,dict):
                            choices=payload.get('choices')
                            finish=(choices[0].get('finish_reason') if isinstance(choices,list) and choices and isinstance(choices[0],dict) else payload.get('stop_reason',payload.get('status')))
                        return translator.parse_response(payload)
                    except ProjectError:
                        raise
                    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                        raise TranslationError('response_format','API 响应格式不兼容') from None
                if status in (400, 401, 402, 403, 404):
                    raise ServiceError(status, 'API 请求失败（%d）：请检查协议、密钥、模型与访问权限' % status)
                if status not in (408, 429, 500, 502, 503, 504, 529):
                    raise ProjectError('API 请求失败（%d），错误正文已隐藏' % status)
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                error='network_or_timeout'
                # Windows timers can wake just before the wall-clock deadline.
                # A timeout bounded by the remaining authorization is terminal;
                # checking the clock alone could dispatch a second paid retry.
                if isinstance(exc,asyncio.TimeoutError) and self.test_budget and request_timeout < self.config.timeout:
                    raise BudgetStop('请求已达到测试剩余时间上限，停止重试') from None
                if self.test_budget:self.test_budget.check()
                if attempt == 2:
                    raise TranslationError('connection','连接失败或请求总时限已到，请检查网络后重试') from None
            except ProjectError as exc:
                error=getattr(exc,'code','request_or_response_validation')
                raise
            finally:
                settlement_error=None
                if usage_ticket is not None:
                    self.usage_budget.settle(usage_ticket,translator.last_usage)
                if ticket:
                    try:self.test_budget.settle(ticket,translator.last_usage)
                    except BudgetStop as exc:settlement_error=exc
                if request_number is not None:
                    allowed=('stop','length','content_filter','tool_calls','function_call','end_turn','max_tokens','tool_use','stop_sequence','completed','incomplete','failed')
                    self.on_trace({'event':'http','request':request_number,'protocol':self.config.protocol,
                        'batch':budget.id,'mod_id':getattr(translator,'trace_mod',''),
                        'provider':self.config.provider,'thinking_disabled':self.config.disable_thinking,
                        'items':len(json.loads(messages[-1]['content']).get('items',{})) if messages else 0,
                        'attempt':attempt+1,'budget_remaining':budget.remaining,'status':status,
                        'elapsed_ms':round((time.monotonic()-started)*1000),'response_bytes':len(raw),
                        'reservation_wait_ms':cost_wait_ms,
                        'finish':finish if finish in allowed else 'unknown','error':error,
                        'usage':dict(translator.last_usage)})
                if request_number is not None:self.network_active-=1
                async with self.condition:
                    self.active -= 1
                    self.condition.notify_all()
                self.stats()
                if settlement_error:raise settlement_error
            if attempt == 2:
                raise TranslationError('rate_limit' if status in (429,529) else 'connection','服务繁忙或限流，已达到本次 HTTP 重试上限')
            delay = 0.5 * 2 ** attempt
            if status in (429, 529):
                delay = max(0, self.cooldown-time.monotonic())
            await self.wait(delay)

    @staticmethod
    def retry_delay(value, attempt):
        delay = 0.5 * 2 ** attempt
        try:
            delay = max(delay, float(value))
        except (TypeError, ValueError):
            if value:
                try:
                    delay = max(delay, (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return min(300, max(0.5, delay))

async def translate_recover(session, items, glossary, on_result, on_error, budget=None, split_item=None, on_attempt=lambda items:None, repair_reason=None):
    budget = budget or Budget()
    from collections import deque, Counter
    scope=session.profile_key(items)
    queue=deque((batch,0) for batch in wire_batches(items,session.limit_for(items),session.batch_chars,session.config.max_output_tokens,session.marker_cost))
    while queue:
        current,depth=queue.popleft()
        oversized=[item for item in current if not fits(item[1],session.batch_chars,session.config.max_output_tokens,session.marker_cost)]
        if oversized:
            on_error(oversized,'单个文字片段超过当前安全输出预算；未发送 API 请求，请提高输出上限或单独处理')
            current=[item for item in current if item not in oversized]
        if not current:continue
        if not budget.remaining:
            on_error(current,'此批次已达到 8 次请求上限，未完成条目保留待重试')
            for rest,_ in queue:on_error(rest,'此批次已达到 8 次请求上限，未完成条目保留待重试')
            break
        translator=Translator(session.config);translator.contexts=session.contexts
        translator.trace_mod=session.contexts.get(current[0][0],'').split(' | ',1)[0][:200]
        generation=session.adaptive.get(scope).generation
        try:
            messages,prefix,records,restores=translator.prepare(current,glossary)
            if depth:
                messages[0]["content"] += " 上次输出未通过校验。这次逐项翻译，英文名称需要中文显示；严格保留全部标记和条目 ID。"
            response=await session.post(translator,messages,budget,on_dispatch=lambda:on_attempt(current))
            results,errors=translator.decode(response.strip(),current,prefix,records,restores,partial=True)
            repairs=on_result(results,translator.last_usage) if results else None
            repairs=repairs if isinstance(repairs,list) else []
            if repairs:
                if budget.remaining and depth<1:queue.append((repairs,depth+1))
                else:
                    for item in repairs:
                        on_error([item],repair_reason(item) if repair_reason else Reason('字段修复仍未通过，保留原文待处理','structure'))
            if not errors:
                if not repairs:session.batch_items=session.adaptive.success(scope,len(current),generation,budget.id)
                continue
            session.on_trace({'event':'decode','batch':budget.id,'mod_id':scope[0],'scope':list(scope),'valid_items':len(results),'failed_items':len(errors),'reasons':dict(Counter(errors.values())),
                              'marker_diagnostics':getattr(translator,'last_decode_diagnostics',{})})
            failed=[item for item in current if item[0] in errors]
            terminal=[item for item in failed if getattr(errors[item[0]],'code','unknown')=='alias_required' or depth>=1]
            for item in terminal:on_error([item],errors[item[0]])
            failed=[item for item in failed if item not in terminal]
            if split_item and budget.remaining:
                unsplit=[]
                for item in failed:
                    smaller=split_item(item,errors[item[0]])
                    if smaller:
                        session.on_trace({'event':'field_split','batch':budget.id,'budget_remaining':budget.remaining,'children':len(smaller)})
                        queue.append((smaller,depth+1))
                    else:unsplit.append(item)
                failed=unsplit
            if not failed:continue
            if budget.remaining and (len(current)>1 or depth<1):
                if len(failed)<len(current):queue.append((failed,depth+1))
                elif len(failed)==1:queue.append((failed,depth+1))
                else:
                    session.shrink(len(current),'逐条校验失败',scope,budget.id)
                    middle=len(failed)//2
                    queue.extend(((failed[:middle],depth+1),(failed[middle:],depth+1)))
            else:
                for reason in set(errors.values()):
                    on_error([item for item in failed if errors[item[0]]==reason],reason)
        except (ServiceError,BudgetStop,UsageLimit):
            raise
        except ProjectError as exc:
            session.on_trace({'event':'decode_error','batch':budget.id,'mod_id':scope[0],'scope':list(scope),'code':getattr(exc,'code','validation'),
                'reason':str(exc),'items':len(current),'budget_remaining':budget.remaining})
            failure=failure_reason(exc,'response_format')
            repairable=depth<1 and failure.code in ('output_limit','response_format','structure')
            if repairable and budget.remaining and len(current)>1:
                session.shrink(len(current),getattr(exc,'code','响应格式不兼容'),scope,budget.id)
                size=max(1,(len(current)+3)//4) if failure.code=='output_limit' else max(1,len(current)//2)
                queue.extend((current[n:n+size],depth+1) for n in range(0,len(current),size))
            elif repairable and budget.remaining and split_item and len(current)==1:
                smaller=split_item(current[0],failure)
                if smaller:
                    session.on_trace({'event':'field_split','batch':budget.id,'budget_remaining':budget.remaining,'children':len(smaller)})
                    queue.append((smaller,depth+1))
                else:on_error(current,failure)
            else:on_error(current,failure)
