"""Per-run limits shared by all batches, retries and concurrent requests.

Input reservations use wire UTF-8 bytes plus protocol overhead, not a provider
tokenizer. Unknown responses retain reservations. These are local guardrails,
not a guarantee about the provider's invoice or unrelated account activity.
"""
import json
import math
import threading
from .core import ProjectError


class UsageLimit(ProjectError):
    code = 'usage_limit'


class UsageWait(ProjectError):
    pass


def validate_limits(config):
    for value, maximum, label in (
        (config.run_request_limit, 100000, '本轮请求上限'),
        (config.run_token_limit, 1000000000, '本轮 token 预算'),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ProjectError('%s须为 1–%d 的整数' % (label, maximum))
    for price in (config.input_price_cny, config.output_price_cny):
        if price is not None and (type(price) not in (int, float) or not math.isfinite(price) or price < 0 or price > 1000000):
            raise ProjectError('单价须为有限非负数字，留空表示未知')


def limit_summary(config):
    validate_limits(config)
    text = '本轮最多 %d 次请求，token 预算 %s（输入保守估算，含重试）。' % (
        config.run_request_limit, format(config.run_token_limit, ','))
    if config.input_price_cny is None or config.output_price_cny is None:
        return text + '\n未填完整单价，无法估算人民币费用；请在服务商控制台设置额度。'
    estimate = config.run_token_limit * max(config.input_price_cny, config.output_price_cny) / 1000000
    return text + '\n按填写单价计算的预算金额至多约 %.4f 元；不是账单或人民币硬限额。' % estimate


class UsageBudget:
    def __init__(self, config):
        validate_limits(config)
        self.request_limit, self.token_limit = config.run_request_limit, config.run_token_limit
        self.input_price, self.output_price = config.input_price_cny, config.output_price_cny
        self.requests = self.tokens = self.input_tokens = self.output_tokens = 0
        self.unknown_requests = self.overruns = 0
        self.active = {}
        self.lock = threading.RLock()
        self.stopped = ''

    def reserve(self, body, output_limit):
        input_bound = len(json.dumps(body, ensure_ascii=False).encode('utf-8')) + 2048
        amount = input_bound + output_limit
        with self.lock:
            if self.stopped:
                raise UsageLimit(self.stopped)
            if self.requests >= self.request_limit:
                self.stopped = '达到本轮请求上限；已保存结果，剩余条目待下轮处理'
                raise UsageLimit(self.stopped)
            if self.tokens + amount > self.token_limit:
                if self.active:
                    raise UsageWait('等待已发送请求结算 token 预算')
                self.stopped = '本轮剩余 token 预算不足以预留下一请求；未发送，已有结果保留'
                raise UsageLimit(self.stopped)
            self.requests += 1
            self.tokens += amount
            self.input_tokens += input_bound
            self.output_tokens += output_limit
            ticket = self.requests
            self.active[ticket] = (input_bound, output_limit)
            return ticket

    def settle(self, ticket, usage):
        with self.lock:
            reserved_input, reserved_output = self.active.pop(ticket)
            actual_input = usage.get('input_tokens', usage.get('prompt_tokens'))
            actual_output = usage.get('output_tokens', usage.get('completion_tokens'))
            if any(type(value) is not int or value < 0 for value in (actual_input, actual_output)):
                self.unknown_requests += 1
                return
            # Claude reports cache reads/writes separately from input_tokens.
            actual_input += sum(usage.get(key, 0) for key in ('cache_read_input_tokens', 'cache_creation_input_tokens'))
            actual_total = max(actual_input + actual_output, usage.get('total_tokens', 0))
            self.tokens += actual_total - reserved_input - reserved_output
            self.input_tokens += actual_input - reserved_input
            self.output_tokens += actual_output - reserved_output
            if actual_total > actual_input + actual_output:
                self.input_tokens += actual_total - actual_input - actual_output
            if actual_input > reserved_input or actual_output > reserved_output or actual_total > reserved_input + reserved_output:
                self.overruns += 1
                self.stopped = '服务返回用量超过请求预留；已停止新请求，请核对模型限制与服务商账单'

    def snapshot(self):
        with self.lock:
            cost = None if self.input_price is None or self.output_price is None else (
                self.input_tokens * self.input_price + self.output_tokens * self.output_price) / 1000000
            return {'request_limit': self.request_limit, 'token_limit': self.token_limit,
                    'requests': self.requests, 'charged_or_reserved_tokens': self.tokens,
                    'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens,
                    'active_reservations': len(self.active), 'unknown_requests': self.unknown_requests,
                    'provider_overruns': self.overruns, 'estimated_cny': cost,
                    'stopped_reason': self.stopped, 'scope': 'one_run',
                    'token_method': 'provider_usage_or_utf8_reservation', 'invoice_guaranteed': False}
