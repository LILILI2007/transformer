"""Public issue summaries built from a whitelist, never copied raw logs."""
import math
import platform
from . import __version__
from .resource_rules import HASH, VERSION


def public_summary(report):
    def numbers(source, keys):
        return {key: source[key] for key in keys if type(source.get(key)) in (int, float)
                and math.isfinite(source[key]) and source[key] >= 0}
    metrics = report.get('metrics', {})
    usage = metrics.get('usage_budget', {})
    return {
        'schema_version': 1, 'tool_version': __version__,
        'os': platform.system(), 'python_version': platform.python_version(),
        'rules': {'version': VERSION, 'sha256': HASH},
        'counts': numbers(report.get('counts', {}), ('成功','部分完成','失败','文件异常','需进游戏采集','无需翻译','仅其他语言','待确认','已暂停')),
        'metrics': numbers(metrics, ('total','completed','failed','cache_hits','requests','dependency_hits','waiting_dependencies')),
        'usage': numbers(usage, ('request_limit','token_limit','requests','charged_or_reserved_tokens',
                                'unknown_requests','provider_overruns','estimated_cny')),
        'paused': bool(report.get('paused_reason')),
        'includes_paths_text_keys_credentials': False,
        'game_display_verified': False,
    }
