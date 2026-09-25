"""0.7 真实 Qt 页面验收阶段，由 gui_experience.py --interactions 执行。"""

STAGES = [
    ('voice-groups', """(async()=>{
      await pengpeng.showCard('HERO_11');await pengpeng.cardTab('voices');
      const groups=document.querySelector('#voice-groups');
      const total=Number(groups.querySelector('[data-voice-group=all] span').textContent);
      const sum=[...groups.querySelectorAll('button:not([data-voice-group=all]) span')].reduce((n,b)=>n+Number(b.textContent),0);
      if(total!==sum)throw Error('group partition');
      const counts={};
      for(const key of ['interaction','trigger','adventure','holiday','prompt']){
        const b=groups.querySelector(`[data-voice-group=${key}]`);b.click();
        counts[key]=Number(b.querySelector('span').textContent);
        if(!counts[key]||b.getAttribute('aria-pressed')!=='true')throw Error('empty group '+key);
        if(document.querySelectorAll('[data-voice-row]').length>pengpeng.state.voicePageSize)throw Error('unbounded rows');
      }
      groups.querySelector('[data-voice-group=adventure]').click();
      const query=document.querySelector('#voice-search');query.value='面对';query.dispatchEvent(new Event('input'));
      if(document.querySelectorAll('[data-voice-row]').length!==9)throw Error('adventure search');
      const b=groups.querySelector('[data-voice-group=adventure]');b.focus();renderVoiceList();
      if(document.activeElement!==b)throw Error('group focus lost');
      query.value='';query.dispatchEvent(new Event('input'));
      const panel=document.querySelector('#detail');const height=panel.scrollHeight;
      for(let i=0;i<25;i++){panel.scrollTop=i*60;await new Promise(r=>requestAnimationFrame(r));if(panel.scrollHeight!==height)throw Error('scroll geometry changed');}
      panel.scrollTop=groups.offsetTop-40;
      return {total,counts,stableHeight:height};
    })()"""),
    ('scrollbar-native-drag', """(async()=>{
      const panel=document.querySelector('#detail');panel.scrollTop=0;
      await new Promise(r=>requestAnimationFrame(r));
      const r=panel.getBoundingClientRect();
      return {_scrollbar:true,x:r.right-7,y:r.top+20,width:0,height:r.height};
    })()"""),
    ('waveform-native-drag', """(async()=>{
      const b=document.querySelector('[data-play]');await pengpeng.playAsset(b.dataset.play);
      const a=document.querySelector('#audio');
      for(let i=0;i<100&&!Number.isFinite(a.duration);i++)await new Promise(r=>setTimeout(r,50));
      a.pause();a.currentTime=0;await new Promise(r=>setTimeout(r,100));
      const c=document.querySelector('#waveform');c.focus();
      c.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}));
      if(Math.abs(a.currentTime-a.duration)>.05)throw Error('End key');
      c.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}));
      if(a.currentTime!==0)throw Error('Home key');
      c.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',shiftKey:true,bubbles:true}));
      if(Math.abs(a.currentTime-Math.min(1,a.duration))>.05)throw Error('fine seek');
      a.currentTime=0;await new Promise(r=>setTimeout(r,100));
      window.__seekCount=0;window.__dragMoves=0;window.__wavePaints=0;
      a.addEventListener('seeking',()=>window.__seekCount++);
      c.addEventListener('pointermove',()=>{if(waveform.drag)window.__dragMoves++;});
      const original=waveform.paint.bind(waveform);waveform.paint=()=>{window.__wavePaints++;original();};
      const r=c.getBoundingClientRect();
      return {_drag:true,x:r.x,y:r.y,width:r.width,height:r.height};
    })()"""),
    ('waveform-cancel-drag', """(async()=>{
      const a=document.querySelector('#audio'),c=document.querySelector('#waveform');
      window.__beforeCancel=a.currentTime;window.__seekCount=0;window.__dragMoves=0;
      const r=c.getBoundingClientRect();
      return {_drag:true,cancel:true,x:r.x,y:r.y,width:r.width,height:r.height};
    })()"""),
    ('waveform-lifecycle', """(async()=>{
      const a=document.querySelector('#audio');
      const count=window.__wavePaints;await new Promise(r=>setTimeout(r,150));
      if(window.__wavePaints!==count)throw Error('paused animation spinning');
      a.currentTime=0;await a.play();await new Promise(r=>setTimeout(r,160));
      a.pause();await new Promise(r=>setTimeout(r,80));
      if(window.__wavePaints-count<3)throw Error('playback not animated');
      const c=document.querySelector('#waveform');
      if(c.width!==Math.round(c.getBoundingClientRect().width*devicePixelRatio))throw Error('DPI canvas');
      document.querySelector('#stop-player').click();await new Promise(r=>setTimeout(r,100));
      if(waveform.drag||waveform.peaks.length||waveform.frame)throw Error('stop cleanup');
      return {idleFrames:0,playbackFrames:window.__wavePaints-count,keyboard:true,dpi:devicePixelRatio};
    })()"""),
    ('button-feedback', """(async()=>{
      const b=document.createElement('button');document.body.append(b);
      let calls=0; b.onclick=guarded(async()=>{calls++;await new Promise(r=>setTimeout(r,80));});
      b.click();b.click();
      if(b.getAttribute('aria-busy')!=='true')throw Error('busy feedback');
      await new Promise(r=>setTimeout(r,120));
      if(calls!==1||b.hasAttribute('aria-busy'))throw Error('repeat click');
      b.remove();
      const p=document.querySelector('[data-play]').getBoundingClientRect();
      if(p.width<44||p.height<44)throw Error('small target');
      return {calls,target:[p.width,p.height]};
    })()"""),
]
