"""Explicit human-authored display aliases, not API translation results."""
from functools import lru_cache
import json
from pathlib import Path
import sys

@lru_cache(maxsize=1)
def aliases():
    path=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'bridge/package/names.zh.json'
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def display_alias(mod_id,source):
    record=aliases().get(mod_id,{})
    target=record.get('target','') if record.get('source')==source else ''
    return target+'（'+source+'）' if target and source not in target else target
