"""Chinese Tk desktop interface; network work stays off the UI thread."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import sys
import threading
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .core import Project, ProjectError, glossary_warnings, unsupported_reason, validate_translation
from .provider import APIConfig, Translator, batches


def asset_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def enable_dpi_awareness():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("ModLingo · 星露谷 mod 翻译工作台")
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        width = min(round(1180 * scale), self.root.winfo_screenwidth() - 60)
        height = min(round(820 * scale), self.root.winfo_screenheight() - 90)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(round(1000 * scale), width), min(round(720 * scale), height))
        self.root.configure(bg="#f3f5f0")
        self.project = None
        self.project_path = None
        self.current_key = None
        self.visible_keys = []
        self.busy = False
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.config = APIConfig(api_key=os.environ.get("MODLINGO_API_KEY", ""))
        self.status = tk.StringVar(value="先打开示例体验，或选择包含 manifest.json 的单个 mod 文件夹。")
        self.summary = tk.StringVar(value="本地工作台  /  英语 → 简体中文")
        self.filter = tk.StringVar(value="全部")
        self.search = tk.StringVar()
        self.details = tk.StringVar(value="选择左侧条目，在右侧修改或确认译文。")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f0")
        style.configure("TLabel", background="#f3f5f0", foreground="#24352d", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", background="#32664c", foreground="white")
        style.map("Accent.TButton", background=[("active", "#244d39"), ("disabled", "#a2afa5")])
        style.configure("Treeview", rowheight=31, font=("Microsoft YaHei UI", 10), background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), padding=8)
        style.map("Treeview", background=[("selected", "#32664c")])
        wrapper = ttk.Frame(self.root, padding=22)
        wrapper.pack(fill="both", expand=True)
        header = ttk.Frame(wrapper)
        header.pack(fill="x")
        ttk.Label(header, text="ModLingo", font=("Segoe UI", 25, "bold")).pack(side="left")
        ttk.Label(header, text="星露谷 mod 翻译工作台", font=("Microsoft YaHei UI", 14)).pack(side="left", padx=16, pady=9)
        ttk.Button(header, text="API 设置", command=self.settings).pack(side="right")
        ttk.Label(wrapper, text="01 导入 mod    →    02 翻译与检查    →    03 确认变更    →    04 导出并安装", foreground="#617266").pack(anchor="w", pady=(2, 18))
        toolbar = ttk.Frame(wrapper)
        toolbar.pack(fill="x")
        for label, command in [("打开示例", self.sample), ("导入 mod", self.import_mod), ("载入更新", self.update_mod), ("打开项目", self.open_project), ("保存副本", self.save_copy), ("术语表", self.glossary_dialog)]:
            ttk.Button(toolbar, text=label, command=command).pack(side="left", padx=(0, 7))
        ttk.Label(wrapper, textvariable=self.summary, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(16, 9))
        filters = ttk.Frame(wrapper)
        filters.pack(fill="x", pady=(0, 9))
        ttk.Label(filters, text="查看").pack(side="left")
        dropdown = ttk.Combobox(filters, textvariable=self.filter, width=13, state="readonly", values=["全部", "待翻译", "需要处理", "可导出", "原文回退", "暂不支持"])
        dropdown.pack(side="left", padx=8)
        dropdown.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        ttk.Label(filters, text="搜索").pack(side="left", padx=(10, 0))
        search = ttk.Entry(filters, textvariable=self.search, width=29)
        search.pack(side="left", padx=8)
        search.bind("<KeyRelease>", lambda event: self.refresh())
        self.translate_button = ttk.Button(filters, text="翻译待处理文本", style="Accent.TButton", command=self.translate)
        self.translate_button.pack(side="right")
        self.cancel_button = ttk.Button(filters, text="停止后续请求", command=self.stop_event.set, state="disabled")
        self.cancel_button.pack(side="right", padx=8)
        body = ttk.Panedwindow(wrapper, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        right = ttk.Frame(body, padding=(18, 0, 0, 0))
        body.add(left, weight=5)
        body.add(right, weight=6)
        self.tree = ttk.Treeview(left, columns=("state", "text"), show="headings", selectmode="browse")
        self.tree.heading("state", text="状态")
        self.tree.heading("text", text="英文原文 / 翻译键")
        self.tree.column("state", width=115, minwidth=105, stretch=False)
        self.tree.column("text", width=340, minwidth=180)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select_entry)
        self.key_label = ttk.Label(right, text="尚未选择条目", font=("Microsoft YaHei UI", 10, "bold"), wraplength=540)
        self.key_label.pack(anchor="w", pady=(0, 8))
        ttk.Label(right, text="英文原文").pack(anchor="w")
        self.source_text = ScrolledText(right, height=5, wrap="word", font=("Microsoft YaHei UI", 11), bd=1, relief="solid", padx=10, pady=8)
        self.source_text.pack(fill="x", pady=(5, 10))
        self.source_text.configure(state="disabled")
        ttk.Label(right, text="中文译文 · 可直接修改").pack(anchor="w")
        self.target_text = ScrolledText(right, height=6, wrap="word", font=("Microsoft YaHei UI", 11), bd=1, relief="solid", padx=10, pady=8, undo=True)
        self.target_text.pack(fill="both", expand=True, pady=(5, 8))
        self.confirm_button = ttk.Button(right, text="保存 / 确认此条", command=self.confirm_entry, style="Accent.TButton")
        self.confirm_button.pack(anchor="e")
        ttk.Label(right, textvariable=self.details, wraplength=520, foreground="#785d2c", justify="left").pack(anchor="w", fill="x", pady=(10, 0))
        footer = ttk.Frame(wrapper)
        footer.pack(fill="x", pady=(16, 0))
        ttk.Button(footer, text="安装与恢复说明", command=self.install_help).pack(side="left")
        ttk.Button(footer, text="导出翻译 ZIP", command=self.export, style="Accent.TButton").pack(side="right")
        self.progress = ttk.Progressbar(wrapper, mode="determinate")
        self.progress.pack(fill="x", pady=(12, 7))
        ttk.Label(wrapper, textvariable=self.status, wraplength=1110, foreground="#536458").pack(anchor="w")

    def guard(self, require_project=True):
        if self.busy:
            messagebox.showinfo("正在处理", "请等待当前请求结束，或先停止后续请求。", parent=self.root)
            return False
        if require_project and not self.project:
            messagebox.showinfo("尚未导入", "请先导入 mod 或打开示例。", parent=self.root)
            return False
        return True

    def _commit(self, force=False):
        if not self.project or not self.current_key or self.busy:
            return []
        entry = self.project.entries[self.current_key]
        value = self.target_text.get("1.0", "end-1c")
        if force or value != entry.target:
            issues = self.project.edit(self.current_key, value)
            self.checkpoint()
            return issues
        return []

    def checkpoint(self):
        if self.project and self.project_path:
            try:
                self.project.save(self.project_path)
            except OSError:
                self.status.set("项目保存失败，请检查磁盘空间和目录权限，并使用“保存副本”。")
                messagebox.showerror("保存失败", "当前进度尚未保存，请检查磁盘空间和目录权限。", parent=self.root)
                raise

    def set_project(self, project, path=None):
        self._commit()
        if path is None:
            identity = hashlib.sha256(project.mod_id.casefold().encode("utf-8")).hexdigest()[:16]
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "ModLingo" / "projects"
            path = base / (identity + ".json")
            # Never silently replace a different session's existing project.
            if path.exists():
                old = Project.load(path)
                old.merge_update(project)
                project = old
        self.project = project
        self.project_path = Path(path)
        self.current_key = None
        self.checkpoint()
        self.refresh()
        self.status.set("；".join(project.notices) + "  进度自动保存在本机；可用“保存副本”迁移。")

    def sample(self):
        if self.guard(False):
            try:
                self.set_project(Project.from_mod(asset_root() / "examples" / "DemoMod"))
            except (ProjectError, OSError) as exc:
                self.fail(exc)

    def import_mod(self):
        if not self.guard(False):
            return
        folder = filedialog.askdirectory(title="选择含 manifest.json 的单个 mod 文件夹")
        if folder:
            try:
                self.set_project(Project.from_mod(Path(folder)))
            except (ProjectError, OSError) as exc:
                self.fail(exc)

    def update_mod(self):
        if not self.guard():
            return
        folder = filedialog.askdirectory(title="选择同一 mod 更新后的文件夹（UniqueID 必须相同）")
        if folder:
            try:
                self._commit()
                self.project.merge_update(Project.from_mod(Path(folder)))
                self.current_key = None
                self.checkpoint()
                self.refresh()
                self.status.set(self.project.notices[0])
            except (ProjectError, OSError) as exc:
                self.fail(exc)

    def open_project(self):
        if not self.guard(False):
            return
        path = filedialog.askopenfilename(title="打开 ModLingo 项目", filetypes=[("项目文件", "*.json")])
        if path:
            try:
                self._commit()
                self.set_project(Project.load(Path(path)), Path(path))
            except (ProjectError, OSError) as exc:
                self.fail(exc)

    def save_copy(self):
        if not self.guard():
            return
        self._commit()
        path = filedialog.asksaveasfilename(title="保存可迁移的项目副本", defaultextension=".json", initialfile="modlingo-project.json")
        if path:
            try:
                if Path(path).name.casefold() in ("default.json", "zh.json", "manifest.json"):
                    raise ProjectError("项目副本不能覆盖 mod 的语言文件或 manifest.json，请换一个文件名")
                self.project.save(Path(path))
                self.status.set("项目副本已保存（含译文和术语，不含 API 密钥）。")
            except (ProjectError, OSError) as exc:
                self.fail(exc)

    def refresh(self):
        if not self.project:
            return
        self._commit()
        selected = self.current_key
        self.current_key = None
        self.tree.delete(*self.tree.get_children())
        self.visible_keys = []
        search = self.search.get().casefold()
        states = CounterStates(self.project)
        self.summary.set(f"{self.project.name}  ·  {self.project.version}    |    共 {len(self.project.entries)} 项  ·  可导出 {states['ready']}  ·  待处理 {states['todo']}  ·  原文回退 {states['fallback']}  ·  暂不支持 {states['unsupported']}")
        for key, entry in self.project.entries.items():
            status = entry.status()
            mode = self.filter.get()
            if mode == "待翻译" and not entry.needs_translation():
                continue
            if mode == "需要处理" and status not in ("待翻译", "更新待确认", "译文待确认", "检查失败"):
                continue
            if mode == "可导出" and status not in ("可导出", "人工已确认"):
                continue
            if mode == "暂不支持" and status != "暂不支持":
                continue
            if mode == "原文回退" and status != "原文回退":
                continue
            if search and search not in (key + entry.source + entry.target).casefold():
                continue
            index = str(len(self.visible_keys))
            self.visible_keys.append(key)
            self.tree.insert("", "end", iid=index, values=(status, entry.source.replace("\n", " ↵ ")[:100] or key))
        if selected in self.visible_keys:
            iid = str(self.visible_keys.index(selected))
        else:
            iid = "0" if self.visible_keys else None
        if iid:
            self.tree.selection_set(iid)
            self.tree.see(iid)
            self.select_entry()
        else:
            self._set_text(self.source_text, "", disabled=True)
            self._set_text(self.target_text, "", disabled=self.busy)
            self.key_label.configure(text="没有匹配条目")
            self.details.set("")

    def _set_text(self, widget, value, disabled=False):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        if disabled:
            widget.configure(state="disabled")

    def select_entry(self, event=None):
        selection = self.tree.selection()
        if not selection or not self.project:
            return
        key = self.visible_keys[int(selection[0])]
        if key == self.current_key:
            return
        self._commit()
        self.current_key = key
        entry = self.project.entries[key]
        self.key_label.configure(text=key)
        self._set_text(self.source_text, entry.source, disabled=True)
        self._set_text(self.target_text, entry.target, disabled=self.busy)
        notes = [entry.status()]
        if entry.error:
            notes.append(entry.error)
        if unsupported_reason(entry.source):
            notes.append(unsupported_reason(entry.source))
        elif entry.target:
            notes.extend(validate_translation(entry.source, entry.target))
            notes.extend(glossary_warnings(entry.source, entry.target, self.project.glossary))
        if entry.previous_source:
            notes.extend(["旧原文：" + entry.previous_source[:180], "旧译文：" + entry.previous_target[:180]])
        notes.append("清空并保存此条：导出时省略，使用默认语言。" if not unsupported_reason(entry.source) else "此项在导出报告中列出，不写入中文文件。")
        self.details.set("\n".join(notes))

    def confirm_entry(self):
        if not self.guard() or not self.current_key:
            return
        issues = self._commit(force=True)
        self.status.set("已保存，但仍需修复：" + "；".join(issues) if issues else "此条已保存并确认。")
        self.refresh()

    def settings(self):
        if not self.guard(False):
            return
        window = tk.Toplevel(self.root)
        window.title("大模型 API 设置")
        window.geometry("650x420")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=24)
        frame.pack(fill="both", expand=True)
        fields = []
        for label, value, secret in [("服务基础地址（也可填完整 chat/completions 地址）", self.config.base_url, False), ("模型名称（从服务商控制台复制）", self.config.model, False), ("API 密钥（仅本次运行有效）", self.config.api_key, True)]:
            ttk.Label(frame, text=label).pack(anchor="w", pady=(6, 4))
            var = tk.StringVar(value=value)
            ttk.Entry(frame, textvariable=var, show="•" if secret else "").pack(fill="x")
            fields.append(var)
        ttk.Label(frame, text="支持 Chat Completions 格式的文本接口。示例服务地址：\nhttps://api.deepseek.com；模型名称请以服务商最新说明为准。\n远程服务须使用 HTTPS；本机模型可使用 HTTP。\n调用会发送待译文本、翻译键及相关术语，并可能产生费用。", wraplength=590, foreground="#785d2c").pack(anchor="w", pady=18)

        def config():
            return APIConfig(*(var.get().strip() for var in fields))

        def save(test=False):
            try:
                candidate = config()
                candidate.endpoint()
                self.config = candidate
                window.destroy()
                self.status.set("API 配置已应用。密钥仅在内存中；关闭程序后需重新填写。")
                if test:
                    self.test_connection()
            except ProjectError as exc:
                messagebox.showerror("配置待修正", str(exc), parent=window)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="应用并测试连接", command=lambda: save(True)).pack(side="left")
        ttk.Button(buttons, text="应用", command=save, style="Accent.TButton").pack(side="right")

    def _set_busy(self, busy):
        self.busy = busy
        self.translate_button.configure(state="disabled" if busy else "normal")
        self.confirm_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.target_text.configure(state="disabled" if busy else "normal")

    def test_connection(self):
        if not messagebox.askyesno("测试连接", "将发送一条 Hello, farmer! 测试文本，可能产生少量费用。继续？", parent=self.root):
            return
        self.stop_event.clear()
        self._set_busy(True)
        config = replace(self.config)

        def worker():
            try:
                Translator(config).translate([("connection-test", "Hello, farmer!")], {})
                self.events.put(("test", "连接和响应格式检查通过；不代表全部 mod 文本的翻译质量。"))
            except ProjectError as exc:
                self.events.put(("test", str(exc)))
            finally:
                self.events.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def translate(self):
        if not self.guard():
            return
        self._commit()
        items = [(key, entry.source) for key, entry in self.project.entries.items() if entry.needs_translation()]
        if not items:
            messagebox.showinfo("无需调用 API", "没有需要翻译的支持项。已有候选请人工确认；未变文本会直接复用。", parent=self.root)
            return
        try:
            self.config.endpoint()
        except ProjectError as exc:
            self.fail(exc)
            self.settings()
            return
        host = urlparse(self.config.base_url).hostname
        if not messagebox.askyesno("开始大模型翻译", f"将向 {host} 的 {self.config.model} 模型发送 {len(items)} 条待译文本、翻译键及相关术语。\n约 {sum(len(s) for _, s in items)} 个原文字符（不是计费 token 数）。\n费用由你的服务账号承担；失败重试也可能计费。\n\n现有未变译文不会重发。是否开始？", parent=self.root):
            return
        self.stop_event.clear()
        self._set_busy(True)
        self.progress.configure(maximum=len(items), value=0)
        config, glossary = replace(self.config), dict(self.project.glossary)

        def worker():
            translator = Translator(config)
            completed = 0
            try:
                for batch in batches(items):
                    if self.stop_event.is_set():
                        break
                    try:
                        result = translator.translate(batch, glossary)
                        self.events.put(("results", result))
                    except ProjectError as exc:
                        self.events.put(("errors", ([key for key, _ in batch], str(exc))))
                        # Avoid charging every remaining batch for a bad config.
                        break
                    completed += len(batch)
                    self.events.put(("progress", (completed, len(items))))
            finally:
                self.events.put(("done", None))

        self.status.set("正在请求大模型；可停止后续请求，当前请求会等待返回或超时。")
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "results":
                    for key, source, target in data:
                        self.project.accept_ai(key, source, target)
                    self.checkpoint()
                    self.refresh()
                elif kind == "errors":
                    keys, error = data
                    for key in keys:
                        self.project.entries[key].error = error
                    self.checkpoint()
                    self.status.set("请求已停止：" + error + "。修正后可再次翻译，已有成功进度保留。")
                    self.refresh()
                elif kind == "progress":
                    self.progress.configure(value=data[0])
                    self.status.set(f"已处理 {data[0]} / {data[1]} 条。每批结果已自动保存。")
                elif kind == "test":
                    self.status.set(data)
                elif kind == "done":
                    self._set_busy(False)
                    self.refresh()
                    self.status.set(self.status.get() + "  本次处理已结束。")
        except queue.Empty:
            pass
        except (ProjectError, OSError) as exc:
            self.stop_event.set()
            self.fail(exc)
        self.root.after(100, self._poll)

    def glossary_dialog(self):
        if not self.guard():
            return
        self._commit()
        window = tk.Toplevel(self.root)
        window.title("项目术语表")
        window.geometry("580x480")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="每行填写：英文术语=中文译名\n例如：Starfruit=杨桃\n术语会随项目保存，用于后续翻译和复核提示。", justify="left").pack(anchor="w", pady=(0, 12))
        text = ScrolledText(frame, font=("Microsoft YaHei UI", 11), wrap="word")
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(key + "=" + value for key, value in self.project.glossary.items()))

        def save():
            glossary, seen = {}, set()
            try:
                for line in text.get("1.0", "end").splitlines():
                    if not line.strip():
                        continue
                    left, separator, right = line.partition("=")
                    left, right = left.strip(), right.strip()
                    if not separator or not left or not right or len(left) > 120 or len(right) > 120 or left.casefold() in seen:
                        raise ProjectError("每行须为不重复的 英文=中文；左右各不超过 120 字符")
                    seen.add(left.casefold())
                    glossary[left] = right
                if len(glossary) > 1000:
                    raise ProjectError("首版术语表最多 1000 条")
                self.project.glossary = glossary
                self.checkpoint()
                window.destroy()
                self.refresh()
                self.status.set("术语已保存；已有译文不会自动改写，请根据提示检查。")
            except (ProjectError, OSError) as exc:
                messagebox.showerror("术语表待修正", str(exc), parent=window)

        ttk.Button(frame, text="保存术语", command=save, style="Accent.TButton").pack(anchor="e", pady=(12, 0))

    def export(self):
        if not self.guard():
            return
        self._commit()
        path = filedialog.asksaveasfilename(title="导出只含翻译文件的 ZIP", initialfile="translation-zh.zip", defaultextension=".zip")
        if path:
            try:
                report = self.project.export_zip(Path(path))
                self.status.set(f"翻译包已导出：{report['exported']} 项；跳过 {len(report['skipped'])} 项。安装前请备份现有 zh.json。")
                messagebox.showinfo("导出完成", "ZIP 包内包含安装说明和跳过项报告。\n请按 UniqueID 找到原 mod，备份已有 zh.json 后安装。\n仍需要在游戏中检查显示与含义。", parent=self.root)
            except (ProjectError, OSError) as exc:
                self.fail(exc)

    def install_help(self):
        messagebox.showinfo("安装与恢复", "1. 退出游戏，找到原 mod（核对 manifest.json 中的 UniqueID）。\n2. 如已有 i18n/zh.json，复制到别处备份。\n3. 把 ZIP 内的 zh.json 放进原 mod 的 i18n 文件夹。\n4. 选择游戏中文并启动 SMAPI，检查相关文本。\n5. 恢复时复制回备份；原先没有中文文件则删除新增的 zh.json。\n\n翻译包不是独立 mod。不要覆盖 default.json。\n暂不支持的文本会继续使用默认语言。", parent=self.root)

    def fail(self, exc):
        self.status.set(str(exc))
        messagebox.showerror("需要处理", str(exc), parent=self.root)

    def close(self):
        if self.busy and not messagebox.askyesno("退出工作台", "当前请求可能仍在服务端处理并计费。退出会保留已保存进度，但不会保存尚未返回的响应。继续退出？", parent=self.root):
            return
        self.stop_event.set()
        self._commit()
        self.checkpoint()
        self.root.destroy()


def CounterStates(project):
    statuses = [entry.status() for entry in project.entries.values()]
    ready = sum(status in ("可导出", "人工已确认") for status in statuses)
    unsupported = statuses.count("暂不支持")
    fallback = statuses.count("原文回退")
    return {"ready": ready, "unsupported": unsupported, "fallback": fallback,
            "todo": len(statuses) - ready - unsupported - fallback}


def main():
    parser = argparse.ArgumentParser(description="ModLingo 星露谷 mod 翻译工作台")
    parser.add_argument("--smoke", action="store_true", help="无网络 GUI 启动检查")
    parser.add_argument("--editor", action="store_true", help="打开单 mod 编辑器")
    args = parser.parse_args()
    enable_dpi_awareness()
    root = tk.Tk()
    if args.smoke:
        app = App(root)
        root.withdraw()
        # The smoke check deliberately does not write to a user's projects.
        app.project = Project.from_mod(asset_root() / "examples" / "DemoMod")
        app.refresh()
        root.update()
        assert len(app.tree.get_children()) == len(app.project.entries)
        assert "{{PlayerName}}" in app.project.entries["dialogue.greeting"].source
        for callback in root.tk.call('after', 'info'):
            root.after_cancel(callback)
        root.destroy()
        from .library_gui import LibraryApp
        root = tk.Tk()
        root.withdraw()
        library = LibraryApp(root, auto=False)
        root.update()
        assert not library.items and not library.busy
        root.destroy()
        print("GUI smoke passed: Chinese UI, example import and entry selection; no network calls.")
    else:
        if args.editor:
            App(root)
        else:
            from .library_gui import LibraryApp
            LibraryApp(root)
        root.mainloop()
