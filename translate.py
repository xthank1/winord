#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
划词翻译 — 轻量 Windows 划词翻译工具
Bing Translate | English → 中文 | 桌面歌词式悬浮窗

使用方法: 选中任意英文文本 → Ctrl+C 复制 → 译文自动弹出
"""

import tkinter as tk
import threading
import time
import re
import sys
import json
import ssl
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 依赖检查 ──────────────────────────────────────────────

try:
    import pyperclip
except ImportError:
    print("=" * 50)
    print("  缺少依赖: pyperclip")
    print("  请运行: pip install pyperclip requests")
    print("=" * 50)
    input("按回车退出...")
    sys.exit(1)

# ── 必应翻译引擎（延迟加载，避免阻塞启动） ────────────────

_bing_translator = None
_bing_ready = False
_bing_lock = threading.Lock()

def _load_bing_engine():
    """后台线程：加载必应翻译引擎。"""
    global _bing_translator, _bing_ready
    try:
        from translators import translate_text as _t
        _bing_translator = _t
        _bing_ready = True
        print("[OK] 必应翻译引擎就绪")
    except ImportError:
        print("[!] 未安装 translators，使用备用翻译")
        print("    安装: pip install translators")
    except Exception as e:
        print(f"[!] 必应引擎加载失败: {e}")


# ── 翻译结果缓存 ──────────────────────────────────────────

_cache = {}
_cache_keys = []
_CACHE_MAX = 128


def translate(text):
    """
    翻译英文 → 中文。
    并行竞速：必应 / MyMemory / Google 同时发起，取最快返回的有效结果。
    """
    text = text.strip()
    if not text:
        return ""

    # 命中缓存直接返回
    cached = _cache.get(text)
    if cached is not None:
        return cached

    # 构建后端列表，每个是一个 (name, callable) 对
    backends = []

    if _bing_ready and _bing_translator:
        def _do_bing():
            result = _bing_translator(
                text, translator='bing',
                from_language='en', to_language='zh'
            )
            if result and result.strip():
                return result.strip()
            raise Exception("必应返回空结果")
        backends.append(("bing", _do_bing))

    backends.append(("mymemory", lambda: _translate_mymemory(text)))
    backends.append(("google",  lambda: _translate_google(text)))

    if not backends:
        raise Exception("没有可用的翻译后端")

    pool = ThreadPoolExecutor(max_workers=len(backends))
    futures = {}
    errors = []

    try:
        for name, fn in backends:
            futures[pool.submit(fn)] = name

        for fut in as_completed(futures):
            name = futures[fut]
            try:
                result = fut.result()
                if result:
                    _add_to_cache(text, result)
                    return result
            except Exception as e:
                errors.append(f"{name}: {e}")
    finally:
        pool.shutdown(wait=False)

    raise Exception("所有翻译方式均失败，请检查网络连接")


def _add_to_cache(key: str, value: str) -> None:
    """将 key→value 加入 LRU 风格的缓存。"""
    _cache[key] = value
    _cache_keys.append(key)
    if len(_cache_keys) > _CACHE_MAX:
        stale = _cache_keys.pop(0)
        _cache.pop(stale, None)


def _translate_mymemory(text):
    """MyMemory 免费翻译 API。"""
    params = urllib.parse.urlencode({
        'q': text,
        'langpair': 'en|zh-CN'
    })
    url = f"https://api.mymemory.translated.net/get?{params}"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        if data.get('responseStatus') == 200:
            result = data['responseData']['translatedText'].strip()
            if result:
                return result
    raise Exception("MyMemory 返回空结果")


def _translate_google(text):
    """Google Translate 免费 API。"""
    params = urllib.parse.urlencode({
        'client': 'gtx',
        'sl': 'en',
        'tl': 'zh-CN',
        'dt': 't',
        'q': text,
    })
    url = f"https://translate.googleapis.com/translate_a/single?{params}"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://translate.google.com/',
    })
    with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        # API returns: [[["translated","original",...]], ...]
        if data and data[0]:
            parts = [s[0] for s in data[0] if s[0]]
            result = ''.join(parts).strip()
            if result:
                return result
    raise Exception("Google 返回空结果")


# ── 悬浮窗 UI ─────────────────────────────────────────────

class LyricsOverlay:
    """桌面歌词风格的翻译悬浮窗 — 仅显示译文，楷体，滚轮换色。"""

    TRANSP_COLOR = '#010203'   # 文本窗口透明色，不与文字色冲突

    # 滚轮循环的译文字体颜色
    DST_COLORS = [
        '#222222', '#CC0000', '#0066CC', '#339933',
        '#CC6600', '#9933CC', '#008B8B', '#CC3366',
    ]

    def __init__(self):
        self._color_idx = 0
        self._dst_colors = self.DST_COLORS

        # ── 背景窗口（完全透明） ──
        self.bg = tk.Tk()
        self.bg.withdraw()
        self.bg.overrideredirect(True)
        self.bg.attributes('-topmost', True)
        self.bg.attributes('-alpha', 0.0)
        self.bg.configure(bg='#000000')

        # 尺寸与位置
        self.win_w = 580
        self.win_h = 50
        sw = self.bg.winfo_screenwidth()
        sh = self.bg.winfo_screenheight()
        self._x = (sw - self.win_w) // 2
        self._y = sh - self.win_h - 110
        self.bg.geometry(f"{self.win_w}x{self.win_h}+{self._x}+{self._y}")

        # ── 文本窗口（透明背景 + 不透明文字） ──
        self.txt = tk.Toplevel(self.bg)
        self.txt.withdraw()
        self.txt.overrideredirect(True)
        self.txt.attributes('-topmost', True)
        self.txt.attributes('-transparentcolor', self.TRANSP_COLOR)
        self.txt.configure(bg=self.TRANSP_COLOR)
        self.txt.geometry(f"{self.win_w}x{self.win_h}+{self._x}+{self._y}")

        # ── 状态 ──
        self._last_text = ""
        self._busy = False
        self._running = True

        # ── 构建界面、绑定事件 ──
        self._build_ui()
        self._bind_events()

        # ── 启动后台任务 ──
        threading.Thread(target=self._clipboard_watch, daemon=True).start()
        threading.Thread(target=_load_bing_engine, daemon=True).start()

        # ── 初始显示 ──
        self._show("划词翻译就绪 · 复制英文即可翻译")
        self.show()

    # ── UI 构建 ──────────────────────────────────────────

    def _build_ui(self):
        """创建界面控件 — 仅译文标签，楷体，整个区域可拖拽。"""
        tc = self.TRANSP_COLOR

        self.lbl_dst = tk.Label(
            self.txt, text="",
            font=("KaiTi", 16), fg=self._dst_colors[0],
            bg=tc, anchor=tk.W, justify=tk.LEFT,
            wraplength=self.win_w - 24,
            cursor="fleur"
        )
        self.lbl_dst.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

    # ── 事件绑定 ──────────────────────────────────────────

    def _bind_events(self):
        # 整个译文标签可拖拽
        self.lbl_dst.bind('<Button-1>', self._drag_start)
        self.lbl_dst.bind('<B1-Motion>', self._drag)

        def hide_fn(e):
            self.hide()

        for w in (self.bg, self.txt, self.lbl_dst):
            w.bind('<Button-3>', hide_fn)
            w.bind('<Escape>', hide_fn)
            w.bind('<MouseWheel>', self._on_scroll)

        self.lbl_dst.bind('<Double-Button-1>', self._copy_dst)

    # ── 拖拽移动 ─────────────────────────────────────────

    def _drag_start(self, event):
        self._dx = event.x_root
        self._dy = event.y_root

    def _drag(self, event):
        dx = event.x_root - self._dx
        dy = event.y_root - self._dy
        self._x += dx
        self._y += dy
        geo = f"+{self._x}+{self._y}"
        self.bg.geometry(geo)
        self.txt.geometry(geo)
        self._dx = event.x_root
        self._dy = event.y_root

    # ── 滚轮切换文字颜色 ─────────────────────────────────

    def _on_scroll(self, event):
        if event.delta > 0:
            self._color_idx = (self._color_idx + 1) % len(self._dst_colors)
        else:
            self._color_idx = (self._color_idx - 1) % len(self._dst_colors)
        self.lbl_dst.config(fg=self._dst_colors[self._color_idx])

    # ── 复制操作 ─────────────────────────────────────────

    def _copy_dst(self, event=None):
        t = self.lbl_dst.cget('text')
        if t:
            pyperclip.copy(t)
            self._flash_copy()

    # ── 窗口控制 ─────────────────────────────────────────

    def show(self):
        self.bg.deiconify()
        self.txt.deiconify()
        self.bg.lift()
        self.txt.lift()

    def hide(self, event=None):
        self.bg.withdraw()
        self.txt.withdraw()
        self._last_text = ""
        self._busy = False

    def _flash_copy(self):
        """短暂闪烁提示复制成功。"""
        saved = self.lbl_dst.cget('fg')
        self.lbl_dst.config(fg='#ff5252')
        def reset():
            self.lbl_dst.config(fg=saved)
        self.txt.after(600, reset)

    def _show(self, text):
        self.lbl_dst.config(text=text)

    # ── 翻译逻辑 ─────────────────────────────────────────

    def translate(self, text):
        """将剪贴板文本送入翻译队列。"""
        if self._busy:
            return

        text = text.strip()
        if not text:
            return
        if not re.search(r'[a-zA-Z]', text):
            return
        if len(text) > 2000:
            text = text[:2000]
        if text == self._last_text:
            return

        self._last_text = text
        self._busy = True

        # 立即更新 UI
        self.lbl_dst.config(text="翻译中...")
        self._auto_height()
        self.show()

        threading.Thread(target=self._run_translate, args=(text,), daemon=True).start()

    def _run_translate(self, text):
        """后台线程：执行翻译。"""
        try:
            result = translate(text)
        except Exception as e:
            result = f"翻译失败: {e}"
        self.txt.after(0, self._on_result, result)

    def _on_result(self, result):
        """主线程：更新翻译结果。"""
        self.lbl_dst.config(text=result)
        self._busy = False
        self._auto_height()

    def _auto_height(self):
        """自适应窗口高度。"""
        self.txt.update_idletasks()
        needed = self.lbl_dst.winfo_reqheight() + 20
        new_h = max(40, min(300, needed))
        self.bg.geometry(f"{self.win_w}x{new_h}")
        self.txt.geometry(f"{self.win_w}x{new_h}")

    # ── 剪贴板监听 ───────────────────────────────────────

    def _clipboard_watch(self):
        """后台线程：轮询剪贴板变化。"""
        last = ""
        try:
            last = pyperclip.paste()
        except Exception:
            pass

        while self._running:
            try:
                cur = pyperclip.paste()
                if cur and cur != last:
                    last = cur
                    if re.search(r'[a-zA-Z]', cur):
                        self.txt.after(0, self.translate, cur)
            except Exception:
                pass
            time.sleep(0.2)

    # ── 生命周期 ─────────────────────────────────────────

    def run(self):
        self.bg.protocol("WM_DELETE_WINDOW", self._stop)
        self.txt.protocol("WM_DELETE_WINDOW", self._stop)
        self.bg.mainloop()

    def _stop(self):
        self._running = False
        self.bg.destroy()
        self.txt.destroy()


# ── 入口 ──────────────────────────────────────────────────

def main():
    print()
    print("  ╔══════════════════════════════════╗")
    print("  ║    划词翻译 v1.2               ║")
    print("  ║    Bing · English → 中文       ║")
    print("  ╚══════════════════════════════════╝")
    print()
    print("  悬浮窗已显示在屏幕底部中央")
    print("  用法: 选中英文 → Ctrl+C → 自动翻译")
    print("  操作: 拖拽移动 | 右键/Esc隐藏 | 滚轮切换文字颜色")
    print()

    app = LyricsOverlay()
    app.run()


if __name__ == '__main__':
    main()
