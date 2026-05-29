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
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import os
import textwrap

# ── HTTP 连接池（复用 TCP/TLS 连接，减少握手开销） ──────

_http_sessions = {}
_HTTP_TIMEOUT = 2.5

def _get_session(host: str) -> requests.Session:
    """获取或创建针对指定主机的持久 Session（连接复用）。"""
    s = _http_sessions.get(host)
    if s is None:
        s = requests.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0'})
        # 连接池：最多 2 个持久连接，适配并发请求
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1, pool_maxsize=2, max_retries=0
        )
        s.mount(f'https://{host}', adapter)
        _http_sessions[host] = s
    return s

try:
    from PIL import Image, ImageDraw, ImageFont
    _LAYERED_OK = sys.platform == 'win32'
except ImportError:
    _LAYERED_OK = False

# ── Windows 分层窗口辅助（逐像素 Alpha，消除描边）──────────

if _LAYERED_OK:
    _WS_EX_LAYERED = 0x00080000
    _GWL_EXSTYLE = -20
    _ULW_ALPHA = 0x00000002
    _AC_SRC_ALPHA = 0x01
    _SWP_FLAGS = 0x0002 | 0x0001 | 0x0004 | 0x0020

    class _BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_byte),
            ("BlendFlags", ctypes.c_byte),
            ("SourceConstantAlpha", ctypes.c_byte),
            ("AlphaFormat", ctypes.c_byte),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER)]

    def _set_layered(hwnd):
        u32 = ctypes.windll.user32
        style = u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_LAYERED)
        u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_FLAGS)

    def _update_layered(hwnd, pil_img, x, y):
        w, h = pil_img.size
        raw = pil_img.tobytes()
        buf = bytearray(len(raw))
        for i in range(0, len(raw), 4):
            r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
            if a:
                r = (r * a) // 255
                g = (g * a) // 255
                b = (b * a) // 255
            buf[i] = b
            buf[i + 1] = g
            buf[i + 2] = r
            buf[i + 3] = a

        u32 = ctypes.windll.user32
        gdi = ctypes.windll.gdi32

        hdc_screen = u32.GetDC(0)
        hdc_mem = gdi.CreateCompatibleDC(hdc_screen)

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32

        pbits = ctypes.c_void_p()
        hbmp = gdi.CreateDIBSection(
            hdc_mem, ctypes.byref(bmi), 0,
            ctypes.byref(pbits), None, 0
        )
        ctypes.memmove(pbits, bytes(buf), len(buf))

        old_bmp = gdi.SelectObject(hdc_mem, hbmp)

        bf = _BLENDFUNCTION()
        bf.BlendOp = 0
        bf.SourceConstantAlpha = 255
        bf.AlphaFormat = _AC_SRC_ALPHA

        pt_src = _POINT(0, 0)
        pt_dst = _POINT(x, y)
        sz = _SIZE(w, h)

        u32.UpdateLayeredWindow(
            hwnd, hdc_screen, ctypes.byref(pt_dst), ctypes.byref(sz),
            hdc_mem, ctypes.byref(pt_src), 0, ctypes.byref(bf), _ULW_ALPHA
        )

        gdi.SelectObject(hdc_mem, old_bmp)
        gdi.DeleteObject(hbmp)
        gdi.DeleteDC(hdc_mem)
        u32.ReleaseDC(0, hdc_screen)

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


def _prewarm_connections():
    """后台线程：预热 HTTP 连接，提前完成 TCP+TLS 握手。"""
    try:
        # 发送极小翻译请求预热 MyMemory
        session = _get_session('api.mymemory.translated.net')
        session.get(
            'https://api.mymemory.translated.net/get',
            params={'q': 'hi', 'langpair': 'en|zh-CN'},
            timeout=5,
        )
    except Exception:
        pass

# ── 翻译结果缓存 ──────────────────────────────────────────

_cache = {}
_cache_keys = []
_CACHE_MAX = 128


