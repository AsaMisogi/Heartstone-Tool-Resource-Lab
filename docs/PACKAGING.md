# 独立版构建与开源发布

独立版采用 PyInstaller 目录式打包，内置 Python、Qt WebEngine 和解析运行库。使用者完整解压后双击 EXE，无需安装开发环境。

## 构建

1. 在 Windows x64 上安装并验证源码版。
2. 运行 `Build-Portable.bat`，脚本会安装 `requirements.lock` 中的依赖及 PyInstaller 6.19.0，并使用 `PengPengWorkbench.spec`。
3. 输出为 `dist/砰砰解析台/`。必须分发整个目录，包括 `_internal` 下的 Qt WebEngine 子进程、资源、平台插件与音频库。
4. 保留控制台版入口；避免 `--windowed`，否则用户看不到调试日志。
5. 不默认使用 onefile：Qt WebEngine 较大，onefile 每次解压启动慢，也使运行库许可文件和替换动态库不方便。

构建后脚本自动运行 `tools/prepare_release.py`，在输出目录补齐使用说明、项目及 Python 许可、第三方声明、依赖版本、版本信息和 `SHA256SUMS.txt` 文件清单。

## 手动上传 Release

将 **`dist/砰砰解析台/` 整个文件夹**压缩为 `砰砰解析台-v0.3.0.zip`，作为 Release 附件上传。不要添加项目根目录的 `.venv`、`workspace`、`.cache` 或游戏文件。如果曾在发行目录启动过程序，压缩前移走其中新生成的 `workspace/`（保留你的数据副本）。保留 `_internal`、EXE 和所有随包说明文件。不需要将 ZIP 或 `dist` 提交进 Git。

构建后的基本启动验收可使用 `--workspace` 指向发行目录外的测试工作区，配合 `--quit-after 15 --screenshot <截图路径>`，避免将测试数据混入发行包。清除 PATH 中的开发工具只能验证不依赖 PATH；仍应区分此检查与全新 Windows 机器验收。

`Start-PengPengWorkbench.bat` 使用项目 `.venv` 运行源码版 `python -m pengpeng`。已构建版本从 `dist/砰砰解析台/砰砰解析台.exe` 单独启动。

Vosk 的 Python 模块和原生 DLL 由 spec 收集，构建前先按最新 `requirements.lock` 安装依赖。模型不内嵌到发行包，首次使用按语言下载到工作区；离线部署可预先放入对应模型目录，见 [语音识别说明](SPEECH_RECOGNITION.md)。发行验收还需覆盖识别子进程启动、取消、空闲退出以及无模型时的下载/离线错误提示。

## 分发验收

2026-09-23 本机 Windows 11 x64 构建验证：源码 97 项测试通过；独立 EXE 在只保留 Windows 系统目录的 PATH 下显示首次引导；移动到包含中文和空格的目录后，连接本机炉石、加载图片、解码英雄语音、播放器加载及 WAV 导出通过；关闭时解析进程退出。3325 个发行文件的 SHA-256 校验一致。测试工作区与媒体导出仅位于 `.cache/portable-qa/`，未加入发行目录。本次没有使用未安装 Python 的全新 Windows 虚拟机，也没有完整复测语音识别模型下载及所有资源类型。

- 在没有 Python、uv、开发环境的 Windows 10 / 11 x64 机器验证启动。
- 路径包含空格与中文；安装目录与导出目录分开。
- 打开原画、金卡图层、异画；解码中文语音、长音乐和多子采样音频。
- 断网启动与浏览；首次索引、暂停、续扫、损坏包诊断。
- 空闲时和扫描 / 解码期间关闭窗口，确认解析进程和 QtWebEngineProcess 退出。
- 关闭调试控制台，验证 Windows 作业对象清理后代。
- 检查发行包完整性及第三方许可证，生成 SHA-256 清单并附在 Release。

## 运行库与许可证

源码 MIT 不涵盖所有打包进去的二进制。PySide6 / Qt 使用 LGPLv3 / GPLv3 / 商业许可证方案；发行时应保留相应声明、动态库和满足所选许可的材料。FMOD DLL 随 fmod_toolkit 安装，但 FMOD 自身并非因此变成 MIT；公开分发前需要确认该 DLL 的具体分发许可。见 `THIRD_PARTY_NOTICES.md`。

以上是具体依赖带来的发行工作，不影响当前本机源码版的验证；本项目未替用户发布或上传任何游戏资源。

## GitHub 内容

提交源码、测试、文档、`requirements.lock`、BAT、spec。忽略 `.venv`、`.cache`、`.reference`、`workspace`、`build`、`dist`。不要把从游戏提取的图片、声音、数据库缓存打包到源码仓库。

## 本次体积优化

构建阶段排除 WebEngine 的 `qtwebengine_devtools_resources.debug.pak`，减少约 77.8 MiB；保留正常渲染、媒体、语言和软件 OpenGL 运行库，不新增首次下载步骤。详细性能和兼容范围见 [性能说明](PERFORMANCE.md)。
