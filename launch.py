"""PyInstaller 入口；保留 freeze_support，使冻结后的解析子进程正确启动。"""
if __name__ == '__main__':
    # 冻结后的子进程先分流，避免在每个子进程中提前加载 Qt。
    from multiprocessing import freeze_support
    freeze_support()
    from pengpeng.app import main
    raise SystemExit(main())
