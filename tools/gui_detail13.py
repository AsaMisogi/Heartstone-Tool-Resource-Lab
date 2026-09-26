"""0.13 实际桌面回归：侧栏拖动、倍率、偏好保存和英雄筛选迁移。

鼠标事件只发送到本工具创建且位于前台的窗口；结果/截图写入 .cache。
设置 PENGPENG_RENDERER=qt 可在兼容引擎运行同一套验收。
"""
import ctypes
from ctypes import wintypes as wt
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.app import Window, own_process_tree
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    app = QApplication([])
    window = Window(Path('.cache/qa-05-workspace').resolve())
    window.show()
    output = Path('.cache/qa-13')
    output.mkdir(exist_ok=True)
    engine = 'native' if window.native_browser else 'qt'
    user = ctypes.windll.user32
    original = wt.POINT()
    user.GetCursorPos(ctypes.byref(original))
    steps = [
        ('hero-filters', """(async()=>{
          state.viewStates.heroes={filters:{race:'24',keyword:'嘲讽'}};
          await pengpeng.changeView('heroes');
          const keys=[...document.querySelectorAll('[data-filter]')].map(e=>e.dataset.filter);
          if(keys.includes('race')||keys.includes('keyword')||state.filters.race||state.filters.keyword)throw Error('旧筛选未清理');
          await api('save_settings',{online_transcripts:false,speech_recognition:false});
          return {keys};
        })()"""),
        ('prepare-drag', """(async()=>{
          await pengpeng.showCard('EDR_000'); await pengpeng.cardTab('voices');
          host.setInterfaceScale(1.25);detailResize.set(610);
          await new Promise(r=>setTimeout(r,350));
          document.querySelector('#detail').scrollTop=300;
          const h=document.querySelector('#detail-resize').getBoundingClientRect();
          if(Math.abs(h.top)>2)throw Error('拖动边缘随正文滚动');
          return {drag:true,x:h.left+h.width/2,y:220,scale:window.NativeHost?.scale||1};
        })()"""),
        ('verify-width', """(async()=>{
          await new Promise(r=>setTimeout(r,250));
          const s=await api('status');const width=logicalViewport(document.querySelector('#detail').getBoundingClientRect()).width;
          if(Math.abs(width-730)>2||s.settings.detail_width!==730)throw Error('宽度未保存 '+JSON.stringify({width,saved:s.settings.detail_width}));
          closeDetail();await pengpeng.showCard('EDR_000');
          if(Math.abs(logicalViewport(document.querySelector('#detail').getBoundingClientRect()).width-730)>2)throw Error('重开未记忆');
          return {width,saved:s.settings.detail_width};
        })()"""),
        ('keyboard-reset', """(async()=>{
          const h=document.querySelector('#detail-resize');
          h.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));
          await new Promise(r=>setTimeout(r,100));
          if((await api('status')).settings.detail_width!==750)throw Error('方向键调宽失败');
          h.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
          await new Promise(r=>setTimeout(r,100));
          if((await api('status')).settings.detail_width!==610)throw Error('双击复原失败');
          host.setInterfaceScale(1);return {keyboard:true,reset:true};
        })()"""),
        ('ysera-audio', """(async()=>{
          await pengpeng.cardTab('voices');
          const sounds=state.voiceItems.filter(x=>x.kind==='sound');
          if(sounds.length>20||state.voiceErrors.length)throw Error('音效仍过量或解析不完整');
          const voice=state.voiceItems.find(x=>x.kind==='voice'&&x.group==='play');
          state.pairedAudio=true;state.generalAudio=true;
          await pengpeng.playAsset(voice.id,true);await new Promise(r=>setTimeout(r,350));
          if(audio.paused||!audio.duration)throw Error('配套试听失败');
          const duration=audio.duration;stopPlayback();
          return {soundEvents:sounds.length,duration};
        })()"""),
    ]
    index = -1
    deadline = time.monotonic() + 90
    results = []
    failed = False

    def finish(error=None):
        global failed
        failed = bool(error)
        timer.stop()
        if error:
            results.append({'error': error})
            print('FAIL', error, flush=True)
        (output / f'{engine}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf8')
        user.SetCursorPos(original.x, original.y)
        window.close()

    def next_step():
        global index, deadline
        index += 1
        deadline = time.monotonic() + 90
        if index >= len(steps):
            finish()
            return
        print('START', engine, steps[index][0], flush=True)
        window.page.runJavaScript('window.__qa13=null;' + steps[index][1] +
            '.then(result=>window.__qa13={result}).catch(e=>window.__qa13={error:e.message})')

    def drag(result):
        """真实拖动 120 个逻辑像素；核对 HWND 防止向其他软件发输入。"""
        timer.stop()
        handle = int(window.winId())
        user.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
        user.WindowFromPoint.argtypes = [wt.POINT]
        user.WindowFromPoint.restype = wt.HWND
        user.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        user.GetAncestor.restype = wt.HWND
        window.raise_()
        window.activateWindow()
        dpr = window.devicePixelRatioF()
        # Qt page zoom 已在 viewport 坐标中体现，但 QWebEngine 返回的 clientX
        # 是缩放前值；原生 CSS zoom 的 getBoundingClientRect 则已乘倍率。
        page_scale = 1 if window.native_browser else window.page.zoomFactor()
        def move(step):
            point = wt.POINT(round((result['x'] * page_scale - 150 * step / 12) * dpr),
                             round(result['y'] * page_scale * dpr))
            user.ClientToScreen(handle, ctypes.byref(point))
            if user.GetAncestor(user.WindowFromPoint(point), 2) != handle:
                finish('测试窗口被遮挡，停止发送鼠标输入')
                return
            user.SetCursorPos(point.x, point.y)
            if step == 0:
                user.mouse_event(0x0002, 0, 0, 0, 0)
            elif step == 12:
                user.mouse_event(0x0004, 0, 0, 0, 0)
        for step in range(13):
            QTimer.singleShot(150 + step * 30, lambda step=step: move(step) if not failed else None)
        def done():
            if failed:
                return
            next_step()
            timer.start()
        QTimer.singleShot(850, done)

    def receive(encoded):
        if not encoded:
            return
        data = json.loads(encoded)
        if data.get('error'):
            finish(data['error'])
            return
        results.append({'stage': steps[index][0], **data})
        print('PASS', steps[index][0], data['result'], flush=True)
        if data['result'].get('drag'):
            drag(data['result'])
            return
        if steps[index][0] == 'verify-width':
            window.grab().save(str(output / f'{engine}.png'))
        next_step()

    def poll():
        if time.monotonic() > deadline:
            finish('等待界面超时')
        elif index < 0:
            window.page.runJavaScript('!!window.pengpeng?.state.status?.ready', lambda ready: next_step() if ready and index < 0 else None)
        else:
            window.page.runJavaScript('window.__qa13?JSON.stringify(__qa13):null', receive)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(200)
    app.exec()
    sys.exit(1 if failed else 0)
