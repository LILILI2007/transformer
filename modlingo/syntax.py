"""Lossless text spans: script bytes outside known text never change."""
from dataclasses import dataclass
import json
import re
from .core import TOKEN, MASK_TOKEN, ProjectError, _unsupported_reason, validate_translation

PARSER_VERSION = 'spans-2'

# Known non-text commands are retained byte for byte. Other commands remain
# untouched too, but are explicitly reported instead of claiming full coverage.
NON_TEXT_COMMANDS = set(('pause end viewport farmer music playSound stopMusic stopSound move faceDirection '
    'animate stopAnimation sprite setTile removeTile addObject removeObject addTemporaryActor '
    'addConversationTopic friendship emote shake fadeScreen globalFade changeLocation '
    'positionOffset jump speed showFrame hideObject showObject addMail mailReceived '
    'addItem removeItem addQuest removeQuest completeQuest money changeToTemporaryMap '
    'changeSprite dialogueAboveHead halt allActors lockViewport cutscene hideShadow '
    'skippable skip fork switchEvent actorControl warp fade out setScene').split())

@dataclass
class Span:
    start: int
    end: int
    text: str
    quoted: bool = False
    role: str = 'text'

def _commands(source, separator='/'):
    start, quoted, escaped = 0, False, False
    for i, c in enumerate(source):
        if escaped:
            escaped = False
        elif c == '\\' and quoted:
            escaped = True
        elif c == '"':
            quoted = not quoted
        elif c == separator and not quoted:
            yield start, i
            start = i + 1
    if quoted:
        raise ProjectError('事件引号未闭合')
    yield start, len(source)

def event_header_end(source):
    """Recognize only a complete literal music/viewport/actor header."""
    fields=source.split('/',3)
    if len(fields)!=4:return 0
    music,viewport,actors=fields[:3]
    if not re.fullmatch(r'\s*[A-Za-z0-9_.-]+\s*',music):return 0
    if music.strip() in (NON_TEXT_COMMANDS|{'speak','message','question','quickQuestion','splitSpeak','textAboveHead'})-{'continue'}:return 0
    if not re.fullmatch(r'\s*-?\d+\s+-?\d+\s*',viewport):return 0
    values=actors.split()
    if not values or len(values)%4:return 0
    for i in range(0,len(values),4):
        name,x,y,direction=values[i:i+4]
        if not re.fullmatch(r'[A-Za-z0-9_.?+-]+',name):return 0
        if not re.fullmatch(r'-?\d+',x) or not re.fullmatch(r'-?\d+',y) or direction not in ('0','1','2','3'):return 0
    return len(music)+len(viewport)+len(actors)+3

