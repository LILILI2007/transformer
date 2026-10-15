"""Player-first launcher: discover, select, translate, install and report."""
from __future__ import annotations

import json
import os
import queue
import threading
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from . import __version__
from .batch import data_root, library_lock, restore, run_batch
from .core import ProjectError, atomic_write, read_jsonc
from .discovery import discover_games, game_root, scan_mods
from .provider import APIConfig, Translator


class LibraryApp:
    def __init__(self, root, auto=True):
        self.root = root
        root.title('ModLingo ' + __version__ + ' · 一次翻译多个 mod')
        root.geometry('1120x760')
        root.minsize(850, 580)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.items = []
        self.games = []
        self.mods = None
        self.report_path = None
        self.results = []
        self.config = APIConfig(api_key=os.environ.get('MODLINGO_API_KEY', ''))
        self.game = tk.StringVar()
        self.status = tk.StringVar(value='正在准备自动识别目录；也可以手动指定游戏目录。')
        self.summary = tk.StringVar(value='首次使用：自动识别 → 配置 API → 默认全选 → 翻译并安装 → 查看结果')
        self._build()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)
        if auto:
            root.after(150, self.detect)

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Treeview', rowheight=30)
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='ModLingo · 批量汉化', font=('Microsoft YaHei UI', 22, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='翻译成功后直接安装中文；已有中文自动备份。请先退出游戏和 SMAPI。').pack(anchor='w', pady=(4, 14))
        paths = ttk.Frame(frame)
        paths.pack(fill='x')
        ttk.Label(paths, text='游戏目录').pack(side='left')
        self.path_box = ttk.Combobox(paths, textvariable=self.game, state='readonly')
        self.path_box.pack(side='left', fill='x', expand=True, padx=8)
        self.path_box.bind('<<ComboboxSelected>>', lambda event: self.scan())
        ttk.Button(paths, text='自动识别', command=self.detect).pack(side='left', padx=4)
        ttk.Button(paths, text='手动选择', command=self.choose).pack(side='left')
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=12)
        for name, callback in [('全选', lambda: self.select_all(True)), ('全不选', lambda: self.select_all(False)),
                               ('重新扫描', self.scan), ('API 设置', self.settings), ('单 mod 编辑', self.editor)]:
            ttk.Button(actions, text=name, command=callback).pack(side='left', padx=(0, 6))
        self.start_button = ttk.Button(actions, text='翻译并安装所选 mod', command=self.start)
        self.start_button.pack(side='right')
        self.stop_button = ttk.Button(actions, text='停止后续请求', command=self.stop.set, state='disabled')
        self.stop_button.pack(side='right', padx=6)
        ttk.Label(frame, textvariable=self.summary, wraplength=1000).pack(anchor='w', pady=(0, 8))
        holder = ttk.Frame(frame)
        holder.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(holder, columns=('pick', 'name', 'state', 'reason'), show='headings', selectmode='browse')
        for key, title, width in [('pick', '选择', 55), ('name', 'mod 名称 / 版本', 240), ('state', '状态', 110), ('reason', '说明 / 失败原因', 550)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=45, stretch=key in ('name', 'reason'))
        bar = ttk.Scrollbar(holder, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<Button-1>', self.toggle)
        self.tree.bind('<space>', self.toggle_keyboard)
        bottom = ttk.Frame(frame)
        bottom.pack(fill='x', pady=12)
        ttk.Button(bottom, text='打开结果与成功失败名单', command=self.open_report).pack(side='left')
        ttk.Button(bottom, text='从结果记录恢复中文备份', command=self.rollback).pack(side='left', padx=8)
        ttk.Label(bottom, text='进入游戏后请选择中文，并通过 SMAPI 启动。').pack(side='right')
        self.progress = ttk.Progressbar(frame)
        self.progress.pack(fill='x')
        ttk.Label(frame, textvariable=self.status, wraplength=1000).pack(anchor='w', pady=(8, 0))

    def guard(self):
        if self.busy:
            self.status.set('正在处理，请等待完成或停止后续请求。')
            return False
        return True

    def set_busy(self, value):
        self.busy = value
        self.start_button.configure(state='disabled' if value else 'normal')
        self.stop_button.configure(state='normal' if value else 'disabled')
        self.path_box.configure(state='disabled' if value else 'readonly')

    def launch(self, worker):
        self.set_busy(True)
        def wrapped():
            try:
                worker()
            except (ProjectError, OSError) as exc:
                self.events.put(('error', str(exc)))
            except Exception:
                self.events.put(('error', '处理遇到异常，已停止。请保留结果记录并重新扫描重试。'))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=wrapped, daemon=True).start()

    def detect(self):
        if not self.guard():
            return
        self.status.set('正在检查 Steam 库、注册表和常见安装位置，不会遍历整个硬盘。')
        def worker():
            saved = None
            try:
                saved = read_jsonc(data_root() / 'settings.json').get('game_directory')
            except (OSError, ProjectError, AttributeError):
                pass
            games = discover_games(saved)
            selected = games[0] if len(games) == 1 else next((g for g in games if str(g) == saved), None)
            items = scan_mods(selected / 'Mods') if selected else []
            self.events.put(('detected', (games, selected, items)))
        self.launch(worker)

    def choose(self):
        if not self.guard():
            return
        path = filedialog.askdirectory(title='选择星露谷游戏目录（含游戏程序和 Mods）', parent=self.root)
        if path:
            try:
                root = game_root(path)
                if str(root) not in [str(g) for g in self.games]:
                    self.games.append(root)
                self.path_box.configure(values=[str(g) for g in self.games])
                self.game.set(str(root))
                self.scan()
            except (ProjectError, OSError) as exc:
                messagebox.showerror('目录需要调整', str(exc), parent=self.root)

    def scan(self):
        if not self.guard() or not self.game.get():
            return
        # Snapshot the Tk value before entering the worker.
        selected = self.game.get()
        def snapshot_worker():
            root = game_root(selected)
            self.events.put(('scanned', (root, scan_mods(root / 'Mods'))))
        self.status.set('正在扫描 mod 并锁定文件版本……')
        self.launch(snapshot_worker)

    def show_items(self, root, items):
        self.game.set(str(root))
        self.mods = root / 'Mods'
        self.items = items
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(items):
            self.tree.insert('', 'end', iid=str(index), values=('☑', item.name + ' ' + item.version,
                             '无法处理' if item.reason else '待翻译', item.reason or str(item.folder)))
        atomic_write(data_root() / 'settings.json', json.dumps({'game_directory': str(root)}, ensure_ascii=False).encode('utf-8'))
        self.summary.set('发现 %d 个 mod，默认全选；%d 个暂不可处理，将在结果中说明原因。点击选择列或按空格切换。' % (len(items), sum(bool(i.reason) for i in items)))
        self.status.set('Mods 目录已锁定：' + str(self.mods) + '。请配置 API 后开始。')

    def select_all(self, value):
        if not self.guard():
            return
        for index, item in enumerate(self.items):
            item.selected = value
            self.tree.set(str(index), 'pick', '☑' if value else '☐')

    def toggle_index(self, iid):
        if not iid or not self.guard():
            return
        item = self.items[int(iid)]
        item.selected = not item.selected
        self.tree.set(iid, 'pick', '☑' if item.selected else '☐')

    def toggle(self, event):
        if self.tree.identify_column(event.x) == '#1':
            self.toggle_index(self.tree.identify_row(event.y))

    def toggle_keyboard(self, event):
        selected = self.tree.selection()
        if selected:
            self.toggle_index(selected[0])
        return 'break'

    def settings(self):
        if not self.guard():
            return
        window = tk.Toplevel(self.root)
        window.title('API 设置')
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        fields = []
        for title, value, secret in [('服务地址', self.config.base_url, False), ('模型名称', self.config.model, False), ('API 密钥（仅本次运行）', self.config.api_key, True)]:
            ttk.Label(frame, text=title).pack(anchor='w', pady=(8, 4))
            field = ttk.Entry(frame, width=65, show='•' if secret else '')
            field.insert(0, value)
            field.pack(fill='x')
            fields.append(field)
        ttk.Label(frame, text='待译文本会发给你选定的服务，费用由你的 API 账号承担。\n支持 Chat Completions 文本接口；远程用 HTTPS，本机可用 HTTP。').pack(pady=15)
        def save(test=False):
            try:
                config = APIConfig(*(field.get().strip() for field in fields))
                config.endpoint()
                self.config = config
                window.destroy()
                self.status.set('API 已配置；现在可以翻译并安装所选 mod。')
                if test:
                    self.launch(lambda: (Translator(replace(config)).translate([('test', 'Hello, farmer!')], {}), self.events.put(('message', '连接及响应格式检查通过。'))))
            except ProjectError as exc:
                messagebox.showerror('配置待修正', str(exc), parent=window)
        ttk.Button(frame, text='保存并测试（可能计费）', command=lambda: save(True)).pack(side='left')
        ttk.Button(frame, text='保存设置', command=save).pack(side='right')

    def start(self):
        if not self.guard():
            return
        items = [replace(item) for item in self.items if item.selected]
        if not items or self.mods is None:
            self.status.set('请先识别目录，并至少勾选一个 mod。')
            return
        try:
            self.config.endpoint()
        except ProjectError as exc:
            self.status.set(str(exc))
            self.settings()
            return
        if not messagebox.askyesno('开始翻译并安装', '将处理 %d 个 mod，向配置的 API 发送待译文本并可能计费。\n成功译文会直接写入各 mod 的 i18n/zh.json，原中文会自动备份。\n复杂语法、不支持的文件和未通过检查的译文会列入失败或部分完成名单。\n\n请确认已退出游戏和 SMAPI。是否开始？' % len(items), parent=self.root):
            return
        self.stop.clear()
        self.results = []
        self.progress.configure(maximum=len(items), value=0)
        mods, config = self.mods, replace(self.config)
        def worker():
            path, report = run_batch(items, mods, Translator(config), self.stop,
                                     notify=lambda result: self.events.put(('result', result)))
            self.events.put(('report', (path, report)))
        self.status.set('正在批量翻译并安装；每个 mod 完成后保存结果。')
        self.launch(worker)

    def open_report(self):
        if self.report_path:
            path = self.report_path.with_suffix('.txt')
            if os.name == 'nt':
                os.startfile(str(path if path.exists() else self.report_path))
            else:
                messagebox.showinfo('结果记录', str(self.report_path), parent=self.root)
        else:
            messagebox.showinfo('结果记录', '完成批量处理后可打开结果。历史记录保存在：\n' + str(data_root() / 'reports'), parent=self.root)

    def rollback(self):
        if not self.guard():
            return
        path = filedialog.askopenfilename(title='选择要恢复的批量结果 JSON', initialdir=str(data_root() / 'reports'), filetypes=[('批量结果', '*.json')], parent=self.root)
        if not path:
            return
        try:
            report = read_jsonc(Path(path))
            receipts = [Path(r['receipt']) for r in report['success'] + report['failure'] if r.get('receipt')]
            if not receipts:
                raise ProjectError('此记录没有需要恢复的安装文件')
            if not messagebox.askyesno('恢复备份', '将尝试恢复这次处理的 %d 个中文文件。后续被修改的文件会跳过。继续？' % len(receipts), parent=self.root):
                return
            mods = Path(report['mods'])
            def worker():
                done, failed = 0, []
                with library_lock(mods, data_root()):
                    for receipt in receipts:
                        try:
                            if not receipt.resolve().is_relative_to((data_root() / 'backups').resolve()):
                                raise ProjectError('备份记录不在本机 ModLingo 备份目录')
                            restore(receipt)
                            done += 1
                        except (ProjectError, OSError) as exc:
                            failed.append(str(exc))
                self.events.put(('message', '已恢复 %d 个，跳过 %d 个。%s' % (done, len(failed), '；'.join(failed[:4]))))
            self.launch(worker)
        except (ProjectError, OSError, KeyError, TypeError) as exc:
            messagebox.showerror('无法恢复', str(exc), parent=self.root)

    def editor(self):
        if self.guard():
            from .gui import App
            window = tk.Toplevel(self.root)
            editor = App(window)
            editor.config = replace(self.config)

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'detected':
                    self.games, selected, items = data
                    self.path_box.configure(values=[str(g) for g in self.games])
                    if selected:
                        self.show_items(selected, items)
                    else:
                        self.mods, self.items = None, []
                        self.game.set('')
                        self.tree.delete(*self.tree.get_children())
                        self.status.set('找到多份游戏，请在目录下拉框选择一份。' if self.games else '未找到带 Mods 的游戏目录，请用“手动选择”。')
                elif kind == 'scanned':
                    self.show_items(*data)
                elif kind == 'result':
                    self.results.append(data)
                    for index, item in enumerate(self.items):
                        if str(item.folder) == data['folder']:
                            self.tree.set(str(index), 'state', data['status'])
                            self.tree.set(str(index), 'reason', data['reason'])
                    self.progress.configure(value=len(self.results))
                    self.status.set('已处理 %d 个：%s · %s' % (len(self.results), data['name'], data['status']))
                elif kind == 'report':
                    self.report_path, report = data
                    self.summary.set('成功 %d 个；失败或部分完成 %d 个。部分完成已安装的条目和跳过原因见结果记录。' % (len(report['success']), len(report['failure'])))
                    self.status.set('处理完成。请查看名单；进入游戏后选择中文并通过 SMAPI 启动。结果：' + str(self.report_path))
                elif kind in ('error', 'message'):
                    self.status.set(data)
                elif kind == 'done':
                    self.set_busy(False)
        except queue.Empty:
            pass
        except (OSError, ProjectError) as exc:
            self.status.set('保存或显示结果失败：' + str(exc))
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            self.stop.set()
            self.status.set('正在停止，请等待当前请求结束。已完成 mod 会保留；处理结束后再关闭。')
            return
        self.root.destroy()
