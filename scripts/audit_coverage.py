"""Read-only inventory; unknown text and game verification stay explicit."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from modlingo.workers import scan_worker

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--game-path',required=True)
    parser.add_argument('--output',default='artifacts')
    args=parser.parse_args()
    rows=[item.coverage for item in scan_worker(args.game_path)]
    categories=dict(Counter(row['category'] for row in rows))
    known=sum(row.get('text_units',0)>0 for row in rows)
    report={'mods':len(rows),'categories':categories,'text_units':sum(row['text_units'] for row in rows),'known_text_mods':known,
            'in_game_verified':False,'coverage_target_verified':False,
            'reason':'静态提取不等于游戏内生效；需采集、待确认及异常文件尚未逐个验收，未宣称全库 90% 达标。','rows':rows}
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    (output/'coverage-0.5.0.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'coverage-0.5.0.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['mod 名称','UniqueID','分类','文本片段数','文本记录数','游戏内验收','说明'])
        for row in rows:writer.writerow([row['name'],row['mod_id'],row['category'],row['text_units'],row.get('records',0),'待验收','；'.join(row['notes'])])
    lines=['# 0.5.0 本机覆盖审计','',f"共 {len(rows)} 个 mod；{known} 个可静态提取文字，合计 {report['text_units']} 个片段。",'',
           '覆盖率分母应为确认含可见文字的 mod。DLL 的未访问界面、未知资源及异常文件尚未完成逐个确认，因此不以静态表宣称 90% 游戏内成功。', '',
           '| 分类 | 数量 |','|---|---:|']+[f'| {key} | {value} |' for key,value in categories.items()]
    lines+=['','完整逐个名单见同目录 CSV/JSON。纯图像不使用 OCR；损坏 JSON 保留原文件。独立探针验证的是适配器，不代表表中 mod 已完成游戏验收。']
    (output/'coverage-0.5.0.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=True))

if __name__=='__main__':main()
