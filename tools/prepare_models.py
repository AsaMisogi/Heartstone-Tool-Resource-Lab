"""构建前准备官方固定版本模型；下载进度直接显示，失败则停止构建。"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.speech import MODELS, download_model, check_status
from pengpeng.speech_config import DEFAULT_CONFIG


def main():
    root = Path(__file__).resolve().parents[1]
    for locale in MODELS:
        download_model(root / 'pengpeng', locale, lambda message: print(message, flush=True))
    # 仅检查两个标志文件不能发现图文件丢失；构建前实际加载并解码静音。
    result = check_status(root / '.cache/model-check', DEFAULT_CONFIG, print)
    print(result['message'])
    if not result['ok']:
        raise SystemExit('内置模型检查失败，停止构建。')


if __name__ == '__main__':
    main()
