"""PyInstaller 入口；保留 freeze_support，使冻结后的解析子进程正确启动。"""
from pengpeng.app import main

if __name__ == '__main__':
    raise SystemExit(main())
