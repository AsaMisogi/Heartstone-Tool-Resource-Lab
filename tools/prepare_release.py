"""为 PyInstaller 输出补齐使用说明、许可、版本及 SHA-256 清单。"""
from pathlib import Path
import hashlib
import shutil
import sys
import tomllib


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'dist' / '砰砰解析台'
    if not (output / '砰砰解析台.exe').is_file():
        raise SystemExit('请先运行 PyInstaller 构建。')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'requirements.lock'):
        shutil.copy2(root / name, output / name)
    shutil.copy2(root / 'docs' / 'PORTABLE_README.txt', output / '使用说明.txt')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    shutil.copy2(python_license, output / 'PYTHON_LICENSE.txt')
    version = tomllib.loads((root / 'pyproject.toml').read_text('utf-8'))['project']['version']
    (output / 'VERSION.txt').write_text(f'砰砰解析台 {version}\nWindows x64 portable\nPython {sys.version}\n', 'utf-8')
    manifest = output / 'SHA256SUMS.txt'
    files = sorted(p for p in output.rglob('*') if p.is_file() and p != manifest)
    lines = []
    for path in files:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        lines.append(f'{digest}  {path.relative_to(output).as_posix()}\n')
    manifest.write_text(''.join(lines), 'utf-8')
    print(f'发行目录：{output}\n共 {len(files)} 个文件，可直接压缩整个目录。')


if __name__ == '__main__':
    main()
