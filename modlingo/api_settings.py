"""Shared provider-aware API dialog for the launcher and the single-mod editor."""
from dataclasses import replace
import tkinter as tk
from tkinter import messagebox, ttk

from .core import ProjectError
from .provider import APIConfig, PRESETS, PROTOCOLS, preset_config
from .theme import COLORS, FarmBanner, fit_window


class APISettingsDialog:
    def __init__(self, parent, config, on_apply, on_test):
        self.on_apply, self.on_test = on_apply, on_test
        self.original = replace(config)
        self.window = tk.Toplevel(parent)
        self.window.title('API 设置 · 厂商与协议')
        self.window.configure(bg=COLORS['page'])
        self.window.transient(parent)
        fit_window(self.window, 680, 700, minimum=(530, 450))
        FarmBanner(self.window, title='翻译 API', subtitle='选择厂商，再连接你的模型服务', compact=True).pack(fill='x')
        footer = ttk.Frame(self.window, padding=(16, 12))
        footer.pack(side='bottom', fill='x')
        ttk.Button(footer, text='保存并测试（可能计费）', command=lambda: self.save(True)).pack(side='left')
        ttk.Button(footer, text='保存设置', command=self.save, style='Accent.TButton').pack(side='right')
        body = ttk.Frame(self.window)
        body.pack(fill='both', expand=True)
        canvas = tk.Canvas(body, background=COLORS['page'], highlightthickness=0)
        bar = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        frame = ttk.Frame(canvas, padding=(20, 12))
        anchor = canvas.create_window(0, 0, window=frame, anchor='nw')
        frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        self.window.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units'))
        self.provider = tk.StringVar(value=PRESETS[config.provider].label)
        ttk.Label(frame, text='服务厂商').pack(anchor='w')
        picker = ttk.Combobox(frame, textvariable=self.provider, state='readonly', values=[p.label for p in PRESETS.values()])
        picker.pack(fill='x', pady=(4, 8))
        picker.bind('<<ComboboxSelected>>', self.choose_provider)
        self.note = tk.StringVar()
        self.note_label = ttk.Label(frame, textvariable=self.note, wraplength=560, style='Muted.TLabel')
        self.note_label.pack(anchor='w', pady=(0, 8))
        self.base_url, self.model, self.api_key = tk.StringVar(value=config.base_url), tk.StringVar(value=config.model), tk.StringVar(value=config.api_key)
        for title, var, secret in [('服务基础地址（支持完整接口地址）', self.base_url, False),
                                   ('模型 ID（按服务商控制台填写，不自动选模型）', self.model, False),
                                   ('API 密钥（仅本次运行）', self.api_key, True)]:
            ttk.Label(frame, text=title).pack(anchor='w')
            ttk.Entry(frame, textvariable=var, show='•' if secret else '').pack(fill='x', pady=(4, 9))
        self.protocol = tk.StringVar(value=PROTOCOLS[config.protocol])
        ttk.Label(frame, text='接口协议（厂商预设会自动选择）').pack(anchor='w')
        self.protocol_box = ttk.Combobox(frame, textvariable=self.protocol, values=list(PROTOCOLS.values()), state='readonly')
        self.protocol_box.pack(fill='x', pady=(4, 9))
        self.max_tokens = tk.StringVar(value=str(config.max_output_tokens))
        self.timeout = tk.StringVar(value=str(config.timeout))
        numeric = ttk.Frame(frame)
        numeric.pack(fill='x')
        for title, var in [('最大输出 tokens', self.max_tokens), ('每次请求超时（秒）', self.timeout)]:
            column = ttk.Frame(numeric)
            column.pack(side='left', fill='x', expand=True, padx=(0, 10))
            ttk.Label(column, text=title).pack(anchor='w')
            ttk.Entry(column, textvariable=var).pack(fill='x', pady=(4, 9))
        self.workspace = tk.StringVar(value=config.workspace_id)
        ttk.Label(frame, text='Claude 工作区 ID（仅多工作区密钥需要）').pack(anchor='w')
        self.workspace_entry = ttk.Entry(frame, textvariable=self.workspace)
        self.workspace_entry.pack(fill='x', pady=(4, 9))
        self.disable_thinking = tk.BooleanVar(value=config.disable_thinking)
        self.thinking_box = ttk.Checkbutton(frame, text='关闭思考（仅 DeepSeek / 千问 / Kimi 可用，模型须支持）', variable=self.disable_thinking)
        self.thinking_box.pack(anchor='w', pady=(0, 9))
        self.help_label = ttk.Label(frame, text='只支持非流式文本翻译，不支持必须流式或工具调用的模型。\n输出上限须符合模型限制，它不是费用预算。连接测试也会计费。\n当前支持名单经过协议测试；没有宣称所有厂商已做真实云端验收。',
                                    wraplength=560, style='Muted.TLabel')
        self.help_label.pack(anchor='w')
        def resize(event):
            canvas.itemconfigure(anchor, width=event.width)
            for label in (self.note_label, self.help_label):
                label.configure(wraplength=max(300, event.width-45))
        canvas.bind('<Configure>', resize)
        self.update_controls()
        self.window.grab_set()

    def provider_id(self):
        return next(key for key, preset in PRESETS.items() if preset.label == self.provider.get())

    def choose_provider(self, event=None):
        config = preset_config(self.provider_id())
        self.base_url.set(config.base_url)
        self.model.set('')
        # Never silently reuse one vendor's key on another vendor's host.
        self.api_key.set('')
        self.workspace.set('')
        self.protocol.set(PROTOCOLS[config.protocol])
        self.disable_thinking.set(config.disable_thinking)
        self.update_controls()

    def update_controls(self):
        provider = self.provider_id()
        self.note.set(PRESETS[provider].note)
        self.protocol_box.configure(state='readonly' if provider == 'custom' else 'disabled')
        self.workspace_entry.configure(state='normal' if provider in ('claude', 'custom') else 'disabled')
        self.thinking_box.configure(state='normal' if provider in ('deepseek', 'qwen', 'qwen_intl', 'kimi') else 'disabled')

    def candidate(self):
        try:
            budget, timeout = int(self.max_tokens.get()), float(self.timeout.get())
        except ValueError:
            raise ProjectError('输出上限和超时必须是数字') from None
        protocol = next(key for key, label in PROTOCOLS.items() if label == self.protocol.get())
        config = APIConfig(self.base_url.get().strip(), self.model.get().strip(), self.api_key.get().strip(), timeout,
                           provider=self.provider_id(), protocol=protocol, max_output_tokens=budget,
                           workspace_id=self.workspace.get().strip(), disable_thinking=self.disable_thinking.get())
        config.endpoint()
        return config

    def save(self, test=False):
        try:
            config = self.candidate()
        except ProjectError as exc:
            messagebox.showerror('配置待修正', str(exc), parent=self.window)
            return
        self.on_apply(config)
        self.window.destroy()
        if test:
            self.on_test()