def translate(text):
    """
    翻译英文 → 中文。
    并行竞速：必应 / MyMemory 同时发起，取最快返回的有效结果。
    """
    text = text.strip()
    if not text:
        return ""

    # 空白归一化后查缓存，提高命中率
    normalized = ' '.join(text.split())
    cached = _cache.get(normalized)
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

    if not backends:
        raise Exception("没有可用的翻译后端")

    pool = ThreadPoolExecutor(max_workers=len(backends))
    futures = {}
    errors = []

    try:
        for name, fn in backends:
            futures[pool.submit(fn)] = name

        for fut in as_completed(futures, timeout=5):
            name = futures[fut]
            try:
                result = fut.result()
                if result:
                    _add_to_cache(normalized, result)
                    # 取消其余仍在等待的后端请求
                    for f in futures:
                        if f is not fut and not f.done():
                            f.cancel()
                    return result
            except Exception as e:
                errors.append(f"{name}: {e}")
    except TimeoutError:
        pass
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
    """MyMemory 免费翻译 API（连接池复用）。"""
    session = _get_session('api.mymemory.translated.net')
    resp = session.get(
        'https://api.mymemory.translated.net/get',
        params={'q': text, 'langpair': 'en|zh-CN'},
        timeout=_HTTP_TIMEOUT,
    )
    data = resp.json()
    if data.get('responseStatus') == 200:
        result = data['responseData']['translatedText'].strip()
        if result:
            return result
    raise Exception("MyMemory 返回空结果")


# ── 悬浮窗 UI ─────────────────────────────────────────────

