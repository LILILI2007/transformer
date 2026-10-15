"""Opt-in paid live check. Never used by CI; reads credentials only from env."""
import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modlingo.batch import data_root
from modlingo.core import ProjectError, atomic_write
from modlingo.provider import PRESETS, Translator, preset_config


def main(argv=None):
    parser = argparse.ArgumentParser(description='自愿运行的真实 API 检查，会发送三条原创短文本并可能计费。')
    parser.add_argument('--provider', choices=[p for p in PRESETS if p != 'custom'], required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--key-env', default='MODLINGO_API_KEY')
    parser.add_argument('--base-url')
    parser.add_argument('--workspace-id', default='')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--max-output-tokens', type=int, default=8192)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--allow-paid', action='store_true', help='明确允许这次真实请求及可能的重试费用')
    args = parser.parse_args(argv)
    if not args.allow_paid:
        parser.error('没有 --allow-paid，不发送网络请求。协议测试请运行 unittest。')
    key = os.environ.get(args.key_env, '')
    config = preset_config(args.provider, args.model, key)
    if args.base_url:
        config.base_url = args.base_url
    config.workspace_id, config.timeout = args.workspace_id, args.timeout
    config.max_output_tokens = args.max_output_tokens
    report = {'verification': 'live', 'provider': args.provider, 'protocol': config.protocol,
              'model': args.model, 'time_utc': datetime.now(timezone.utc).isoformat(),
              'success': False, 'usage': {}, 'reason': ''}
    translator = Translator(config)
    try:
        config.endpoint()
        report['host'] = urlparse(config.base_url).hostname
        items = [('greeting', 'Hello {{PlayerName}}!$h'),
                 ('lines', 'Good morning.\nGood night.'), ('farm', 'Your %farm farm, @.')]
        outputs = translator.translate(items, {})
        report['success'] = all(re.search(r'[\u3400-\u9fff]', target) for _, _, target in outputs)
        report['usage'] = translator.last_usage
        report['reason'] = '三条响应及标记检查通过，含中文；不是人工语义评审' if report['success'] else '返回结构有效，但部分测试译文未含中文，需人工核对'
    except ProjectError as exc:
        report['reason'] = str(exc)
    except Exception:
        report['reason'] = '检查异常；未保存原始异常、响应正文或凭据'
    output = args.output or data_root() / 'api-checks' / (args.provider + '-' + uuid.uuid4().hex[:8] + '.json')
    atomic_write(output, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
    print('Live API check: ' + ('passed' if report['success'] else 'failed') + '; report: ' + str(output))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
