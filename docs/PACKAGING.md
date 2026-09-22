# 独立版构建与开源发布

当前交付是可双击启动的源码版；独立 EXE 构建配置已经提供，但尚未产出经过无 Python 环境验证的正式发行包。

## 构建

1. 在 Windows x64 上安装并验证源码版。
2. 运行 `Build-Portable.bat`，脚本会安装 PyInstaller 6.19.0，并使用 `PengPengWorkbench.spec`。
3. 输出为 `dist/砰砰解析台/`。必须分发整个目录，包括 `_internal` 下的 Qt WebEngine 子进程、资源、平台插件与音频库。
4. 保留控制台版入口；避免 `--windowed`，否则用户看不到调试日志。
5. 不默认使用 onefile：Qt WebEngine 较大，onefile 每次解压启动慢，也使运行库许可文件和替换动态库不方便。

`Start-PengPengWorkbench.bat` 使用项目 `.venv` 运行源码版 `python -m pengpeng`。已构建版本从 `dist/砰砰解析台/砰砰解析台.exe` 单独启动。

Vosk 的 Python 模块和原生 DLL 由 spec 收集，构建前先按最新 `requirements.lock` 安装依赖。模型不内嵌到发行包，首次使用按语言下载到工作区；离线部署可预先放入对应模型目录，见 [语音识别说明](SPEECH_RECOGNITION.md)。发行验收还需覆盖识别子进程启动、取消、空闲退出以及无模型时的下载/离线错误提示。

## 分发验收

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
