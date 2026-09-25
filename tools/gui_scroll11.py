"""真实 Qt / Chromium 验收：系统滚轮幅度、显示帧、嵌套容器与原生滚动条。

调用系统滚轮事件，逐帧采样真实页面位置；数字是本机测量，不代表主观手感。
普通刻度、连滚、反向及嵌套场景使用相同页面，结果记录实际帧间隔和尾声。
"""
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, QPoint, QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pengpeng.app import Window, own_process_tree


if __name__ == '__main__':
    mp.freeze_support(); job = own_process_tree(); app = QApplication([])
    window = Window(Path('.cache/qa-05-workspace').resolve()); window.show()
    output=Path('.cache/qa-11');output.mkdir(exist_ok=True)
    results=[]; stage=-1; failed=False; deadline=time.monotonic()+120
    steps=[
        ('responsive-wheel', """(async()=>{closeDetail();await pengpeng.changeView('cards');state.filters={};state.query='';state.favorites=false;state.limit=96;state.offset=0;await refresh();return {_wheel:'single'};})()"""),
        ('wheel-burst', """Promise.resolve({_wheel:'burst'})"""),
        ('wheel-reverse', """Promise.resolve({_wheel:'reverse'})"""),
        ('reduced-motion', """Promise.resolve({_wheel:'reduced'})"""),
        ('nested-wheel', """(async()=>{await pengpeng.showCard('NPC_INNKEEPER');await pengpeng.cardTab('voices');return {_wheel:'nested'};})()"""),
    ]
    steps += [(name, "Promise.resolve({_wheel:'"+name+"'})") for name in ('nested-reduced','lines-one','lines-six','lines-zero','page','zoom','instant')]

    steps.extend([
        ('scrollbar-drag', """(async()=>{closeDetail();WheelScroll.stop();document.scrollingElement.scrollTop=500;
          await new Promise(r=>setTimeout(r,100));const h=innerHeight,s=document.scrollingElement.scrollHeight,thumb=Math.max(44,h*h/s);
          return {_drag:true,thumbY:500/(s-h)*(h-thumb)+thumb/2};})()"""),
        ('scroll-setting', """(async()=>{await pengpeng.changeView('settings');const node=document.querySelector('#scroll-mode');node.value='instant';await node.onchange();
          const status=await api('status');if(status.settings.scroll_mode!=='instant')throw Error('setting not saved');
          node.value='smooth';await node.onchange();return {saved:true,restored:true};})()"""),
    ])
    if '--controls' in sys.argv: steps = [step for step in steps if step[0] in ('responsive-wheel','scrollbar-drag','scroll-setting')]

    def finish(error=None):
        global failed
        failed=bool(error);poll.stop()
        if error:results.append({'error':error});print('FAIL',error,flush=True)
        (output/('controls.json' if '--controls' in sys.argv else 'results.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf8')
        window.close()

    def next_step():
        global stage,deadline
        stage+=1;deadline=time.monotonic()+120
        if stage>=len(steps):finish();return
        name,js=steps[stage];print('START',name,flush=True)
        window.page.runJavaScript('window.__qa09=null;'+js+'.then(result=>window.__qa09={result}).catch(e=>window.__qa09={error:e.message});')

    def wheel(mode):
        poll.stop()
        window.bridge.setSmoothScrolling('reduced' not in mode)
        import pengpeng.scrolling as scrolling
        original_units = scrolling.system_scroll_units
        units = {'lines-one':1, 'lines-six':6, 'lines-zero':0, 'page':scrolling.PAGE_SCROLL}.get(mode, 3)
        scrolling.system_scroll_units = lambda horizontal=False: units
        window.web.setZoomFactor(1.25 if mode == 'zoom' else 1)
        selector="document.querySelector('#detail')" if mode.startswith('nested') else 'document.scrollingElement'
        prepare=f"""WheelScroll.stop();WheelScroll.setMode({'"instant"' if mode=='instant' else '"smooth"'});if(!{str(mode.startswith('nested')).lower()})closeDetail();window.__qa09=null;window.__wheelPanel={selector};__wheelPanel.scrollTop=500;
          window.__points=[];window.__sampling=true;window.__start=performance.now();window.__outside=document.scrollingElement.scrollTop;
          function sample(t){{__points.push([performance.now()-__start,__wheelPanel.scrollTop,document.querySelector('.card-tile:nth-child(13)')?.getBoundingClientRect().top+document.scrollingElement.scrollTop]);if(__sampling)requestAnimationFrame(sample);}}requestAnimationFrame(sample);"""
        def inject(_):
            point=QPoint(1150,450) if mode.startswith('nested') else QPoint(700,350)
            target=window.web.focusProxy()
            events=[(i*25,-120) for i in range(10)] if mode=='burst' else [(0,-120),(40,120)] if mode=='reverse' else [(0,-120)]
            def send(angle):
                # 向本测试窗口送 Windows WM_MOUSEWHEEL，经过 Qt 的真实 Windows 输入分派。
                # 不移动用户鼠标、不改全局滚动设置，也不只用 QApplication.sendEvent。
                import ctypes
                from ctypes import wintypes
                user32=ctypes.windll.user32
                user32.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
                user32.SendMessageW.restype=wintypes.LPARAM
                hwnd=int(window.winId());dpr=window.devicePixelRatioF()
                native=wintypes.POINT(round(point.x()*dpr),round(point.y()*dpr))
                user32.ClientToScreen(hwnd,ctypes.byref(native))
                user32.SendMessageW(hwnd,0x020A,(angle & 0xffff)<<16,(native.x & 0xffff)|((native.y & 0xffff)<<16))
            for delay,angle in events:QTimer.singleShot(delay,lambda a=angle:send(a))
            def collect():
                js="""(()=>{window.__sampling=false;const p=[[0,500],...__points],changes=p.filter((v,i)=>i&&v[1]!==p[i-1][1]);
                  const intervals=p.slice(1).map((v,i)=>v[0]-p[i][0]).sort((a,b)=>a-b);
                  return JSON.stringify({positions:p,firstMs:changes[0]?.[0],lastMs:changes.at(-1)?.[0],steps:changes.length,
                    p95FrameMs:intervals[Math.floor(intervals.length*.95)],total:p.at(-1)[1]-500,outside:document.scrollingElement.scrollTop-__outside,viewport:__wheelPanel.clientHeight,layoutDrift:Math.max(...__points.map(x=>x[2]))-Math.min(...__points.map(x=>x[2]))});})()"""
                def done(encoded):
                    data=json.loads(encoded);data['tailMs']=data.get('lastMs',999)-events[-1][0]
                    if not data['steps'] and mode != 'lines-zero':
                        results.append(data);window.grab().save(str(output/'failed.png'));window.page.runJavaScript("JSON.stringify({h:document.scrollingElement.scrollHeight,client:innerHeight,overflow:getComputedStyle(document.body).overflow,dialogs:[...document.querySelectorAll('dialog[open]')].map(x=>x.id),target:document.elementFromPoint(700,350)?.outerHTML.slice(0,300)})",lambda value:print('DIAG',value,flush=True));QTimer.singleShot(200,lambda:finish('wheel produced no movement: '+mode));return
                    if data['steps'] and data['tailMs']>130:finish('wheel tail exceeded 300ms: '+mode);return
                    if mode.startswith('nested') and data['outside']!=0:finish('nested scroll escaped');return
                    if ('reduced' in mode or mode=='instant') and data['steps']>2:
                        results.append({'stage':'reduced-motion','result':data});print(data,flush=True);finish('reduced motion animated');return
                    expected = {'single':100,'burst':1000,'lines-one':100/3,'lines-six':200,'lines-zero':0,'zoom':80,'instant':100,'nested':100,'nested-reduced':100,'reduced':100}.get(mode)
                    scrolling.system_scroll_units = original_units
                    if mode == 'page': expected=data['viewport']-40
                    if expected is not None and abs(data['total']-expected)>1:finish('system distance mismatch '+str(data));return
                    if data['layoutDrift']>1:finish('content layout shifted '+str(data));return
                    results.append({'stage':steps[stage][0],'result':data})
                    print('PASS',steps[stage][0],{k:v for k,v in data.items() if k!='positions'},flush=True)
                    window.bridge.setSmoothScrolling(True)
                    window.web.setZoomFactor(1)
                    next_step();poll.start()
                window.page.runJavaScript(js,done)
            QTimer.singleShot(events[-1][0]+500,collect)
        QTimer.singleShot(400,lambda:window.page.runJavaScript(prepare,lambda _:QTimer.singleShot(100,lambda:window.page.runJavaScript('window.__start=performance.now();window.__points=[];',inject))))

    def receive(encoded):
        if not encoded:return
        data=json.loads(encoded)
        if data.get('error'):finish(data['error']);return
        result=data['result']
        if result.get('_wheel'):wheel(result['_wheel']);return
        if result.get('_drag'):
            poll.stop();target=window.web.focusProxy();x=window.web.width()-7
            y=round(result['thumbY']);point=QPoint(x,y)
            QTest.mousePress(target,Qt.LeftButton,Qt.NoModifier,point)
            QTest.qWait(40)
            local=QPointF(x,y+100)
            QApplication.sendEvent(target,QMouseEvent(QEvent.Type.MouseMove,local,
                QPointF(target.mapToGlobal(QPoint(x,y+100))),Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
            QTest.qWait(40)
            QTest.mouseRelease(target,Qt.LeftButton,Qt.NoModifier,QPoint(x,y+100))
            window.page.runJavaScript("window.__dragEnd=document.scrollingElement.scrollTop")
            QTimer.singleShot(250,lambda:window.page.runJavaScript("window.__qa09=__dragEnd>550&&Math.abs(document.scrollingElement.scrollTop-__dragEnd)<1?{result:{dragEnd:__dragEnd,stable:true}}:{error:'scrollbar did not move or kept drifting '+JSON.stringify({end:__dragEnd,now:document.scrollingElement.scrollTop})}"))
            poll.start();return
        poll.stop()
        def capture():
            window.grab().save(str(output/(steps[stage][0]+'.png')))
            results.append({'stage':steps[stage][0],**data});print('PASS',steps[stage][0],data,flush=True)
            next_step()
            if stage<len(steps):poll.start()
        QTimer.singleShot(200,capture)

    def tick():
        if time.monotonic()>deadline:finish('timeout');return
        if stage<0:
            window.page.runJavaScript("!!window.pengpeng?.state.status?.ready && document.querySelectorAll('.card-image img').length>2",lambda ready:next_step() if ready and stage<0 else None)
        else:window.page.runJavaScript('window.__qa09?JSON.stringify(__qa09):null',receive)
    poll=QTimer();poll.timeout.connect(tick);poll.start(250)
    app.exec();sys.exit(1 if failed else 0)
