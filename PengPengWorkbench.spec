# Windows onedir 打包配置。保留控制台及 Qt 动态库，方便诊断与 LGPL 合规分发。
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, copy_metadata

datas = [('pengpeng/web', 'pengpeng/web'), ('THIRD_PARTY_NOTICES.md', '.'), ('LICENSE', '.')]
binaries = []
hiddenimports = []
for package in ('UnityPy', 'fmod_toolkit', 'pyfmodex', 'vosk'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
binaries += collect_dynamic_libs('_soundfile_data')
for package in ('UnityPy', 'PySide6', 'soundfile', 'fmod_toolkit', 'vosk'):
    datas += copy_metadata(package)

a = Analysis(['launch.py'], pathex=['.'], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='砰砰解析台',
          icon='pengpeng/web/icon.ico', debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='砰砰解析台')
