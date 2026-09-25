"""隔离工作区验证首次引导及保存目录后的再次启动；不会覆盖用户设置。"""
import json
import multiprocessing as mp
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pengpeng.app import Window, own_process_tree


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    output = Path('.cache/qa-first-run').resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix='workspace-', dir=output))
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = Window(workspace)
    window.show()
    phase = 0
    deadline = time.monotonic() + 120
    results = []
    progress_captured = False

    def capture_progress(text):
        """欢迎模态框必须展示建库阶段，不能只在被遮挡的主窗口显示。"""
        global progress_captured
        if text and not progress_captured:
            progress_captured = True
            window.grab().save(str(output / 'welcome-progress.png'))
            results.append({'visible_initialization_progress': text})

    def finish(error=None):
        timer.stop()
        if error:
            results.append({'error': error})
        window.close()
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
        app.exit(1 if error else 0)

    def check(value):
        global window, phase
        if value is None:
            return
        if phase == 0 and value:
            window.grab().save(str(output / 'welcome.png'))
            results.append({'welcome': True})
            phase = 1
            window.page.runJavaScript("document.querySelector('#welcome-path').value='F:/Games/Hearthstone';document.querySelector('#welcome-connect').click()")
        elif phase == 1 and value:
            if not progress_captured:
                finish('首次建库时欢迎窗口没有可见阶段进度')
                return
            window.grab().save(str(output / 'index-guide.png'))
            results.append({'connected_with_index_guide': True})
            phase = 3
            window.page.runJavaScript("document.querySelector('#index-later').click()")
        elif phase == 3 and value:
            results.append({'deferred_index': True})
            window.close()
            window = Window(workspace)
            window.show()
            phase = 2
        elif phase == 2 and value:
            results.append({'restart_without_welcome': True})
            finish()

    def tick():
        if time.monotonic() > deadline:
            finish('first-run flow timed out')
            return
        expression = "!!document.querySelector('#welcome')?.open" if phase == 0 else "!!window.pengpeng?.state.status?.ready && !document.querySelector('#welcome').open"
        if phase == 1:
            window.page.runJavaScript("document.querySelector('#welcome').open && !document.querySelector('#welcome-progress').hidden ? document.querySelector('#welcome-progress').textContent : ''", capture_progress)
            expression += " && document.querySelector('#index-guide').open"
        elif phase in (2, 3):
            expression += " && !document.querySelector('#index-guide').open && window.pengpeng.state.status.settings.index_guide_seen"
        window.page.runJavaScript(expression, check)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(500)
    sys.exit(app.exec())
