"""0.6 真实 Qt 回归：分页、播放状态、冒险语音、缩略图与设置。

使用页面已公开的用户流程函数，不伪造后端数据。截图写入 .cache/qa。
必须在 Windows 桌面中运行；受限无桌面沙箱无法启动 Chromium 渲染器。
"""
import json
import argparse
import multiprocessing as mp
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pengpeng.app import Window, own_process_tree


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    app = QApplication([])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('.cache/qa-05-workspace'))
    parser.add_argument('--interactions', action='store_true', help='验收 0.7 分组、原生鼠标拖拽及播放帧生命周期')
    parser.add_argument('--upgrade', action='store_true', help='验收 0.8 快速导航、重播、战棋和百科关联')
    args = parser.parse_args()
    window = Window(args.workspace.resolve())
    window.show()
    output = Path('.cache/qa-08' if args.upgrade else '.cache/qa-07' if args.interactions else '.cache/qa-06')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('pagination', """(async()=>{
          await pengpeng.showCard('NPC_INNKEEPER'); await pengpeng.cardTab('voices');
          if(!document.querySelector('#voice-options').open)throw Error('options closed');
          const size=document.querySelector('#voice-page-size');size.value='12';size.dispatchEvent(new Event('change'));
          await new Promise(r=>setTimeout(r,100));
          if(document.querySelectorAll('.voice-row').length!==12)throw Error('page size');
          const first=document.querySelector('[data-play]').dataset.play;
          document.querySelector('#voice-next').click();
          if(pengpeng.state.voicePage!==2||first===document.querySelector('[data-play]').dataset.play)throw Error('next page');
          const jump=document.querySelector('#voice-page');jump.value='3';jump.dispatchEvent(new Event('change'));
          if(pengpeng.state.voicePage!==3)throw Error('jump');
          return {total:pengpeng.state.voiceItems.length,page:pengpeng.state.voicePage,rows:document.querySelectorAll('.voice-row').length};
        })()"""),
        ('play-pause', """(async()=>{
          const b=document.querySelector('[data-play]'), id=b.dataset.play;
          await pengpeng.playAsset(id);await new Promise(r=>setTimeout(r,250));
          if(b.textContent!=='Ⅱ')throw Error('playing icon '+b.textContent);
          await pengpeng.playAsset(id);await new Promise(r=>setTimeout(r,80));
          const a=document.querySelector('#audio');if(!a.paused||b.textContent!=='▷')throw Error('pause');
          await pengpeng.playAsset(id);await new Promise(r=>setTimeout(r,80));
          if(a.paused||b.textContent!=='Ⅱ')throw Error('resume');
          document.querySelector('#stop-player').click();return {pause:true,resume:true};
        })()"""),
        ('lich-adventure', """(async()=>{
          await pengpeng.showCard('HERO_11');await pengpeng.cardTab('voices');
          const items=pengpeng.state.voiceItems.filter(x=>x.adventure);
          if(!items.some(x=>x.event.includes('面对法师'))||!items.some(x=>x.event.includes('回应')))throw Error('missing adventure');
          const q=document.querySelector('#voice-search');q.value='面对';q.dispatchEvent(new Event('input'));
          return {adventure:items.length,classes:[...document.querySelectorAll('.voice-top b')].map(x=>x.textContent)};
        })()"""),
        ('edition-thumbnails', """(async()=>{
          await pengpeng.showCard('TOY_330');const d=document.querySelector('.edition-panel');d.open=true;
          for(let i=0;i<120;i++){await new Promise(r=>setTimeout(r,100));if([...d.querySelectorAll('img')].every(im=>im.complete&&im.naturalWidth>0))break;}
          const images=[...d.querySelectorAll('img')];if(images.length<9||images.some(im=>!im.naturalWidth))throw Error('edition thumbnails');
          d.scrollIntoView();return {images:images.length};
        })()"""),
        ('set-colors', """(async()=>{
          document.querySelector('#close-detail').click();await pengpeng.changeView('cards');
          const select=document.querySelector('[data-filter=set]');select.nextElementSibling.click();
          const rows=[...document.querySelectorAll('.select-popup [data-kind]')];
          const kinds=[...new Set(rows.map(r=>r.dataset.kind))];
          if(kinds.length<3)throw Error('missing set kinds');
          return {kinds};
        })()"""),
        ('update-settings', """(async()=>{
          document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));
          await pengpeng.changeView('settings');
          const b=document.querySelector('#auto-check-updates');if(!b)throw Error('missing updater');
          b.checked=false;b.dispatchEvent(new Event('change'));await new Promise(r=>setTimeout(r,100));
          const s=await pengpeng.api('status');if(s.settings.auto_check_updates!==false)throw Error('not persisted');
          return {auto:s.settings.auto_check_updates,pageSize:s.settings.voice_page_size};
        })()"""),
    ]
    stages.extend([
        ('update-dialog-fixture', """(async()=>{
          // 受控新版响应仅用于验证弹窗；真实 API 的版本比较由离线测试覆盖。
          const original=api;
          try {
            api=(method,params)=>method==='check_updates'?Promise.resolve({available:true,current:'0.6.0',latest:'v99.0.0',url:'https://github.com/AsaMisogi/Heartstone-Tool-Resource-Lab/releases/tag/v99.0.0'}):original(method,params);
            await checkUpdates(true);
            const dialog=document.querySelector('#update-dialog');
            if(!dialog.open||!document.querySelector('#update-description').textContent.includes('v99.0.0'))throw Error('update dialog');
            return {open:dialog.open};
          } finally {api=original;}
        })()"""),
        ('scroll-and-pager', """(async()=>{
          document.querySelector('#update-close').click();
          await pengpeng.showCard('NPC_INNKEEPER');await pengpeng.cardTab('voices');
          const panel=document.querySelector('#detail'),frames=[];
          let previous=performance.now();
          for(let i=0;i<45;i++){await new Promise(r=>requestAnimationFrame(r));const t=performance.now();frames.push(t-previous);previous=t;panel.scrollTop+=35;}
          document.querySelector('.voice-pagination').scrollIntoView({block:'center'});
          frames.sort((a,b)=>a-b);
          return {frames:frames.length,medianMs:frames[22],p95Ms:frames[42],rows:document.querySelectorAll('.voice-row').length};
        })()"""),
    ])
    if args.interactions:
        from interaction_cases import STAGES
        stages = STAGES
    if args.upgrade:
        from upgrade_cases import STAGES
        stages = STAGES
    # 启动失败保留可见页面和脚本状态，避免只能等待超时而不知道故障阶段。
    def startup_snapshot():
        window.grab().save(str(output / 'startup.png'))
        window.page.runJavaScript("JSON.stringify({ready:!!window.pengpeng?.state.status?.ready,toast:document.querySelector('#toast')?.textContent,url:location.href})", lambda value:print('QA startup', value, flush=True))
    QTimer.singleShot(8000, startup_snapshot)
    results = []
    current = -1
    deadline = time.monotonic() + 180
    failed = False

    def next_stage():
        global current, deadline
        current += 1
        if current >= len(stages):
            # 主动让后端进入复杂皮肤解析，再关闭窗口，验证忙碌时也没有孤儿进程。
            window.inbox.put({'id': 999999, 'method': 'card_audio', 'params': {'cardid': 'HERO_01bn'}})
            QTimer.singleShot(200, finish)
            return
        deadline = time.monotonic() + 180
        name, source = stages[current]
        print('QA START', name, flush=True)
        window.page.runJavaScript("window.__qa=null;" + source + ".then(r=>{window.__qa={result:r};}).catch(e=>{window.__qa={error:e.message};});")

    def finish(error=None):
        global failed
        failed = bool(error)
        if error:
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
        if data.get('result', {}).get('_wheel'):
            timer.stop()
            window.page.runJavaScript('window.__qa=null')
            target = window.web.focusProxy()
            point = QPoint(700, 350)
            event = QWheelEvent(QPointF(point), QPointF(target.mapToGlobal(point)), QPoint(), QPoint(0,-120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(target, event)
            window.page.runJavaScript("""setTimeout(()=>{
              const values=window.__wheelSamples||[], steps=new Set(values.map(v=>Math.round(v))).size;
              window.__wheelDone=true;
              window.__qa=steps<3?{error:'wheel not animated '+JSON.stringify(values)}:{result:{distinctPositions:steps,total:values.at(-1),frames:values.length}};
            },650);""")
            timer.start()
            return
        if data.get('result', {}).get('_scrollbar'):
            timer.stop()
            window.page.runJavaScript('window.__qa=null')
            rect = data['result']; scale = window.web.zoomFactor()
            target = window.web.focusProxy()
            start = QPoint(round(rect['x'] * scale), round(rect['y'] * scale))
            end = QPoint(start.x(), round((rect['y'] + rect['height'] * .55) * scale))
            QTest.mousePress(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
            QTest.mouseMove(target, end, 80)
            QTest.qWait(100)
            QTest.mouseRelease(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
            window.page.runJavaScript("""setTimeout(()=>{
              const panel=document.querySelector('#detail'),top=panel.scrollTop;
              window.__qa=top<300?{error:'scrollbar drag failed '+top}:{result:{scrollTop:top}};
              panel.scrollTop=document.querySelector('#voice-groups').offsetTop-40;
            },100);""")
            timer.start()
            return
        if data.get('result', {}).get('_drag'):
            # 真实 Qt 鼠标事件才能获得 Pointer Capture；合成 JS 事件不能验证移出边界。
            timer.stop()
            window.page.runJavaScript('window.__qa=null')
            rect = data['result']
            scale = window.web.zoomFactor()
            target = window.web.focusProxy()
            def point(fraction):
                return QPoint(round((rect['x'] + rect['width'] * fraction) * scale),
                              round((rect['y'] + rect['height'] / 2) * scale))
            QTest.mousePress(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(.15))
            for fraction in (.25, .4, 1.1, .7, .8):
                QTest.mouseMove(target, point(fraction), 30)
                QTest.qWait(35)
            if rect.get('cancel'):
                QTest.keyClick(target, Qt.Key.Key_Escape)
                QTest.qWait(50)
            QTest.mouseRelease(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(.8))
            if rect.get('cancel'):
                window.page.runJavaScript("""setTimeout(()=>{
                  const a=document.querySelector('#audio');
                  window.__qa=waveform.drag||window.__seekCount||a.currentTime!==window.__beforeCancel
                    ? {error:'cancel changed playback'} : {result:{cancelled:true,seeks:0}};
                },160);""")
            else:
                window.page.runJavaScript("""setTimeout(()=>{
              const a=document.querySelector('#audio');
              const ratio=a.currentTime/a.duration;
              window.__qa=Math.abs(ratio-.8)>.025||waveform.drag||window.__seekCount!==1||window.__dragMoves<3
                ? {error:'native drag '+JSON.stringify({ratio,seeks:window.__seekCount,moves:window.__dragMoves})}
                : {result:{ratio,seeks:window.__seekCount,moves:window.__dragMoves,paused:a.paused}};
                },160);""")
            timer.start()
            return
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
            window.page.runJavaScript("!!window.pengpeng?.state.status?.ready && document.querySelectorAll('.card-image img').length>2", lambda ready: next_stage() if ready and current < 0 else None)
        else:
            window.page.runJavaScript('window.__qa?JSON.stringify(window.__qa):null', received)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(500)
    app.exec()
    print('QA backend alive after close:', window.worker.is_alive(), flush=True)
    sys.exit(1 if failed or window.worker.is_alive() else 0)
