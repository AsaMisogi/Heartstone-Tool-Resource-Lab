# Windows onedir 打包配置。保留控制台及 Qt 动态库，方便诊断与 LGPL 合规分发。
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, copy_metadata
from pathlib import Path
import re
import os
from fmod_toolkit.importer import get_fmod_path_for_system

# collect_all 会导入 pyfmodex；让它在构建时也找到随包提供的 FMOD。
os.environ['PYFMODEX_DLL_PATH'] = get_fmod_path_for_system()
# 不从开发机 PATH 收集其他软件的同名 DLL（例如 Poppler 的 ICU）。
# Qt 使用 Windows 自带 ICU；PyInstaller 的 Qt hook 会补充自己的库目录。
windows = Path(os.environ['SystemRoot'])
os.environ['PATH'] = os.pathsep.join(map(str, (windows / 'System32', windows)))

datas = [('pengpeng/web', 'pengpeng/web'), ('THIRD_PARTY_NOTICES.md', '.'), ('LICENSE', '.')]
# 模型是发行必需资源，缺失时构建失败，不能生成首次运行失效的安装包。
for name in ('vosk-model-small-cn-0.22', 'vosk-model-small-en-us-0.15'):
    folder = Path('pengpeng/models') / name
    if not (folder / 'am/final.mdl').is_file() or not (folder / 'conf/model.conf').is_file():
        raise RuntimeError('请先运行 tools/prepare_models.py：缺失 ' + name)
datas += [('pengpeng/models', 'pengpeng/models'), ('licenses/VOSK_MODELS_APACHE-2.0.txt', 'licenses')]
binaries = []
hiddenimports = []
# QtWebView 的 Python 模块尚无专用 PyInstaller hook。显式携带官方 QML 插件
# 和 WebView2 适配 DLL，不能依赖开发机的 Qt 插件搜索路径；系统 Runtime 不随包复制。
import PySide6
qt_root = Path(PySide6.__file__).parent
datas += [(str(qt_root / 'qml/QtWebView'), 'PySide6/qml/QtWebView')]
binaries += [
    (str(qt_root / 'Qt6WebView.dll'), 'PySide6'),
    (str(qt_root / 'Qt6WebViewQuick.dll'), 'PySide6'),
    (str(qt_root / 'plugins/webview/qtwebview_webview2.dll'), 'PySide6/plugins/webview'),
]
for package in ('UnityPy', 'fmod_toolkit', 'pyfmodex', 'vosk'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
binaries += collect_dynamic_libs('_soundfile_data')
datas += collect_data_files('archspec')  # 纹理解码器运行时读取 CPU 特性 JSON。
for package in re.findall(r'^([\w-]+)==', Path('requirements.lock').read_text('utf-8'), re.M):
    datas += copy_metadata(package)

a = Analysis(['launch.py'], pathex=['.'], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
# 调试符号资源不参与页面渲染；保留正式 devtools pak、全部语言和软件渲染器。
# 在收集阶段过滤，避免构建后手工删文件导致发行清单不一致。
a.datas = [entry for entry in a.datas if not entry[0].endswith('qtwebengine_devtools_resources.debug.pak')]
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='砰砰解析台',
          icon='pengpeng/web/icon.ico', debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='砰砰解析台')
