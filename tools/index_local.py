"""开发 / 无界面使用：扫描所有本地包，输出可恢复的 SQLite 索引。"""
from pathlib import Path
import logging
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    service = Service(Path('workspace'))
    service.initialize()
    for progress in service.scan():
        pass
    print(service.status())