def spans(source, kind='text', _separator='/', _depth=0):
    """Return nonoverlapping spans and explicit unsupported reasons."""
    if len(source) > 200000:
        return [], ['文本超过解析大小限制']
    if '%item' in source and not re.search(r'%item\b.*?%%',source,re.S):
        return [], ['邮件附件未闭合，原文保留']
    if kind in ('quest', 'gift'):
        ranges = list(_commands(source))
        indexes = (1,2,3) if kind == 'quest' else (0,2,4,6,8)
        result, notes = [], []
        for n in indexes:
            if n >= len(ranges):
                continue
            a,b = ranges[n]
            inner, issues = spans(source[a:b])
            result.extend(Span(a+p.start,a+p.end,p.text,p.quoted,p.role) for p in inner)
            notes.extend(issues)
        return result, list(dict.fromkeys(notes))
    if kind == 'event':
        result, notes = [], []
        header_end=event_header_end(source) if _separator=='/' and _depth==0 else 0
        try:
            for start, end in _commands(source, _separator):
                if start<header_end:continue
                command = source[start:end]
                name = command.strip().split(' ', 1)[0]
                if name=='end' and re.match(r'\s*end\s+dialogue(?:WarpOut)?(?:\s|$)',command):
                    closing=re.fullmatch(r'\s*end\s+(dialogue|dialogueWarpOut)\s+([^\s"/\\]+)\s+"((?:\\.|[^"\\])*)"\s*',command)
                    if closing is None:
                        notes.append('end dialogue 参数格式不支持，原文保留')
                        continue
                    try:text=json.loads('"'+closing.group(3)+'"')
                    except ValueError:
                        notes.append('end dialogue 文本转义不支持，原文保留')
                        continue
                    result.append(Span(start+closing.start(3),start+closing.end(3),text,True,'end-dialogue'))
                    continue
                if name == 'textAboveHead':
                    bubble=re.fullmatch(r'\s*textAboveHead\s+([^\s"/\\]+)\s+"((?:\\.|[^"\\])*)"\s*',command)
                    if bubble is None:
                        notes.append('textAboveHead 参数格式不支持，原文保留')
                        continue
                    try:text=json.loads('"'+bubble.group(2)+'"')
                    except ValueError:
                        notes.append('textAboveHead 文本转义不支持，原文保留')
                        continue
                    result.append(Span(start+bubble.start(2),start+bubble.end(2),text,True,'bubble'))
                    continue
                if name == 'quickQuestion':
                    # The game XML defines unquoted # choices followed by
                    # (break) branches, whose commands use backslash separators.
                    # Never treat the final quoted branch dialogue as the header.
                    head=re.match(r'\s*quickQuestion\s+',command)
                    blocks=command[head.end():].split('(break)') if head else []
                    if not blocks or any(c in blocks[0] for c in ('"','\\','/')):
                        notes.append('quickQuestion 问题格式不支持，整条命令保留原文')
                        continue
                    answers=blocks[0].count('#')
                    if not answers or len(blocks)-1!=answers or any(not branch.strip() for branch in blocks[1:]):
                        notes.append('quickQuestion 选项与分支数量不匹配，整条命令保留原文')
                        continue
                    if _depth>=3:
                        notes.append('quickQuestion 嵌套超出安全范围，原文保留')
                        continue
                    inner,issues=spans(blocks[0])
                    result.extend(Span(start+head.end()+p.start,start+head.end()+p.end,p.text,p.quoted,p.role) for p in inner)
                    notes.extend(issues)
                    offset=head.end()+len(blocks[0])+len('(break)')
                    for branch in blocks[1:]:
                        inner,issues=spans(branch,'event','\\',_depth+1)
                        result.extend(Span(start+offset+p.start,start+offset+p.end,p.text,p.quoted,p.role) for p in inner)
                        notes.extend(issues)
                        offset+=len(branch)+len('(break)')
                    continue
                if name not in ('speak', 'message', 'question', 'quickQuestion', 'splitSpeak'):
                    if name and name not in NON_TEXT_COMMANDS and not name.isdigit():
                        notes.append('未处理事件命令，原样保留：' + name)
                    continue
                quoted = list(re.finditer(r'"((?:\\.|[^"\\])*)"', command))
                if not quoted:
                    notes.append('事件文本命令缺少可识别引号：' + name)
                    continue
                # Only the final quoted argument is human text, never NPC IDs.
                match = quoted[-1]
                raw = match.group(1)
                try:
                    text = json.loads('"' + raw + '"')
                except ValueError:
                    notes.append('事件文本转义不支持')
                    continue
                result.append(Span(start + match.start(1), start + match.end(1), text, True))
        except ProjectError as exc:
            notes.append(str(exc))
        return result, notes
    protected = [(m.start(), m.end()) for m in MASK_TOKEN.finditer(source)]
    # Dialogue question/response headers contain IDs and branch logic.
    for pattern in (r'\$(?:q|r|p|c|d|t)\b[^#\r\n]*#', r'%item\b.*?%%', r'#(?:attachment|action|reply|conversation|item|money|recipe|quest|autoOpen|title|image)\b[^\r\n]*?(?=\[#\]|$)', r'\[#\]', r'\$\{|\}\$'):
        protected.extend((m.start(), m.end()) for m in re.finditer(pattern, source, re.S))
    covered = [False] * len(source)
    for a, b in protected:
        covered[a:b] = [True] * (b-a)
    result, notes, i = [], [], 0
    while i < len(source):
        if covered[i]:
            i += 1
            continue
        end = i + 1
        while end < len(source) and not covered[end]:
            end += 1
        text = source[i:end]
        if _unsupported_reason(text):
            notes.append('未知嵌套或控制表达式保留原文')
        elif any(c.isalpha() for c in text):
            result.append(Span(i, end, text))
        i = end
    return result, list(dict.fromkeys(notes))

def has_quick_question(source):
    return bool(re.search(r'(?:^|[/\\])\s*quickQuestion\b',source))

def has_bubble_text(source):
    return bool(re.search(r'(?:^|[/\\])\s*textAboveHead\b',source))

def has_end_dialogue(source):
    return bool(re.search(r'(?:^|[/\\])\s*end\s+dialogue(?:WarpOut)?(?:\s|$)',source))

def has_incremental_event_text(source):
    return has_quick_question(source) or has_bubble_text(source) or has_end_dialogue(source)

def parser_version(source):
    if has_end_dialogue(source):return 'spans-5-end-dialogue'
    if has_bubble_text(source):return 'spans-4-bubble'
    return 'spans-3-quickquestion' if has_quick_question(source) else PARSER_VERSION

def kind_for(source):
    if has_incremental_event_text(source):return 'event'
    return 'event' if '/' in source and re.search(r'(?:^|/)\s*(?:(?:speak|message|pause|viewport)\b|farmer\s+-?\d)', source) else 'text'

def rebuild(source, fragments, translations):
    result, cursor = [], 0
    for index, fragment in enumerate(fragments):
        target = translations.get(str(index), fragment.text)
        if validate_translation(fragment.text, target):
            raise ProjectError('片段译文结构无效')
        result.append(source[cursor:fragment.start])
        result.append(json.dumps(target, ensure_ascii=False)[1:-1] if fragment.quoted else target)
        cursor = fragment.end
    result.append(source[cursor:])
    return ''.join(result)

def validate_rebuilt(source, target, kind='text'):
    before, notes = spans(source, kind)
    after, after_notes = spans(target, kind)
    if notes != after_notes or len(before) != len(after):
        return False
    # Replace human spans with identical sentinels and compare all other bytes.
    def skeleton(text, parts):
        out, cursor = [], 0
        for part in parts:
            out.extend((text[cursor:part.start], '<TEXT>'))
            cursor = part.end
        return ''.join(out) + text[cursor:]
    return skeleton(source, before) == skeleton(target, after)
