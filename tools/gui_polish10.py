"""真实 Qt / Chromium 验收：输入延迟、拖尾、嵌套滚动、弹窗和声音时间轴。

调用系统滚轮事件，逐帧采样真实页面位置；数字是本机测量，不代表主观手感。
普通刻度、连滚、反向及嵌套场景使用相同页面，结果记录实际帧间隔和尾声。
"""
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineSettings
from pengpeng.app import Window, own_process_tree


if __name__ == '__main__':
    mp.freeze_support(); job = own_process_tree(); app = QApplication([])
    window = Window(Path('.cache/qa-05-workspace').resolve()); window.show()
    output=Path('.cache/qa-10');output.mkdir(exist_ok=True)
    results=[]; stage=-1; failed=False; deadline=time.monotonic()+120
    steps=[
        ('responsive-wheel', """(async()=>{closeDetail();await pengpeng.changeView('cards');state.filters={};state.query='';state.favorites=false;state.limit=96;state.offset=0;await refresh();return {_wheel:'single'};})()"""),
        ('wheel-burst', """Promise.resolve({_wheel:'burst'})"""),
        ('wheel-reverse', """Promise.resolve({_wheel:'reverse'})"""),
        ('reduced-motion', """Promise.resolve({_wheel:'reduced'})"""),
        ('nested-wheel', """(async()=>{await pengpeng.showCard('NPC_INNKEEPER');await pengpeng.cardTab('voices');return {_wheel:'nested'};})()"""),
        ('sidebar-fit', """(async()=>{closeDetail();host.setInterfaceScale(1.4);document.documentElement.style.fontSize='21px';await new Promise(r=>setTimeout(r,300));
          const side=document.querySelector('.sidebar'),last=side.querySelector('[data-view=settings]').getBoundingClientRect(),bottom=side.querySelector('.side-bottom').getBoundingClientRect();
          if(last.bottom>innerHeight||last.bottom>bottom.top||bottom.bottom>innerHeight+1||getComputedStyle(side).overflowY!=='hidden')throw Error('sidebar overflow '+JSON.stringify({last:last.bottom,bottom:bottom.bottom,h:innerHeight}));
          return {navigationBottom:last.bottom,footerBottom:bottom.bottom,height:innerHeight};})()"""),
        ('treasure-fixed-close', """(async()=>{host.setInterfaceScale(1);document.documentElement.style.fontSize='14px';await pengpeng.showCard('ONY_005');document.querySelector('[data-text-relation]').click();
          await new Promise(r=>setTimeout(r,400));const list=document.querySelector('#relation-choices');list.scrollTop=list.scrollHeight;await new Promise(r=>setTimeout(r,700));
          if(![...list.querySelectorAll('img')].some(im=>im.naturalWidth>0))throw Error('treasure thumbnails missing');
          const b=document.querySelector('#relation-close').getBoundingClientRect(),d=document.querySelector('#relation-dialog').getBoundingClientRect();
          if(b.top<d.top||b.bottom> d.top+90||!list.scrollTop)throw Error('close scrolled away');
          return {_click:true,x:d.left-20,y:d.top+100};})()"""),
        ('music-scenes', """(async()=>{closeDetail();await pengpeng.changeView('audio');state.category='背景音乐';state.query='';state.subgroup='';state.favorites=false;state.filters={};await refresh();
          if(state.total<100||document.querySelectorAll('#audio-subgroup option').length<5)throw Error('music library incomplete');
          const count=state.total;state.subgroup='英雄主题音乐';await refresh();
          if(!document.querySelector('.audio-annotation')||state.total<100)throw Error('scene or annotations');
          return {total:count,heroes:state.total,annotations:[...document.querySelectorAll('.audio-annotation')].slice(0,5).map(x=>x.textContent)};})()"""),
        ('sample-accurate-preview', """(async()=>{await api('save_settings',{paired_audio:false,general_audio:false});state.pairedAudio=false;state.generalAudio=false;await pengpeng.showCard('BOT_021');await pengpeng.cardTab('voices');if(!document.querySelector('#paired-audio').checked)document.querySelector('#paired-audio').click();if(!document.querySelector('#general-audio').checked)document.querySelector('#general-audio').click();await Promise.resolve();
          const voice=state.voiceItems.find(x=>x.kind==='voice'&&x.group==='play');await pengpeng.playAsset(voice.id,true);await new Promise(r=>setTimeout(r,300));
          if(!audio.src.includes('audio-timelines')||audio.paused||document.querySelector('#audio-timeline') !== null)throw Error('timeline playback');
          document.querySelector('#play-pause').click();await new Promise(r=>setTimeout(r,50));if(!audio.paused)throw Error('pause');
          audio.currentTime=.8;await new Promise(r=>setTimeout(r,150));if(Math.abs(audio.currentTime-.8)>.05)throw Error('seek');
          await pengpeng.playAsset(voice.id,true);await new Promise(r=>setTimeout(r,100));if(audio.currentTime>.5)throw Error('replay');
          const result={duration:audio.duration,timelineHidden:!document.querySelector('#audio-timeline'),mediaClocks:1};stopPlayback();return result;})()"""),
    ]

    if '--content' in sys.argv:
        steps = [step for step in steps if step[0] in ('music-scenes', 'sample-accurate-preview')]

    steps.extend([
        ('classic-music', """(async()=>{closeDetail();await pengpeng.changeView('audio');state.category='背景音乐';state.subgroup='经典默认音乐';state.query='';await refresh();
          if(state.total!==11 || !document.querySelector('#results').textContent.includes('Duel'))throw Error('classic music');
          return {total:state.total,names:[...document.querySelectorAll('.list-row b')].map(x=>x.textContent)};})()"""),
        ('immune-filter', """(async()=>{await pengpeng.changeView('cards');state.filters={keyword:'免疫',collectible:'1'};state.query='';await refresh();
          if(state.total<20)throw Error('immune filter incomplete');return {total:state.total,names:[...document.querySelectorAll('.tile-name')].slice(0,8).map(x=>x.textContent)};})()"""),
        ('page-index-message', """(async()=>{receive(JSON.stringify({event:'scan_finished',scope:'music',status:state.status}));await new Promise(r=>setTimeout(r,50));
          const text=document.querySelector('#toast').textContent;if(!text.includes('当前页面索引已完成'))throw Error(text);return {text};})()"""),
        ('full-index-message', """(async()=>{receive(JSON.stringify({event:'scan_finished',scope:'all',status:state.status}));await new Promise(r=>setTimeout(r,50));
          const text=document.querySelector('#toast').textContent;if(!text.includes('完整资源索引已完成'))throw Error(text);return {text};})()"""),
    ])

    def finish(error=None):
        global failed
        failed=bool(error);poll.stop()
        if error:results.append({'error':error});print('FAIL',error,flush=True)
        (output/('content-results.json' if '--content' in sys.argv else 'results.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf8')
        window.close()

    def next_step():
        global stage,deadline
        stage+=1;deadline=time.monotonic()+120
        if stage>=len(steps):finish();return
        name,js=steps[stage];print('START',name,flush=True)
        if name=='sidebar-fit':window.resize(1060,720)
        if name=='treasure-fixed-close':window.resize(1440,940)
        window.page.runJavaScript('window.__qa09=null;'+js+'.then(result=>window.__qa09={result}).catch(e=>window.__qa09={error:e.message});')

    def wheel(mode):
        poll.stop()
        window.bridge.setSmoothScrolling(mode != 'reduced')
        window.page.settings().setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, mode != 'reduced')
        selector="document.querySelector('#detail')" if mode=='nested' else 'document.scrollingElement'
        prepare=f"""window.__qa09=null;window.__wheelPanel={selector};__wheelPanel.scrollTop=500;
          window.__points=[];window.__sampling=true;window.__start=performance.now();window.__outside=document.scrollingElement.scrollTop;
          function sample(t){{__points.push([performance.now()-__start,__wheelPanel.scrollTop]);if(__sampling)requestAnimationFrame(sample);}}requestAnimationFrame(sample);"""
        def inject(_):
            point=QPoint(1150,450) if mode=='nested' else QPoint(700,350)
            target=window.web.focusProxy()
            events=[(i*25,-120) for i in range(10)] if mode=='burst' else [(0,-120),(40,120)] if mode=='reverse' else [(0,-120)]
            def send(angle):
                event=QWheelEvent(QPointF(point),QPointF(target.mapToGlobal(point)),QPoint(),QPoint(0,angle),Qt.NoButton,Qt.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
                QApplication.sendEvent(target,event)
            for delay,angle in events:QTimer.singleShot(delay,lambda a=angle:send(a))
            def collect():
                js="""(()=>{window.__sampling=false;const p=[[0,500],...__points],changes=p.filter((v,i)=>i&&v[1]!==p[i-1][1]);
                  const intervals=p.slice(1).map((v,i)=>v[0]-p[i][0]).sort((a,b)=>a-b);
                  return JSON.stringify({positions:p,firstMs:changes[0]?.[0],lastMs:changes.at(-1)?.[0],steps:changes.length,
                    p95FrameMs:intervals[Math.floor(intervals.length*.95)],total:p.at(-1)[1]-500,outside:document.scrollingElement.scrollTop-__outside});})()"""
                def done(encoded):
                    data=json.loads(encoded);data['tailMs']=data.get('lastMs',999)-events[-1][0]
                    if not data['steps']:
                        results.append(data);window.grab().save(str(output/'failed.png'));window.page.runJavaScript("JSON.stringify({h:document.scrollingElement.scrollHeight,client:innerHeight,overflow:getComputedStyle(document.body).overflow,dialogs:[...document.querySelectorAll('dialog[open]')].map(x=>x.id),target:document.elementFromPoint(700,350)?.outerHTML.slice(0,300)})",lambda value:print('DIAG',value,flush=True));QTimer.singleShot(200,lambda:finish('wheel produced no movement: '+mode));return
                    if mode != 'reduced' and data['tailMs']>300:finish('wheel tail exceeded 300ms: '+mode);return
                    if mode=='nested' and data['outside']!=0:finish('nested scroll escaped');return
                    if mode=='reduced' and data['steps']>2:
                        results.append({'stage':'reduced-motion','result':data});print(data,flush=True);finish('reduced motion animated');return
                    results.append({'stage':steps[stage][0],'result':data})
                    print('PASS',steps[stage][0],{k:v for k,v in data.items() if k!='positions'},flush=True)
                    window.bridge.setSmoothScrolling(True)
                    window.page.settings().setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled,True)
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
        if result.get('_click'):
            window.grab().save(str(output/'treasure-dialog.png'))
            poll.stop();window.page.runJavaScript('window.__qa09=null')
            scale=window.web.zoomFactor();point=QPoint(round(result['x']*scale),round(result['y']*scale))
            QTest.mouseClick(window.web.focusProxy(),Qt.LeftButton,Qt.NoModifier,point)
            QTimer.singleShot(150,lambda:window.page.runJavaScript("window.__qa09=document.querySelector('#relation-dialog').open?{error:'outside click did not close'}:{result:{outsideClosed:true}}"))
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
