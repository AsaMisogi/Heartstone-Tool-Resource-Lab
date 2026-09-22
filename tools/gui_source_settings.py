"""真实 Qt 回归：来源开关、自定义配置、排序、删除与表单布局。

使用页面已公开的用户流程函数，不伪造后端数据。截图写入 .cache/qa。
必须在 Windows 桌面中运行；受限无桌面沙箱无法启动 Chromium 渲染器。
"""
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pengpeng.app import Window, own_process_tree


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    app = QApplication([])
    settings_path = Path('workspace/settings.json')
    original_settings = settings_path.read_bytes()
    window = Window(Path('workspace').resolve())
    window.show()
    output = Path('.cache/qa-source-settings')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('source-settings', """(async()=>{
          await changeView('settings');
          const boxes=()=>[...document.querySelectorAll('[data-provider]')];
          if(boxes().length<4)throw Error('builtins missing');
          window.originalSourceCount=boxes().length;
          boxes()[0].click();await new Promise(r=>setTimeout(r,350));
          const enabled=(await api('status')).settings.transcript_sources[0].enabled;
          if(boxes()[0].checked!==enabled)throw Error('toggle not persisted');
          document.querySelector('#source-name').closest('details').open=true;
          document.querySelector('#source-name').value='测试来源 <安全文本>';
          document.querySelector('#source-url').value='https://example.org/{dbfid}?locale={locale}';
          document.querySelector('#save-transcript-source').click();await new Promise(r=>setTimeout(r,350));
          if(boxes().length!==originalSourceCount+1)throw Error('add failed');
          document.querySelector(`[data-source-edit="${originalSourceCount}"]`).click();
          document.querySelector('#source-name').value='已编辑来源';
          document.querySelector('#save-transcript-source').click();await new Promise(r=>setTimeout(r,350));
          document.querySelector(`[data-source-up="${originalSourceCount}"]`).click();await new Promise(r=>setTimeout(r,350));
          if((await api('status')).settings.transcript_sources[originalSourceCount-1].name!=='已编辑来源')throw Error('edit/order failed');
          return {sources:(await api('status')).settings.transcript_sources};
        })()"""),
        ('responsive', """(async()=>{
          await new Promise(r=>setTimeout(r,150));
          document.querySelector('#source-name').closest('details').open=true;
          document.querySelector('#source-name').scrollIntoView({block:'center'});
          if(document.documentElement.scrollWidth>innerWidth)throw Error('horizontal overflow');
          return {width:innerWidth};
        })()"""),
        ('delete-source', """(async()=>{
          document.querySelector(`[data-source-delete="${originalSourceCount-1}"]`).click();await new Promise(r=>setTimeout(r,350));
          if((await api('status')).settings.transcript_sources.length!==originalSourceCount)throw Error('delete failed');
          return {deleted:true};
        })()"""),
    ]
    results = []
    current = -1
    deadline = time.monotonic() + 180
    failed = False

    def next_stage():
        global current, deadline
        current += 1
        if current >= len(stages):
            # 主动让后端进入复杂皮肤解析，再关闭窗口，验证忙碌时也没有孤儿进程。
            window.inbox.put({'id': 999999, 'method': 'save_settings', 'params': {'paired_audio': False}})
            QTimer.singleShot(200, finish)
            return
        deadline = time.monotonic() + 180
        name, source = stages[current]
        if name == 'responsive':
            window.resize(1060, 720)
        print('QA START', name, flush=True)
        window.page.runJavaScript("window.__qa=null;" + source + ".then(r=>{window.__qa={result:r};}).catch(e=>{window.__qa={error:e.message};});")

    def finish(error=None):
        global failed
        failed = bool(error)
        if error:
            print('QA FAIL', error, flush=True)
            results.append({'error': error})
        timer.stop()
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
        window.close()

    def received(encoded):
        if not encoded:
            return
        data = json.loads(encoded)
        if data.get('error'):
            finish(data['error'])
            return
        name = stages[current][0]
        timer.stop()
        # DOM 已更新不等于 Chromium 已绘制；等待一帧以上再截图。
        def capture():
            window.grab().save(str(output / f'{name}.png'))
            results.append({'stage': name, **data})
            print('QA PASS', name, str(data)[:250], flush=True)
            next_stage()
            if current < len(stages):
                timer.start()
        QTimer.singleShot(350, capture)

    def tick():
        if time.monotonic() > deadline:
            finish('GUI stage timeout: ' + (stages[current][0] if current >= 0 else 'initialization'))
        elif current < 0:
            window.page.runJavaScript("!!window.pengpeng?.state.status", lambda ready: next_stage() if ready and current < 0 else None)
        else:
            window.page.runJavaScript('window.__qa?JSON.stringify(window.__qa):null', received)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(500)
    app.exec()
    settings_path.write_bytes(original_settings)
    print('QA backend alive after close:', window.worker.is_alive(), flush=True)
    sys.exit(1 if failed or window.worker.is_alive() else 0)
