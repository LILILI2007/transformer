"""Render the real launcher with explicitly fictional inventory for documentation."""
import os
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tkinter as tk
from PIL import ImageGrab
from modlingo.discovery import ModItem
from modlingo.gui import enable_dpi_awareness
from modlingo.library_gui import LibraryApp

root_dir = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--view', choices=['main', 'small', 'api'], default='main')
args = parser.parse_args()
with tempfile.TemporaryDirectory() as temp:
    old = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = temp
    enable_dpi_awareness()
    root = tk.Tk()
    app = LibraryApp(root, auto=False)
    game = Path('D:/SteamLibrary/steamapps/common/Stardew Valley')
    items = [ModItem(game / 'Mods' / 'DemoFarm', '演示农场', 'Demo.Farm', '1.0'),
             ModItem(game / 'Mods' / 'DemoDialogue', '演示对话包', 'Demo.Dialogue', '2.0'),
             ModItem(game / 'Mods' / 'DemoUnsupported', '演示硬编码 mod', 'Demo.Unsupported', '1.0', reason='没有 i18n/default.json，暂不提取硬编码文本')]
    app.games = [game]
    app.path_box.configure(values=[str(game)])
    app.show_items(game, items)
    app.tree.set('0', 'state', '成功')
    app.tree.set('0', 'reason', '示例：中文已安装，原文件已备份')
    app.tree.set('1', 'state', '部分完成')
    app.tree.set('1', 'reason', '示例：已安装 18 项；2 项复杂语法暂不支持')
    app.tree.set('2', 'state', '失败')
    app.refresh_inventory()
    app.summary.set('示例名单：成功 1 个；失败或部分完成 2 个。默认全选，可取消不需要的 mod。')
    app.status.set('演示预览：虚构目录和名单，未扫描真实游戏，未调用 API，未安装翻译。')
    root.geometry('+30+30')
    root.lift()
    root.attributes('-topmost', True)
    capture_window = root
    if args.view == 'small':
        width, height = root.minsize()
        root.geometry('%dx%d+30+30' % (width, height))
    elif args.view == 'api':
        app.settings()
        capture_window = next(child for child in root.winfo_children() if isinstance(child, tk.Toplevel))
    output = root_dir / 'artifacts' / ('preview.png' if args.view == 'main' else 'qa-' + args.view + '.png')
    def capture():
        root.update_idletasks()
        x, y = capture_window.winfo_rootx(), capture_window.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + capture_window.winfo_width(), y + capture_window.winfo_height())).save(output)
        for callback in root.tk.call('after', 'info'):
            root.after_cancel(callback)
        root.destroy()
    root.after(600, capture)
    root.mainloop()
    if old is None:
        os.environ.pop('LOCALAPPDATA', None)
    else:
        os.environ['LOCALAPPDATA'] = old
print('Saved fictional inventory preview.')
