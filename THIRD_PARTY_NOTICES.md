# 第三方来源与声明

## Hermes

参考项目：[Fbigame/Hermes](https://github.com/Fbigame/Hermes)。借鉴其 `cards_map`、`base_assets_catalog`、本地语言 GUID 覆盖以及 CardDef 的解析思路。GUI、索引、进程调度和预览服务为本项目新实现。参考源码仅放在被 Git 忽略的 `.reference` 内。

为保留参考项目的许可与署名，列出原始声明：

```text
ISC License

Copyright (c) 2025 Octasin

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## 运行依赖

直接与传递依赖版本记录在 `requirements.lock`。各自版权和许可证以对应发行包中的原文为准。

- UnityPy：Unity 资源读取；[项目](https://github.com/K0lb3/UnityPy)，MIT。
- PySide6 / Qt：桌面窗口、Qt WebView/WebView2 插件、Chromium WebEngine、WebChannel；[许可说明](https://doc.qt.io/qtforpython-6/licenses.html)。应按所选 LGPL / GPL / 商业许可履行发行义务。
- Microsoft Edge WebView2 Runtime：Windows 原生浏览器运行时，使用系统已安装版本，不随本包复制。参见 [Microsoft WebView2](https://learn.microsoft.com/microsoft-edge/webview2/)。
- python-soundfile / libsndfile：PCM 音频读写；[项目](https://github.com/bastibe/python-soundfile)。Python 包 BSD，libsndfile 使用 LGPL。
- Vosk 0.3.45：[项目与 Apache-2.0 许可](https://github.com/alphacep/vosk-api)。离线模型 `vosk-model-small-cn-0.22`、`vosk-model-small-en-us-0.15` 均由[官方模型目录](https://alphacephei.com/vosk/models)标为 Apache-2.0，构建时下载、随免安装包提供并保留模型原目录文件；不随源码捆绑。识别结果是机器转写，不是客户端字幕。
- fmod_toolkit / pyfmodex：FMOD 解码适配。fmod_toolkit 的 Python 代码采用 MIT；其分发包包含 FMOD 原生 DLL，原生库许可需单独确认，不能用 Python 包的 MIT 覆盖。
- Pillow、NumPy、lz4、brotli、texture2ddecoder、etcpak、astc-encoder-py 等：参见安装包内的 LICENSE / NOTICE。

## 游戏资源

炉石传说的名称、美术、声音、文本、脚本和其他素材属于对应权利人。本项目不提供游戏资源下载、不随源码分发游戏资源；本项目 MIT 许可证仅覆盖自有源码。

## HearthstoneJSON 完整卡面

用户打开“完整卡面”时，按需使用 https://art.hearthstonejson.com/ 图像服务并缓存。接口说明：https://hearthstonejson.com/docs/images.html 。该服务由 HearthSim 提供，游戏图像权利归原权利人；项目源码分发不包含下载的卡图。本工具不宣称该图源为本地提取或暴雪官方服务。

## 公开台词补充

0.5 增加 [ifindhs 炉石传说 Wiki](https://wiki.ifindhs.com/) 作为默认首选公开台词来源，
保留页面链接与获取时间。金卡、异画、钻石完整卡面按需取自其页面使用的
`search.ifindhs.com` 动态卡面图源。代码仓库和便携发行包不捆绑这些台词、图像或视频，
本站内容与游戏素材的权利不由本项目 MIT 许可覆盖，使用应遵循来源说明。

可选的在线补充来源为[炉石传说中文维基（灰机）](https://hearthstone.huijiwiki.com/)，通过公开 MediaWiki API 按卡牌编号获取。界面保留每条台词的原页面链接，缓存保留来源与获取时间；只在唯一基础事件中关联，没有把社区文本标成客户端字幕。站点页脚声明文字/图像在无特殊说明时采用 CC BY-NC-SA 4.0，具体范围以[站点著作权说明](https://hearthstone.huijiwiki.com/wiki/著作权)及原条目为准。游戏台词的原始权利仍归相应权利人。

本项目不随源码分发下载的台词库；缓存位于忽略的 workspace 目录。源码 MIT 许可不覆盖这些外部内容。

## 台词补充来源

- [HearthSim hsdata](https://github.com/HearthSim/hsdata)：按需读取从炉石客户端提取的简中字幕表；不将其收录内容视为本工具自有或 MIT 授权文本。
- [炉石 Wiki.gg 中文](https://hearthstone.wiki.gg/zh/)：按需读取社区台词，具体页面链接保留在台词下方；文本遵循来源页面所列许可与署名要求。
- 百度百科、灰机的来源说明与现有缓存继续保留。未在项目源码中捆绑或重新授权整套游戏台词。

## 文档截图与项目标识

`docs/screenshots/` 保存少量真实软件界面截图，用于介绍操作与功能。其中出现的炉石卡牌美术、台词和商标仍归原权利人，不能视为 MIT 授权的游戏素材。截图来自本机软件运行界面。

本项目是朝禊ASOGI / AsaMisogi 的非官方学习研究项目，不代表暴雪娱乐，不声称获得暴雪授权或背书。源码的许可与免责声明不替代对游戏协议、第三方内容许可及适用法律的遵守。

## 内置 Vosk 语音模型（v0.4.0 起）

发行包内置 Alphacephei 的 vosk-model-small-cn-0.22 和 vosk-model-small-en-us-0.15。
来源及许可声明：https://alphacephei.com/vosk/models ，两者均列为 Apache 2.0。
保留模型原目录及 README，许可证全文见 licenses/VOSK_MODELS_APACHE-2.0.txt
（免安装版位于 _internal/licenses）。模型不含本项目提取的游戏资源；模型文件仅随 Release 分发。
