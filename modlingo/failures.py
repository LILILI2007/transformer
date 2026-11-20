"""Typed, serializable translation failures and independent circuit scopes."""
from collections import deque
from .core import ProjectError


class Reason(str):
    def __new__(cls, text, code='unknown'):
        value = super().__new__(cls, text)
        value.code = code
        return value


class TranslationError(ProjectError):
    def __init__(self, code, text):
        self.code = code
        super().__init__(text)


def reason(error, default='unknown'):
    return Reason(str(error), getattr(error, 'code', default))


CONTENT_CODES = frozenset(('unchanged', 'alias_required', 'structure', 'response_format',
                           'output_limit', 'refusal', 'tool_call', 'incomplete'))
SYSTEMIC_CODES = frozenset(('structure', 'response_format', 'output_limit', 'incomplete'))


class Circuits:
    def __init__(self):
        self.streaks = {}
        self.paused = set()
        self.recent = deque(maxlen=6)

    def finish(self, scope, good, codes):
        content_failure = bool(codes) and all(code in CONTENT_CODES for code in codes)
        self.streaks[scope] = self.streaks.get(scope, 0) + 1 if not good and content_failure else 0
        if self.streaks[scope] >= 4:
            self.paused.add(scope)
        self.recent.append((scope[0], bool(codes) and not good and all(c in SYSTEMIC_CODES for c in codes)))
        return len(self.recent)==6 and len({mod for mod, _ in self.recent}) >= 3 and sum(bad for _, bad in self.recent) >= 4
