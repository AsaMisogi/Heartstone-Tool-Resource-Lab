"""WebView2 实际页面高刷新率、Windows 原生输入及业务兼容验收。

与旧 Qt 输入测试不同，本工具在测试窗口中使用 Windows 原生鼠标输入，
不调用自定义滚动控制器。rAF 只采样，不驱动滚轮/拖拽用例的位置。
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
    mp.freeze_support(); job = own_process_tree(); app = QApplication([])
    window = Window(Path('.cache/qa-05-workspace').resolve()); window.show()
    assert window.native_browser
    output = Path('.cache/qa-12'); output.mkdir(exist_ok=True)
    original_cursor=wt.POINT();ctypes.windll.user32.GetCursorPos(ctypes.byref(original_cursor))
    results = []; index = -1; deadline = time.monotonic()+90; failed = False
    steps = [
      ('native-frame-cadence', """(async()=>{closeDetail();await pengpeng.changeView('cards');state.filters={};state.query='';state.favorites=false;state.limit=96;state.offset=0;await refresh();
        scrollTo(0,500);await new Promise(r=>setTimeout(r,700));
        const frames=[];let start=performance.now(),last=start;
        await new Promise(resolve=>{function tick(t){frames.push(t-last);last=t;scrollTo(0,500+(t-start)*1.1);if(t-start<3500)requestAnimationFrame(tick);else resolve();}requestAnimationFrame(tick);});
        const elapsed=last-start;frames.shift();const ordered=[...frames].sort((a,b)=>a-b);
        return {fps:frames.length*1000/elapsed,p95:ordered[Math.floor(ordered.length*.95)],max:Math.max(...frames),images:document.querySelectorAll('.card-image img').length,ua:navigator.userAgent};})()"""),
      ('wheel-single', "Promise.resolve({_input:'single'})"),
      ('wheel-burst', "Promise.resolve({_input:'burst'})"),
      ('wheel-instant', "Promise.resolve({_input:'instant'})"),
      ('scrollbar-drag', "Promise.resolve({_input:'drag'})"),
      ('nested-wheel', """(async()=>{await pengpeng.showCard('NPC_INNKEEPER');await pengpeng.cardTab('voices');return {_input:'nested'};})()"""),
      ('sidebar-scale', """(async()=>{closeDetail();host.setInterfaceScale(1.4);document.documentElement.style.fontSize='21px';await new Promise(r=>setTimeout(r,200));
        document.querySelector('[data-view=settings]').scrollIntoView({block:'nearest'});
        const nav=document.querySelector('[data-view=settings]').getBoundingClientRect(),foot=document.querySelector('.side-bottom').getBoundingClientRect();
        const result={navBottom:nav.bottom,footerTop:foot.top,footerBottom:foot.bottom,height:innerHeight};
        host.setInterfaceScale(1);document.documentElement.style.fontSize='14px';
        if(nav.bottom>foot.top+1||foot.bottom>innerHeight+1)throw Error('sidebar layout '+JSON.stringify(result));return result;})()"""),
      ('native-scale', """(async()=>{closeDetail();host.setInterfaceScale(1.25);await new Promise(r=>setTimeout(r,100));const zoom=getComputedStyle(document.documentElement).zoom;host.setInterfaceScale(1);if(zoom!=='1.25')throw Error('scale');return {zoom};})()"""),
      ('voice-preview', """(async()=>{await pengpeng.showCard('BOT_021');await pengpeng.cardTab('voices');const voice=state.voiceItems.find(x=>x.kind==='voice');await pengpeng.playAsset(voice.id,true);await new Promise(r=>setTimeout(r,250));
        if(audio.paused||!audio.duration)throw Error('audio not playing');const duration=audio.duration;audio.pause();audio.currentTime=.5;await new Promise(r=>setTimeout(r,100));if(Math.abs(audio.currentTime-.5)>.1)throw Error('seek');stopPlayback();return {duration,seek:true};})()"""),
      ('directory-bridge', """new Promise(resolve=>host.chooseDirectory('game', path=>{if(!path.includes('qa-05-workspace'))throw Error('directory callback');resolve({callback:true});}))"""),
      ('settings-bridge', """(async()=>{closeDetail();await pengpeng.changeView('settings');let n=document.querySelector('#scroll-mode');n.value='instant';await n.onchange();let s=await api('status');if(s.settings.scroll_mode!=='instant')throw Error('save');n.value='smooth';await n.onchange();return {saved:true,restored:true};})()"""),
    ]
    if '--no-input' in sys.argv:
        steps=[step for step in steps if step[0] not in {
            'wheel-single','wheel-burst','wheel-instant','scrollbar-drag','nested-wheel'}]

    def finish(error=None):
        global failed
        failed=bool(error);timer.stop()
        if error:results.append({'error':error});print('FAIL',error,flush=True)
        (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf-8')
        ctypes.windll.user32.SetCursorPos(original_cursor.x,original_cursor.y)
        window.close()

    def next_step():
        global index,deadline
        index+=1;deadline=time.monotonic()+90
        if index>=len(steps):finish();return
        name,script=steps[index];print('START',name,flush=True)
        if name=='directory-bridge':window.bridge.chooseDirectory=lambda purpose:str(Path('.cache/qa-05-workspace').resolve())
        window.page.runJavaScript('window.__qa12=null;'+script+'.then(result=>window.__qa12={result}).catch(e=>window.__qa12={error:e.message})')

    def inject(mode):
        timer.stop()
        window.raise_(); window.activateWindow()
        panel="document.querySelector('#detail')" if mode=='nested' else 'document.scrollingElement'
        script=f"""window.__qa12=null;WheelScroll.setMode({'"instant"' if mode=='instant' else '"smooth"'});
          window.__panel={panel};__panel.scrollTop=500;window.__outside=document.scrollingElement.scrollTop;
          window.__points=[];window.__sample=true;window.__start=performance.now();
          function sample(t){{__points.push([t,__panel.scrollTop]);if(__sample)requestAnimationFrame(sample);}}requestAnimationFrame(sample);
          JSON.stringify({{height:innerHeight,content:__panel.scrollHeight,width:innerWidth}})"""
        def prepared(encoded):
            geometry=json.loads(encoded);handle=int(window.winId());user=ctypes.windll.user32
            user.ClientToScreen.argtypes=[wt.HWND,ctypes.POINTER(wt.POINT)]
            user.SetForegroundWindow.argtypes=[wt.HWND]
            user.SetForegroundWindow(handle)
            user.WindowFromPoint.argtypes=[wt.POINT];user.WindowFromPoint.restype=wt.HWND
            user.GetAncestor.argtypes=[wt.HWND,wt.UINT];user.GetAncestor.restype=wt.HWND
            dpr=window.devicePixelRatioF()
            def own_point(point):
                # 真实输入只能发到测试窗口；前台被其他窗口覆盖时停止测试。
                if user.GetAncestor(user.WindowFromPoint(point),2)!=handle:
                    finish('测试窗口被遮挡，未发送鼠标输入');return False
                return True
            def wheel():
                point=wt.POINT(round((1150 if mode=='nested' else 700)*dpr),round(450*dpr))
                user.ClientToScreen(handle,ctypes.byref(point))
                if not own_point(point):return
                user.SetCursorPos(point.x,point.y);user.mouse_event(0x0800,0,0,ctypes.c_ulong(-120).value,0)
            if mode=='drag':
                h=geometry['height'];content=geometry['content'];track=h-34;thumb=track*h/content
                y=17+500/(content-h)*(track-thumb)+thumb/2;x=geometry['width']-8
                def mouse(message,y,button):
                    point=wt.POINT(round(x*dpr),round(y*dpr));user.ClientToScreen(handle,ctypes.byref(point));user.SetCursorPos(point.x,point.y)
                    if not own_point(point):return
                    if message==0x201:user.mouse_event(0x0002,0,0,0,0)
                    elif message==0x202:user.mouse_event(0x0004,0,0,0,0)
                QTimer.singleShot(100,lambda:mouse(0x201,y,1))
                for i in range(1,31):QTimer.singleShot(100+i*10,lambda i=i:mouse(0x200,y+i*4,1))
                QTimer.singleShot(430,lambda:mouse(0x202,y+120,0))
            else:
                for i in range(10 if mode=='burst' else 1):QTimer.singleShot(100+i*25,wheel)
            def complete():
                window.page.runJavaScript("""(()=>{__sample=false;WheelScroll.setMode('smooth');const changed=__points.filter((p,i)=>i&&p[1]!==__points[i-1][1]);
                  window.__qa12={result:{mode:"""+json.dumps(mode)+""",total:__panel.scrollTop-500,changes:changed.length,outer:document.scrollingElement.scrollTop-__outside,
                  points:changed.map(p=>[p[0]-__points[0][0],p[1]-500])}};})()""")
                timer.start()
            QTimer.singleShot(1300,complete)
        window.page.runJavaScript(script,prepared)

    def receive(encoded):
        if not encoded:return
        data=json.loads(encoded)
        if data.get('error'):finish(data['error']);return
        result=data['result']
        if result.get('_input'):inject(result['_input']);return
        name=steps[index][0]
        if name=='native-frame-cadence' and result['fps']<window.screen().refreshRate()*.75:
            finish('页面帧率未达到显示器刷新率的 75%：'+str(result));return
        if name in ('wheel-single','wheel-instant','nested-wheel') and abs(result['total']-100)>1:
            finish('滚轮距离不正确：'+str(result));return
        if name=='wheel-burst' and abs(result['total']-1000)>1:finish('连续距离不正确');return
        if name=='scrollbar-drag' and result['total']<300:finish('未拖动原生滑块');return
        if name=='nested-wheel' and result['outer']!=0:finish('详情滚动穿透');return
        results.append({'stage':name,**data});print('PASS',name,{k:v for k,v in result.items() if k!='points'},flush=True)
        if name=='native-frame-cadence':window.grab().save(str(output/'cards.png'))
        next_step()

    def poll():
        if time.monotonic()>deadline:finish('timeout');return
        if index<0:
            window.page.runJavaScript('!!window.pengpeng?.state.status?.ready',lambda ready:next_step() if ready and index<0 else None)
        else:window.page.runJavaScript('window.__qa12?JSON.stringify(__qa12):null',receive)
    timer=QTimer();timer.timeout.connect(poll);timer.start(200)
    app.exec();sys.exit(1 if failed else 0)
