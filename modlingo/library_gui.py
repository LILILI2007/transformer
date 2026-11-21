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
from .api_verification import APIVerification
from .provider import APIConfig, Translator
from .theme import COLORS, FarmBanner, apply_theme, fit_window, paper_panel, style_rows
from .workers import call_in_process, scan_worker, detect_worker
from .engine import run_engine


class LibraryApp(APIVerification):
    def __init__(self, root, auto=True):
        self.root = root
        root.title('ModLingo ' + __version__ + ' · 一次翻译多个 mod')
        apply_theme(root)
        fit_window(root)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.items = []
        self.games = []
        self.mods = None
        self.report_path = None
        self.results = []
        self.config = APIConfig(api_key=os.environ.get('MODLINGO_API_KEY', ''), provider='deepseek', disable_thinking=True)
        self.concurrency = tk.StringVar(value='4')
        self.retry_exhausted = tk.BooleanVar(value=False)
        self.game = tk.StringVar()
        self.status = tk.StringVar(value='正在准备自动识别目录；也可以手动指定游戏目录。')
        self.summary = tk.StringVar(value='首次使用：自动识别 → 配置 API → 默认全选 → 翻译并安装 → 查看结果')
        self.search = tk.StringVar()
        self.view = tk.StringVar(value='全部')
        self.row_details = tk.StringVar(value='选择一行查看完整说明；点击选择列或按空格勾选 mod。')
        self.metrics = {key: tk.StringVar(value='0') for key in ('total', 'selected', 'success', 'attention')}
        self.init_verification()
        self._build()
        self.update_api_buttons()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)
        if auto:
            root.after(150, self.detect)

    def _build(self):
        self.banner = FarmBanner(self.root)
        self.banner.pack(fill='x')
        frame = ttk.Frame(self.root, padding=(20, 12))
        frame.pack(fill='both', expand=True)
        steps = ttk.Frame(frame)
        steps.pack(fill='x', pady=(0, 10))
        ttk.Label(steps, text='01  找到农场     →     02  配置 API     →     03  勾选 mod     →     04  翻译并安装',
                  foreground=COLORS['wood'], font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        ttk.Label(steps, text='英语 → 简体中文', style='Muted.TLabel').pack(side='right')
        border, paths_panel = paper_panel(frame, padding=(14, 10))
        border.pack(fill='x', pady=(0, 10))
        ttk.Label(paths_panel, text='农场位置 · 游戏目录', style='Section.TLabel').pack(anchor='w', pady=(0, 6))
        paths = ttk.Frame(paths_panel, style='Paper.TFrame')
        paths.pack(fill='x')
        self.path_box = ttk.Combobox(paths, textvariable=self.game, state='readonly')
        self.path_box.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.path_box.bind('<<ComboboxSelected>>', lambda event: self.scan())
        ttk.Button(paths, text='自动识别', command=self.detect).pack(side='left', padx=(0, 6))
        ttk.Button(paths, text='手动选择', command=self.choose).pack(side='left', padx=(0, 6))
        ttk.Button(paths, text='配置 API', command=self.settings, style='Accent.TButton').pack(side='left')
        self.verification_row(paths_panel)
        controls = ttk.Frame(paths_panel)
        controls.pack(fill='x')
        ttk.Label(controls, text='并发请求').pack(side='left')
        self.concurrency_box = ttk.Combobox(controls, textvariable=self.concurrency, values=['1','2','3','4','5','6','8','16','32'], state='readonly', width=3)
        self.concurrency_box.pack(side='left', padx=6)
        ttk.Button(controls, text='安装采集插件', command=self.install_bridge).pack(side='left', padx=6)
        ttk.Button(controls, text='卸载采集插件', command=self.remove_bridge).pack(side='left')
        ttk.Label(controls, text='DLL/资源文字：进游戏采集 → 退出 → 重新扫描翻译', style='Muted.TLabel').pack(side='right')
        cards = ttk.Frame(frame)
        self.cards = cards
        cards.pack(fill='x', pady=(0, 10))
        for index, (key, title, note, variant) in enumerate([
            ('total', '发现的 mod', '来自当前游戏目录', 'Metric.TLabel'),
            ('selected', '已勾选', '本次要处理的 mod', 'Metric.TLabel'),
            ('success', '已成功', '中文文件准备就绪', 'GoodMetric.TLabel'),
            ('attention', '需要关注', '失败、部分完成或不支持', 'AlertMetric.TLabel'),
        ]):
            cards.columnconfigure(index, weight=1, uniform='cards')
            edge, card = paper_panel(cards, padding=(12, 8))
            edge.grid(row=0, column=index, sticky='nsew', padx=(0 if index == 0 else 8, 0))
            ttk.Label(card, textvariable=self.metrics[key], style=variant).pack(side='left', padx=(0, 12))
            labels = ttk.Frame(card, style='Paper.TFrame')
            labels.pack(side='left')
            ttk.Label(labels, text=title, style='Section.TLabel').pack(anchor='w')
            ttk.Label(labels, text=note, style='PaperMuted.TLabel').pack(anchor='w')
        edge, inventory = paper_panel(frame, padding=(12, 10))
        self.inventory_edge = edge
        edge.pack(fill='both', expand=True)
        tools = ttk.Frame(inventory, style='Paper.TFrame')
        tools.pack(fill='x', pady=(0, 8))
        ttk.Label(tools, text='翻译清单', style='Section.TLabel').pack(side='left', padx=(0, 12))
        ttk.Button(tools, text='全选', command=lambda: self.select_all(True), style='Quiet.TButton').pack(side='left', padx=(0, 5))
        ttk.Button(tools, text='全不选', command=lambda: self.select_all(False), style='Quiet.TButton').pack(side='left', padx=(0, 5))
        ttk.Button(tools, text='重新扫描', command=self.scan, style='Quiet.TButton').pack(side='left')
        ttk.Combobox(tools, textvariable=self.view, values=['全部', '已勾选', '成功', '需要关注'],
                     state='readonly', width=11).pack(side='right', padx=(8, 0))
        ttk.Entry(tools, textvariable=self.search, width=23).pack(side='right', padx=(8, 0))
        ttk.Label(tools, text='搜索', style='PaperMuted.TLabel').pack(side='right')
        self.summary_label = ttk.Label(inventory, textvariable=self.summary, style='PaperMuted.TLabel', wraplength=1100)
        self.summary_label.pack(anchor='w', pady=(0, 8))
        holder = ttk.Frame(inventory, style='Paper.TFrame')
        holder.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(holder, columns=('pick', 'name', 'state', 'reason'), show='headings', selectmode='browse', height=5)
        for key, title, width in [('pick', '选择', 65), ('name', 'mod 名称 / 版本', 280), ('state', '状态', 130), ('reason', '说明 / 失败原因', 560)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=45, stretch=key in ('name', 'reason'))
        bar = ttk.Scrollbar(holder, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        style_rows(self.tree)
        self.tree.bind('<Button-1>', self.toggle)
        self.tree.bind('<space>', self.toggle_keyboard)
        self.tree.bind('<<TreeviewSelect>>', self.select_details)
        self.empty_hint = ttk.Label(holder, text='农场清单还空着\n自动识别或选择游戏目录后，mod 会出现在这里。',
                                    style='PaperMuted.TLabel', justify='center')
        self.empty_hint.place(relx=.5, rely=.55, anchor='center')
        self.details_label = ttk.Label(inventory, textvariable=self.row_details, style='PaperMuted.TLabel', wraplength=1100)
        self.details_label.pack(anchor='w', pady=(7, 0))
        footer = ttk.Frame(frame)
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        # Reserve the footer first; the inventory gets the remaining space.
        edge.pack_forget()
        edge.pack(fill='both', expand=True)
        bottom = ttk.Frame(footer)
        bottom.pack(fill='x', pady=(12, 8))
        ttk.Button(bottom, text='单 mod 编辑', command=self.editor).pack(side='left', padx=(0, 6))
        ttk.Button(bottom, text='查看结果名单', command=self.open_report).pack(side='left', padx=(0, 6))
        ttk.Button(bottom, text='恢复中文备份', command=self.rollback).pack(side='left')
        self.start_button = ttk.Button(bottom, text='开始翻译并安装', command=self.start, style='Accent.TButton')
        self.start_button.pack(side='right')
        self.stop_button = ttk.Button(bottom, text='停止并保存', command=self.request_stop, state='disabled')
        self.stop_button.pack(side='right', padx=8)
        ttk.Checkbutton(tools, text='重试待处理（计费）', variable=self.retry_exhausted).pack(side='left', padx=8)
        ttk.Label(footer, text='改写前自动备份已有中文。请先退出游戏与 SMAPI；翻译完成后以中文启动游戏。',
                  style='Muted.TLabel').pack(anchor='w', pady=(0, 7))
        self.progress = ttk.Progressbar(footer)
        self.progress.pack(fill='x')
        self.status_label = ttk.Label(footer, textvariable=self.status, wraplength=1100, style='Muted.TLabel')
        self.status_label.pack(anchor='w', pady=(7, 0))
        self.search.trace_add('write', lambda *args: self.refresh_inventory())
        self.view.trace_add('write', lambda *args: self.refresh_inventory())
        self.root.bind('<Configure>', self.resize_text, add='+')

    def resize_text(self, event):
        if event.widget == self.root:
            width = max(450, self.root.winfo_width()-100)
            self.status_label.configure(wraplength=width)
            self.summary_label.configure(wraplength=width)
            self.details_label.configure(wraplength=width)
            compact = self.root.winfo_height() < round(760 * self.banner.scale)
            if compact != self.banner.compact:
                self.banner.compact = compact
                self.banner.configure(height=round((75 if compact else 148) * self.banner.scale))
            small = self.root.winfo_height() < round(720 * self.banner.scale)
            if small and self.cards.winfo_manager():
                self.cards.pack_forget()
            elif not small and not self.cards.winfo_manager():
                self.cards.pack(before=self.inventory_edge, fill='x', pady=(0, 10))

    def refresh_inventory(self):
        counts = {'total': len(self.items), 'selected': sum(i.selected for i in self.items), 'success': 0, 'attention': 0}
        visible = 0
        query, view = self.search.get().strip().casefold(), self.view.get()
        for index, item in enumerate(self.items):
            iid = str(index)
            if not self.tree.exists(iid):
                continue
            state = self.tree.set(iid, 'state')
            reason = self.tree.set(iid, 'reason')
            attention = state in ('无法处理', '失败', '部分完成', '部分支持', '文件异常', '尚未支持', '需进游戏采集', '待确认', '已暂停')
            counts['success'] += state == '成功'
            counts['attention'] += attention
            tag = 'good' if state == '成功' else 'failed' if state == '失败' else 'attention' if attention else 'odd' if index % 2 else 'even'
            self.tree.item(iid, tags=(tag,))
            match = not query or query in (item.name + item.mod_id + str(item.folder) + reason).casefold()
            match = match and (view == '全部' or (view == '已勾选' and item.selected) or (view == '成功' and state == '成功') or (view == '需要关注' and attention))
            if match:
                self.tree.move(iid, '', 'end')
                visible += 1
            else:
                self.tree.detach(iid)
        for key, value in counts.items():
            self.metrics[key].set(str(value))
        if visible:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.configure(text='没有匹配的 mod\n可调整搜索或筛选；筛选不会取消已勾选的项目。' if self.items else '农场清单还空着\n自动识别或选择游戏目录后，mod 会出现在这里。')
            self.empty_hint.place(relx=.5, rely=.55, anchor='center')

    def select_details(self, event=None):
        selected = self.tree.selection()
        if selected:
            iid = selected[0]
            self.row_details.set(self.tree.set(iid, 'state') + ' · ' + self.tree.set(iid, 'reason'))

    def clear_rows(self):
        rows = [str(i) for i in range(len(self.items)) if self.tree.exists(str(i))]
        if rows:
            self.tree.delete(*rows)
        self.row_details.set('选择一行查看完整说明；点击选择列或按空格勾选 mod。')

    def guard(self):
        if self.busy:
            self.status.set('正在处理，请等待完成或停止后续请求。')
            return False
        return True

    def set_busy(self, value):
        self.busy = value
        self.update_api_buttons()
        self.stop_button.configure(state='normal' if value else 'disabled')
        self.path_box.configure(state='disabled' if value else 'readonly')
        self.concurrency_box.configure(state='disabled' if value else 'readonly')

    def request_stop(self):
        self.stop.set()
        self.status.set('正在取消本地等待并保存已返回结果；已发送的请求仍可能计费。')

    def install_bridge(self):
        if not self.guard() or self.mods is None:
            return
        from .bridge_io import install_plugin
        mods = self.mods
        def worker():
            path = install_plugin(mods)
            self.events.put(('message', '采集插件已安装：'+str(path)+'。用 SMAPI 启动，访问 mod 菜单后退出，再重新扫描。插件不联网。'))
        self.launch(worker)

    def remove_bridge(self):
        if not self.guard() or self.mods is None:
            return
        if not messagebox.askyesno('卸载采集插件', '移除插件程序，保留采集记录和译文供恢复。请先退出游戏。继续？', parent=self.root):
            return
        from .bridge_io import uninstall_plugin
        mods=self.mods
        def worker():
            uninstall_plugin(mods)
            self.events.put(('message','采集插件程序已卸载；采集和译文保留。'))
        self.launch(worker)

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
            games, selected, items = call_in_process(detect_worker, saved)
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
            self.events.put(('scanned', (root, call_in_process(scan_worker, root))))
        self.status.set('正在扫描 mod 并锁定文件版本……')
        self.launch(snapshot_worker)

    def show_items(self, root, items):
        self.game.set(str(root))
        self.mods = root / 'Mods'
        self.clear_rows()
        self.items = items
        for index, item in enumerate(items):
            self.tree.insert('', 'end', iid=str(index), values=('☑' if item.selected else '☐', item.name + ' ' + item.version,
                             getattr(item,'category','无法处理' if item.reason else '待翻译'), item.reason or str(item.folder)))
        atomic_write(data_root() / 'settings.json', json.dumps({'game_directory': str(root)}, ensure_ascii=False).encode('utf-8'))
        self.summary.set('发现 %d 个 mod，默认全选；%d 个暂不可处理，将在结果中说明原因。点击选择列或按空格切换。' % (len(items), sum(bool(i.reason) for i in items)))
        self.status.set('Mods 目录已锁定：' + str(self.mods) + '。请配置 API 后开始。')
        self.refresh_inventory()

    def select_all(self, value):
        if not self.guard():
            return
        for index, item in enumerate(self.items):
            item.selected = value
            self.tree.set(str(index), 'pick', '☑' if value else '☐')
        self.refresh_inventory()

    def toggle_index(self, iid):
        if not iid or not self.guard():
            return
        item = self.items[int(iid)]
        item.selected = not item.selected
        self.tree.set(iid, 'pick', '☑' if item.selected else '☐')
        self.refresh_inventory()

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
        from .api_settings import APISettingsDialog
        def apply(config):
            self.config = config
            self.invalidate_api()
            self.status.set('API 设置已保存，请通过验证后再翻译。')
        self.api_dialog = APISettingsDialog(self.root, self.config, apply, self.test_connection)

    def test_connection(self):
        self.begin_api_test(self.set_busy)

    def start(self):
        if not self.guard() or not self.require_verified_api():
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
        self.plan_decision=queue.Queue()
        self.stop.clear()
        self.results = []
        self.progress.configure(maximum=len(items), value=0)
        mods, config, concurrency = self.mods, replace(self.config), int(self.concurrency.get())
        retry_exhausted = self.retry_exhausted.get()
        def worker():
            path, report = run_engine(items, mods, config, self.stop, concurrency=concurrency,
                                      plan_decision=self.plan_decision,
                                      retry_exhausted=retry_exhausted,
                                      notify=lambda kind,data: self.events.put((kind,data)))
            self.events.put(('report', (path, report)))
        self.status.set('正在准备并发任务；每批校验通过的译文立即保存。')
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
            receipts = [Path(p) for r in report['success'] + report['failure'] for p in r.get('receipts', [r['receipt']] if r.get('receipt') else [])]
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
            import time
            started = time.perf_counter()
            for _ in range(50):
                if time.perf_counter()-started > .008:
                    break
                kind, data = self.events.get_nowait()
                if kind == 'detected':
                    self.games, selected, items = data
                    self.path_box.configure(values=[str(g) for g in self.games])
                    if selected:
                        self.show_items(selected, items)
                    else:
                        self.clear_rows()
                        self.mods, self.items = None, []
                        self.game.set('')
                        self.refresh_inventory()
                        self.status.set('找到多份游戏，请在目录下拉框选择一份。' if self.games else '未找到带 Mods 的游戏目录，请用“手动选择”。')
                elif kind == 'scanned':
                    self.show_items(*data)
                elif kind == 'result':
                    self.results.append(data)
                    for index, item in enumerate(self.items):
                        if str(item.folder) == data['folder']:
                            self.tree.set(str(index), 'state', data['status'])
                            self.tree.set(str(index), 'reason', data['reason'])
                    self.refresh_inventory()
                    self.select_details()
                    self.status.set('已处理 %d 个：%s · %s' % (len(self.results), data['name'], data['status']))
                elif kind == 'translation_plan':
                    self.summary.set('去重后待译 %d 个字段 / %d 个片段；已可用 %d 个片段，合并重复 %d 个片段。' % (data['pending_fields'],data['unique_pending_fragments'],data['already_available_fragments'],data['duplicate_fragments']))
                    if data.get('dependency_waiting_fields'):
                        self.summary.set(self.summary.get()+'另有 %d 个显示字段等待名称译文，将在本轮自动回填。' % data['dependency_waiting_fields'])
                    if data['pending_fields']:
                        bridge=data.get('bridge',{})
                        bridge_note='\n伴随插件缺失：存在插件译文时会补齐程序。' if not bridge.get('ready',True) else ''
                        approved=messagebox.askyesno('确认实际翻译用量',self.summary.get()+bridge_note+'\n初始预计约 %d 次 HTTP 请求；拆分与重试可能增加请求数。\n\n确认已退出游戏。继续将向 API 发送文本并可能计费，随后备份并安装已校验译文。' % data['estimated_initial_requests'],parent=self.root)
                        self.plan_decision.put(approved)
                elif kind == 'engine_progress':
                    self.progress.configure(maximum=max(1,data['total']), value=data['completed']+data.get('failed',0))
                    eta='估算中' if data['eta_seconds'] is None else '%d 分 %d 秒' % divmod(data['eta_seconds'],60)
                    age='尚无响应' if data['last_response'] is None else '%d 秒前' % max(0,int(time.time()-data['last_response']))
                    self.status.set('%s · 片段完成 %d/%d · 失败 %d · 缓存 %d · HTTP 已发 %d / 处理中 %d · 重试等待 %d · 最近响应 %s · 本轮剩余 %s' % (data['phase'],data['completed'],data['total'],data.get('failed',0),data['cache_hits'],data['requests'],data['in_flight'],data['waiting_retry'],age,eta))
                    if data.get('waiting_dependencies') or data.get('dependency_hits'):
                        self.status.set(self.status.get()+' · 等待名称 %d / 已回填 %d 片段' % (data.get('waiting_dependencies',0),data.get('dependency_hits',0)))
                elif kind == 'report':
                    self.report_path, report = data
                    partial = sum(r['status'] == '部分完成' for r in report['failure'])
                    unsupported = sum(r['status'] == '不支持' for r in report['failure'])
                    if report.get('schema_version', 1) >= 2:
                        from collections import Counter
                        counts=Counter(r['status'] for r in report['success']+report['failure'])
                        self.summary.set('；'.join('%s %d 个' % (name,counts[name]) for name in ('成功','部分完成','失败','文件异常','需进游戏采集','无需翻译','仅其他语言','待确认','已暂停'))+'。可采集不代表已汉化，详情见记录。')
                        if report.get('dependencies'):
                            dependencies=report['dependencies']
                            self.summary.set(self.summary.get()+'本轮自动回填 %d 个显示字段，%d 个仍等待可靠名称。' % (dependencies['same_run_backfilled_fields'],dependencies['pending_fields']))
                    else:
                        failed = len(report['failure']) - partial - unsupported
                        self.summary.set('成功 %d 个；部分完成 %d 个；失败 %d 个；不支持 %d 个（失败或部分完成等共 %d 个需关注）。详情见记录。' % (len(report['success']), partial, failed, unsupported, len(report['failure'])))
                    if report.get('paused_reason'):
                        self.status.set('任务已暂停：'+report['paused_reason']+'；已返回译文已保存。结果：'+str(self.report_path))
                    else:
                        self.status.set('处理完成。请查看名单；进入游戏后选择中文并通过 SMAPI 启动。结果：' + str(self.report_path))
                elif kind == 'api_test':
                    self.finish_api_test(data)
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
            self.status.set('正在取消本地等待并保存结果；已发送请求仍可能计费，保存后可关闭。')
            return
        self.root.destroy()
