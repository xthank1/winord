# 划词翻译 (Winrod)

轻量 Windows 划词翻译工具 — 选中英文 → Ctrl+C → 译文自动弹出。

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

## 功能

- **划词即译** — 选中任意英文文本，Ctrl+C 复制，悬浮窗自动显示中文翻译
- **多引擎竞速** — Bing / DeepL / MyMemory 并行请求，取最快返回结果
- **桌面悬浮窗** — 歌词式半透明悬浮窗，始终置顶，不遮挡工作区
- **分层窗口渲染** — 基于 Windows UpdateLayeredWindow + PIL 逐像素 Alpha，文字无描边
- **系统托盘** — 最小化到托盘，右键退出
- **操作手势** — 拖拽移动 | 拖拽右缘调宽度 | 右键锁定/解锁 | 滚轮切换颜色 | 双击复制译文 | Esc 隐藏

## 截图

```
  ┌─────────────────────────────────────────────┐
  │  这是翻译结果文本，悬浮在桌面上方            │
  └─────────────────────────────────────────────┘
```

## 安装

```bash
# 克隆仓库
git clone https://github.com/xthank1/winord.git
cd winord

# 安装依赖
pip install -r requirements.txt
```

### 可选：配置 DeepL API

```bash
# 免费注册: https://www.deepl.com/pro-api
set DEEPL_API_KEY=your-api-key-here
```

## 使用

```bash
python translate.py
```

或双击 `run.bat`（自动安装依赖并启动）。

| 操作 | 说明 |
|------|------|
| 选中英文 + Ctrl+C | 自动翻译 |
| 拖拽悬浮窗 | 移动位置 |
| 拖拽右边缘 | 调整宽度 |
| 右键悬浮窗 | 锁定/解锁（锁定后不跟踪剪贴板） |
| 滚轮 | 切换文字颜色 |
| 双击 | 复制译文 |
| Esc | 隐藏悬浮窗 |
| 托盘右键 | 退出程序 |

## 翻译引擎

| 引擎 | 说明 |
|------|------|
| Bing | 免费，默认启用（需 `translators` 库） |
| DeepL | 翻译质量最佳，需配置 API Key |
| MyMemory | 免费备用，无需配置 |

## 依赖

- [pyperclip](https://github.com/asweigart/pyperclip) — 剪贴板访问
- [translators](https://github.com/UlionTse/translators) — 必应翻译
- [requests](https://github.com/psf/requests) — HTTP 请求
- [Pillow](https://github.com/python-pillow/Pillow) — 图像渲染

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller translate.spec
```

输出在 `dist/划词翻译/` 目录。

## 许可证

MIT
