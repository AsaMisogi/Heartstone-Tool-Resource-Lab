# 公开仓库文件范围

仓库保留可运行、可维护的源码与说明；忽略规则不会删除本机数据。

| 提交 | 原因 |
| --- | --- |
| pengpeng/ 与项目图标 | 应用运行必需的 Python、HTML、CSS、JavaScript 和自有图标 |
| 启动、依赖安装及构建 BAT，launch.py，PyInstaller spec | 源码启动与后续独立版构建 |
| pyproject.toml、requirements.txt、requirements.lock | 包元数据和可复现的依赖版本 |
| tests/ 与通用 tools/ 脚本 | 测试和开发验证；GUI／集成脚本需要本地游戏与桌面 |
| README、LICENSE、THIRD_PARTY_NOTICES、docs/ | 用户说明、许可、架构与功能说明 |
| docs/screenshots/ | 经人工查看的真实界面截图，仅用于说明功能 |

不提交 `.venv/`、`.cache/`、`.pytest_cache/`、`__pycache__/`、`.reference/`、`workspace/`、`build/`、`dist/`、`node_modules/`，以及本机凭据、日志、数据库、导出音频与游戏资源包。`tools/probe.py`、`tools/probe_links.py`、`tools/probe_details.py` 是硬编码本机路径的一次性探查脚本，保留本地并忽略。

`docs/` 中较早的验证说明是开发记录，可能引用未随仓库发布的 `.cache/` 结果；不能据此认为源码包附带模型、索引或已安装依赖。新用户以根目录 README 为准。

## 截图来源

截图直接选自本机已有 GUI 验证产物，未重新合成界面或编造数据。不同截图对应不同开发阶段，局部布局和计数可能不同。

| 公开文件 | 本地原始文件 |
| --- | --- |
| overview.png | .cache/qa-workbench/gallery.png |
| cards.png | .cache/qa-catalog-cards/cards.png |
| welcome.png | .cache/qa-first-run/welcome.png |
| hero-voices.png | .cache/qa-voice-updates/hero-regression.png |
| playback.png | .cache/qa/export.png |
| voice-export.png | .cache/qa-ux/voice-export.png |
| audio-library.png | .cache/qa/audio-library.png |
| artwork.png | .cache/qa/golden.png |
| settings.png | .cache/qa-speech-gui/speech-settings.png |
| effects.png | .cache/qa/effect.png |

界面截图中的游戏美术、台词和商标不属于项目自有素材，MIT 不授予其使用权。仓库不另行附带原始游戏纹理或音频。
