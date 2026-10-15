"""Create an original, manually translated example package; no API calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modlingo.core import Project

root = Path(__file__).resolve().parent.parent
project = Project.from_mod(root / "examples" / "DemoMod")
project.glossary = {"Starfruit": "杨桃"}
translations = {
    "dialogue.greeting": "你好，{{PlayerName}}！欢迎来到农场。$h",
    "item.starfruit.name": "杨桃果酱",
    "item.starfruit.description": "一罐香甜的杨桃果酱。",
    "dialogue.day": "早上好！#$b#今天的庄稼长得不错。$h",
    "dialogue.farm": "你的 %farm 农场看起来很棒，@！",
    "ui.lines": "农场日志\n今日收获",
}
for key, target in translations.items():
    assert not project.edit(key, target)
output = root / "artifacts"
output.mkdir(exist_ok=True)
project.save(output / "demo-project.json")
report = project.export_zip(output / "demo-translation-zh.zip")
print(f"Manual demo export: {report['exported']} translated, {len(report['skipped'])} skipped; no API calls.")
