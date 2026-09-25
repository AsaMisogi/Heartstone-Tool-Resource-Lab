"""0.5 真实 Qt 回归：聚合、词条气泡、关联返回、专属语音、场景角色与滚动。

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
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pengpeng.app import Window, own_process_tree


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    app = QApplication([])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('.cache/qa-05-workspace'))
    args = parser.parse_args()
    window = Window(args.workspace.resolve())
    window.show()
    output = Path('.cache/qa-05')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('gallery', "Promise.resolve({cards:pengpeng.state.status.cards,images:document.querySelectorAll('.card-image img').length})"),
        ('grouped', "(async()=>{const r=await pengpeng.api('list_cards',{query:'奇利亚斯豪华版3000型'});if(r.total!==1||r.items[0].version_count!==9)throw Error('group count');await pengpeng.showCard(r.items[0].id);document.querySelector('.edition-panel').open=true;return {total:r.total,versions:r.items[0].version_count};})()"),
        ('keyword', "(async()=>{await pengpeng.showCard('BOT_451');const b=[...document.querySelectorAll('[data-keyword]')].find(x=>x.textContent==='突袭');if(!b)throw Error('keyword missing');b.click();const text=document.querySelector('.keyword-popover').textContent;if(!text.includes('立即攻击随从'))throw Error('keyword text');return {text};})()"),
        ('related-return', "(async()=>{document.querySelector('.keyword-popover')?.remove();await pengpeng.showCard('BOT_102t','related');if(!document.querySelector('#back-card'))throw Error('no back');document.querySelector('#back-card').click();await new Promise(r=>setTimeout(r,600));if(pengpeng.state.card.id!=='BOT_451')throw Error('wrong return');return {id:pengpeng.state.card.id};})()"),
        ('husk-voices', "(async()=>{await pengpeng.showCard('HERO_11aq');await pengpeng.cardTab('voices');const names=pengpeng.state.voiceItems.filter(x=>x.trigger_card).map(x=>x.event);if(!names.some(x=>x.includes('灵界打击'))||!names.some(x=>x.includes('窒息')))throw Error('missing trigger');return {names};})()"),
        ('scarlet-voices', "(async()=>{await pengpeng.showCard('HERO_11s_Scarlet_hls');await pengpeng.cardTab('voices');if(!pengpeng.state.voiceItems.some(x=>x.trigger_card&&x.event.includes('邪爆')))throw Error('missing scarlet');const q=document.querySelector('#voice-search');q.value='邪爆';q.dispatchEvent(new Event('input'));return {rows:document.querySelectorAll('.voice-row').length};})()"),
        ('bob-voices', "(async()=>{await pengpeng.showCard('TB_BaconShopBob_SKIN_E');await pengpeng.cardTab('voices');if(pengpeng.state.voiceItems.length<30)throw Error('npc voices');return {voices:pengpeng.state.voiceItems.length};})()"),
        ('scroll', "(async()=>{await pengpeng.showCard('NPC_INNKEEPER');await pengpeng.cardTab('voices');const d=document.querySelector('#detail');for(let i=0;i<30;i++){d.scrollTop=i%2?0:d.scrollHeight;await new Promise(r=>requestAnimationFrame(r));}return {rows:document.querySelectorAll('.voice-row').length,overflow:document.documentElement.scrollWidth>innerWidth};})()"),
        ('audio', "(async()=>{const id=document.querySelector('[data-play]').dataset.play;await pengpeng.playAsset(id);const a=document.querySelector('#audio');await new Promise((r,j)=>{if(a.readyState>=2)r();else{a.addEventListener('canplay',r,{once:true});setTimeout(()=>j(Error('audio timeout')),10000);}});return {duration:a.duration,ready:a.readyState};})()"),
        ('golden', "(async()=>{await pengpeng.showCard('EX1_116');pengpeng.state.renderVariant=1;await pengpeng.cardTab('render');const v=document.querySelector('#full-card');if(!v||v.tagName!=='VIDEO')throw Error('missing video');await new Promise((r,j)=>{if(v.readyState>=2)r();else{v.addEventListener('loadeddata',r,{once:true});v.addEventListener('error',()=>j(Error('video decode')), {once:true});setTimeout(()=>j(Error('video timeout')),10000);}});return {ready:v.readyState,width:v.videoWidth,height:v.videoHeight,duration:v.duration};})()"),
        ('signature', "(async()=>{await pengpeng.showCard('JAIL_446');pengpeng.state.renderVariant=2;await pengpeng.cardTab('render');const v=document.querySelector('#full-card');if(!v||v.tagName!=='VIDEO')throw Error('missing video');await new Promise((r,j)=>{if(v.readyState>=2)r();else{v.addEventListener('loadeddata',r,{once:true});v.addEventListener('error',()=>j(Error('video decode')), {once:true});setTimeout(()=>j(Error('video timeout')),10000);}});return {ready:v.readyState,width:v.videoWidth,height:v.videoHeight,duration:v.duration};})()"),
        ('diamond', "(async()=>{await pengpeng.showCard('LOE_011');pengpeng.state.renderVariant=3;await pengpeng.cardTab('render');const v=document.querySelector('#full-card');if(!v||v.tagName!=='VIDEO')throw Error('missing video');await new Promise((r,j)=>{if(v.readyState>=2)r();else{v.addEventListener('loadeddata',r,{once:true});v.addEventListener('error',()=>j(Error('video decode')), {once:true});setTimeout(()=>j(Error('video timeout')),10000);}});return {ready:v.readyState,width:v.videoWidth,height:v.videoHeight,duration:v.duration};})()"),
        ('npc-gallery', "(async()=>{await pengpeng.changeView('heroes');document.querySelector('#npc-shortcut').click();await new Promise(r=>setTimeout(r,1500));return {total:pengpeng.state.total};})()"),
    ]
    stages.append(('render-navigation', """(async()=>{
        await pengpeng.showCard('EX1_116');
        pengpeng.state.renderVariant=1; await pengpeng.cardTab('render');
        await pengpeng.showCard('BOT_451','related');
        if(pengpeng.state.renderVariant!==0)throw Error('quality not reset');
        document.querySelector('#back-card').click();
        for(let i=0;i<100;i++){
            await new Promise(r=>setTimeout(r,50));
            if(document.querySelector('#full-card')?.tagName==='VIDEO')break;
        }
        if(pengpeng.state.card.id!=='EX1_116'||pengpeng.state.renderVariant!==1)throw Error('quality not restored');
        const v=document.querySelector('#full-card');
        if(!v)throw Error('video not restored');
        document.querySelector('#close-detail').click();
        if(!v.paused)throw Error('hidden video still playing');
        return {restored:1,paused:v.paused};
    })()"""))
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
