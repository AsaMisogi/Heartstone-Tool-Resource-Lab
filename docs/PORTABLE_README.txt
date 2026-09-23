砰砰解析台 · Windows x64 免安装版

1. 将压缩包完整解压到可写目录（支持中文和空格路径）。
2. 双击“砰砰解析台.exe”。无需安装 Python、uv、Node.js 或额外 Python 包。
3. 首次启动选择包含 Data 文件夹的 Hearthstone 安装目录。

必须保留整个文件夹，尤其是 _internal；不要单独移动 EXE，也不要在压缩包内直接运行。
Windows 10 / 11 64 位；炉石客户端及所需语言包需由用户自行安装。
设置、收藏、索引、缓存、日志和默认导出文件保存在 EXE 同目录的 workspace 中。
升级时退出旧版，再将旧版 workspace 文件夹复制到新版 EXE 旁即可保留数据。

本地解析、原画、声音试听与导出可离线使用。
完整卡面、公开台词查询需要联网；语音识别模型首次使用按语言下载，之后可离线运行。
发行包不包含游戏素材或语音识别模型。

启动时的控制台用于诊断，关闭控制台也会退出程序。
遇到问题请保留控制台错误及 workspace/logs/pengpeng.log。

源码及反馈：https://github.com/AsaMisogi/Heartstone-Tool-Resource-Lab
版本见 VERSION.txt；文件校验值见 SHA256SUMS.txt。
第三方声明见 THIRD_PARTY_NOTICES.md，各依赖许可保留在 _internal 的发行元数据中。
