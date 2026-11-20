"""Session-only verification gate shared by both translation screens."""
from dataclasses import replace
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .core import ProjectError
from .provider import Translator


def verify_connection(config, stop, transport=None,test_budget=None):
    import asyncio
    from .async_provider import Session, Budget
    async def run():
        if stop.is_set():
            raise ProjectError('验证已停止；已发送请求仍可能计费')
        translator=Translator(config)
        items=[('connection-test','Hello, {{PlayerName}}!\nWelcome to the farm!')]
        messages,prefix,records,restores=translator.prepare(items,{})
        async with Session(config,1,transport=transport,test_budget=test_budget) as session:
            task=asyncio.create_task(session.post(translator,messages,Budget()))
            try:
                while not task.done():
                    if stop.is_set():
                        raise ProjectError('验证已停止；已发送请求仍可能计费')
                    await asyncio.wait({task},timeout=.1)
                return translator.decode(task.result(),items,prefix,records,restores)
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
    return asyncio.run(run())


class APIVerification:
    def init_verification(self):
        self.verified_config = None
        self.verification_generation = 0
        self.api_status = tk.StringVar(value='API 未验证：请配置并测试，通过后才能翻译。')

    def verification_row(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(5, 5))
        self.verify_button = ttk.Button(row, text='验证 API（可能计费）', command=self.test_connection)
        self.verify_button.pack(side='right')
        ttk.Label(row, textvariable=self.api_status, wraplength=540).pack(side='left', fill='x', expand=True)

    def api_ready(self):
        return self.verified_config is not None and self.verified_config == self.config

    def invalidate_api(self):
        self.verified_config = None
        self.verification_generation += 1
        self.api_status.set('API 未验证：设置已更新，请先验证。')
        self.update_api_buttons()

    def update_api_buttons(self):
        button = getattr(self, 'start_button', None) or self.translate_button
        button.configure(state='normal' if not self.busy and self.api_ready() else 'disabled')
        self.verify_button.configure(state='disabled' if self.busy else 'normal')

    def require_verified_api(self):
        if self.api_ready():
            return True
        self.api_status.set('API 未验证：请先配置并通过验证，才能开始翻译。')
        self.update_api_buttons()
        messagebox.showwarning('请先验证 API', self.api_status.get(), parent=self.root)
        return False

    def begin_api_test(self, set_busy):
        if self.busy:
            return
        try:
            self.config.endpoint()
        except ProjectError as exc:
            self.invalidate_api()
            self.api_status.set('API 配置不完整：' + str(exc))
            messagebox.showerror('API 配置不完整', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('验证 API', '将发送一条含占位符及换行的短文本，检查接口、模型、响应格式和简体中文输出。可能产生少量费用。继续？', parent=self.root):
            return
        self.invalidate_api()
        generation = self.verification_generation
        config = replace(self.config)
        stop = getattr(self,'stop',None) or self.stop_event
        stop.clear()
        self.api_status.set('API 验证中：请等待，暂不能开始翻译。')
        set_busy(True)

        def worker():
            error = None
            try:
                result = verify_connection(config,stop)
                if len(result) != 1 or not re.search(r'[\u4e00-\u9fff]', result[0][2]):
                    raise ProjectError('模型没有返回中文译文，请检查模型和服务配置')
            except ProjectError as exc:
                error = str(exc)
            except Exception:
                error = '验证遇到异常，请检查配置后重试（错误正文已隐藏）'
            self.events.put(('api_test', (generation, config, error)))
            self.events.put(('done', None))

        threading.Thread(target=worker, daemon=True).start()

    def finish_api_test(self, data):
        generation, config, error = data
        if generation != self.verification_generation or config != self.config:
            self.api_status.set('API 未验证：配置已变化，请重新验证。')
            return
        if error:
            self.verified_config = None
            self.api_status.set('API 验证失败：' + error)
            messagebox.showerror('API 验证失败', error + '\n修正配置并重新验证后才能翻译。', parent=self.root)
        else:
            self.verified_config = replace(config)
            self.api_status.set('API 验证成功：' + config.model + ' · 可以开始翻译。')
            messagebox.showinfo('API 验证成功', '当前配置已通过接口、译文格式、占位符和中文输出检查。\n可以开始翻译；批量调用仍可能受额度、网络或模型输出影响。', parent=self.root)
        self.status.set(self.api_status.get())
        self.update_api_buttons()
