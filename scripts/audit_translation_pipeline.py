"""Read captured preparation spools; fake translations never leave this process."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from modlingo import __version__
from modlingo.batching import fragment_groups,fits
from modlingo.core import _unsupported_reason
from modlingo.extraction import Record
from modlingo.memory import target_parts,whole_text
from modlingo.provider import APIConfig,Translator,preset_config
from modlingo.syntax import Span,spans,rebuild,validate_rebuilt

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tasks',required=True)
    parser.add_argument('--output',default='artifacts')
    parser.add_argument('--provider',choices=['legacy','deepseek'],default='legacy')
    args=parser.parse_args()
    translator=Translator(preset_config('deepseek','deepseek-flash','') if args.provider=='deepseek' else APIConfig('http://127.0.0.1:1/v1','offline-structure-audit'))
    marker_cost=8 if args.provider=='deepseek' else 22
    records=fragments=groups=whole=0;failures=[];mods=0
    for path in sorted(Path(args.tasks).glob('*.jsonl')):
        mods+=1
        with path.open(encoding='utf-8') as stream:
            next(stream)
            for line in stream:
                row=json.loads(line)
                if 'record' not in row:continue
                record=row['record'];parts,notes=spans(record['source'],record['kind'])
                record={**record,'notes':notes}
                if not parts:continue
                records+=1;fragments+=len(parts);values={}
                try:
                    field=Record(**{**record,'parts':parts})
                    if whole_text(field) and fits(record['source'],4096,8192,marker_cost):
                        items=[('field',record['source'])]
                        _,prefix,inputs,restores=translator.prepare(items,{})
                        pieces=re.split('('+re.escape(prefix)+r'\d+_\d+__)',inputs['0']['text'])
                        answer=''.join(piece if piece.startswith(prefix) or not any(c.isalpha() for c in piece) else '中'+piece for piece in pieces)
                        results,errors=translator.decode(json.dumps({'0':answer}),items,prefix,inputs,restores,partial=True)
                        if errors:raise ValueError(next(iter(errors.values())))
                        if target_parts(field,results[0][2]) is None:raise ValueError('整字段无法安全拆回原片段')
                        whole+=1
                        continue
                    for chunk,marker,joined in fragment_groups([(str(i),p.text) for i,p in enumerate(parts)],4096,8192,marker_cost):
                        groups+=1
                        items=[('field',joined)]
                        _,prefix,inputs,restores=translator.prepare(items,{})
                        text=inputs['0']['text']
                        # Keep every generated protection token exactly. These
                        # are synthetic Chinese prefixes, not usable translations.
                        pieces=re.split('('+re.escape(prefix)+r'\d+_\d+__)',text)
                        answer=''.join(piece if piece.startswith(prefix) or not any(c.isalpha() for c in piece) else '中'+piece for piece in pieces)
                        results,errors=translator.decode(json.dumps({'0':answer}),items,prefix,inputs,restores,partial=True)
                        if errors:raise ValueError(next(iter(errors.values())))
                        target=results[0][2];decoded=[]
                        for i in range(1,len(chunk)):
                            piece,target=target.split('{{'+marker+'_'+str(i)+'}}',1);decoded.append(piece)
                        decoded.append(target)
                        values.update((key,value) for (key,_),value in zip(chunk,decoded))
                    target=rebuild(record['source'],parts,values)
                    if not validate_rebuilt(record['source'],target,record['kind']):raise ValueError('重建结构不匹配')
                except ValueError as exc:
                    failures.append({'spool':path.name,'context':record['context'],'reason':str(exc)})
    report={'version':__version__,'mode':'real preparation spools, synthetic output; no translation quality claim',
            'mods':mods,'records':records,'fragments':fragments,'joined_groups':groups,'whole_fields':whole,'failures':failures,
            'provider':args.provider,'paid_api_calls':0,'game_files_written':0,'passed':not failures}
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    (output/('translation-audit-'+__version__+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    return bool(failures)

if __name__=='__main__':raise SystemExit(main())
