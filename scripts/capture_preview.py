"""Optional documentation preview. Requires Pillow; not used by the app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk
from PIL import ImageGrab
from modlingo.core import Project
from modlingo.gui import App, enable_dpi_awareness

root_dir = Path(__file__).resolve().parent.parent
enable_dpi_awareness()
root = tk.Tk()
app = App(root)
app.project = Project.from_mod(root_dir / "examples" / "DemoMod")
app.project.glossary = {"Starfruit": "杨桃"}
app.project.edit("dialogue.greeting", "你好，{{PlayerName}}！欢迎来到农场。$h")
app.project.edit("item.starfruit.name", "杨桃果酱")
app.refresh()
app.status.set("示例预览：译文为人工填写；未调用真实 API。选择条目即可修改，更新后仅处理新增和变更内容。")
root.geometry("+25+25")
root.lift()
root.attributes("-topmost", True)


def capture():
    root.update_idletasks()
    bounds = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_rootx() + root.winfo_width(), root.winfo_rooty() + root.winfo_height())
    output = root_dir / "artifacts" / "qa-editor.png"
    output.parent.mkdir(exist_ok=True)
    ImageGrab.grab(bbox=bounds).save(output)
    root.destroy()
    print(str(output))


root.after(600, capture)
root.mainloop()