class LyricsOverlay:
    """桌面歌词风格的翻译悬浮窗。

    Windows 分层窗口 + PIL 逐像素 Alpha 渲染，文字边缘干净无描边/阴影。
    """

    DST_COLORS = [
        '#FFE501',  # 黄色
        '#229712',  # 深绿
        '#5F10DD',  # 深蓝
        '#FF3C88',  # 深粉
    ]

    def __init__(self):
        if not _LAYERED_OK:
            print("=" * 50)
            print("  缺少依赖: Pillow")
            print("  请运行: pip install Pillow")
            print("=" * 50)
            input("按回车退出...")
            sys.exit(1)

        self._color_idx = 0
        self._dst_colors = self.DST_COLORS
        self._hover = False

        self._pad_x = 12
        self._pad_y = 8

        # 获取屏幕尺寸
        tmp = tk.Tk()
        tmp.withdraw()
        sw = tmp.winfo_screenwidth()
        sh = tmp.winfo_screenheight()
        tmp.destroy()

        self._win_w = 580
        self._win_h = 50
        self._x = (sw - self._win_w) // 2
        self._y = sh - self._win_h - 110

        # 创建主窗口
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.configure(bg='#000000')
        self.root.geometry(f"{self._win_w}x{self._win_h}+{self._x}+{self._y}")

        # 设为分层窗口
        self.root.update_idletasks()
        self._hwnd = int(self.root.frame(), 16)
        _set_layered(self._hwnd)

        # 加载楷体
        self._pil_font = self._load_font()
        self._current_text = ""

        # 状态
        self._last_text = ""
        self._busy = False
        self._running = True

        # 事件绑定
        self._bind_events()

        # 后台任务
        threading.Thread(target=self._clipboard_watch, daemon=True).start()
        threading.Thread(target=_load_bing_engine, daemon=True).start()
        threading.Thread(target=_prewarm_connections, daemon=True).start()

        # 初始显示
        self._show("划词翻译就绪 · 复制英文即可翻译")
        self.show()
        self._keep_on_top()

    def _load_font(self):
        windir = os.environ.get('WINDIR', 'C:/Windows')
        paths = [
            f"{windir}/Fonts/simkai.ttf",
            f"{windir}/Fonts/kaiu.ttf",
            f"{windir}/Fonts/STKAITI.TTF",
        ]
        for path in paths:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, 16)
                except Exception:
                    pass
        return ImageFont.load_default()

    # ── 事件绑定 ──────────────────────────────────────────

    def _bind_events(self):
        self.root.bind('<Button-1>', self._drag_start)
        self.root.bind('<B1-Motion>', self._drag)
        self.root.bind('<Button-3>', self.hide)
        self.root.bind('<Escape>', self.hide)
        self.root.bind('<MouseWheel>', self._on_scroll)
        self.root.bind('<Double-Button-1>', self._copy_dst)
        self.root.bind('<Enter>', self._on_enter)
        self.root.bind('<Leave>', self._on_leave)

    # ── PIL 文字渲染 ────────────────────────────────────

    def _render(self, text, color=None, hover=False):
        if color is None:
            color = self._dst_colors[self._color_idx]

        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        font = self._pil_font
        max_w = self._win_w - self._pad_x * 2

        # 逐字符换行
        dummy = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        lines = []
        cur = ""
        for ch in text:
            test = cur + ch
            bb = dummy.textbbox((0, 0), test, font=font)
            if bb[2] - bb[0] > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        if cur:
            lines.append(cur)

        wrapped = '\n'.join(lines)

        bb = dummy.multiline_textbbox((0, 0), wrapped, font=font, spacing=4)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]

        iw = max(tw + self._pad_x * 2, self._win_w)
        ih = max(th + self._pad_y * 2, 30)
        ih = min(ih, 300)

        img = Image.new('RGBA', (iw, ih), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if hover:
            draw.rectangle(
                [(0, 0), (iw - 1, ih - 1)],
                fill=(100, 180, 255, 50)
            )

        draw.multiline_text(
            (self._pad_x, self._pad_y), wrapped,
            font=font, fill=(r, g, b, 255), spacing=4
        )
        return img, iw, ih

    # ── 显示控制 ────────────────────────────────────────

    def _show(self, text):
        self._current_text = text
        img, w, h = self._render(text, hover=self._hover)
        self.root.geometry(f"{w}x{h}")
        _update_layered(self._hwnd, img, self._x, self._y)

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def hide(self, event=None):
        self.root.withdraw()
        self._last_text = ""
        self._busy = False

    def _keep_on_top(self):
        if not self._running:
            return
        try:
            self.root.lift()
        except Exception:
            pass
        self.root.after(300, self._keep_on_top)

    # ── 拖拽移动 ─────────────────────────────────────────

    def _drag_start(self, event):
        self._dx = event.x_root - self._x
        self._dy = event.y_root - self._y

    def _drag(self, event):
        self._x = event.x_root - self._dx
        self._y = event.y_root - self._dy
        self.root.geometry(f"+{self._x}+{self._y}")

    # ── 滚轮切换颜色 ────────────────────────────────────

    def _on_scroll(self, event):
        if event.delta > 0:
            self._color_idx = (self._color_idx + 1) % len(self._dst_colors)
        else:
            self._color_idx = (self._color_idx - 1) % len(self._dst_colors)
        if self._current_text:
            img, _, _ = self._render(self._current_text, hover=self._hover)
            _update_layered(self._hwnd, img, self._x, self._y)

    def _on_enter(self, event):
        self._hover = True
        if self._current_text:
            img, _, _ = self._render(self._current_text, hover=True)
            _update_layered(self._hwnd, img, self._x, self._y)

    def _on_leave(self, event):
        self._hover = False
        if self._current_text:
            img, _, _ = self._render(self._current_text, hover=False)
            _update_layered(self._hwnd, img, self._x, self._y)

    # ── 复制操作 ─────────────────────────────────────────

    def _copy_dst(self, event=None):
        if self._current_text:
            pyperclip.copy(self._current_text)
            self._flash_copy()

    def _flash_copy(self):
        saved = self._dst_colors[self._color_idx]
        img, _, _ = self._render(self._current_text, '#ff5252')
        _update_layered(self._hwnd, img, self._x, self._y)

        def reset():
            img2, _, _ = self._render(self._current_text, saved)
            _update_layered(self._hwnd, img2, self._x, self._y)
        self.root.after(600, reset)

    # ── 翻译逻辑 ─────────────────────────────────────────

    def translate(self, text):
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

        self._show("翻译中...")
        self.show()

        threading.Thread(target=self._run_translate, args=(text,), daemon=True).start()

    def _run_translate(self, text):
        try:
            result = translate(text)
        except Exception:
            result = text
        self.root.after(0, self._on_result, result)

    def _on_result(self, result):
        self._show(result)
        self._busy = False

    # ── 剪贴板监听 ─────────────────────────────────────

    def _clipboard_watch(self):
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
                        self.root.after(0, self.translate, cur)
            except Exception:
                pass
            time.sleep(0.2)

    # ── 生命周期 ───────────────────────────────────────

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._stop)
        self.root.mainloop()

    def _stop(self):
        self._running = False
        self.root.destroy()


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
