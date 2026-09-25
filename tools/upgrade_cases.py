"""0.8 Qt 验收：延迟注入仅控制完成顺序，其余流程使用真实本地数据。"""
STAGES = [
 ('smooth-wheel', """(async()=>{
   await pengpeng.changeView('cards');state.filters={};state.query='';document.querySelector('#search').value='';await pengpeng.refresh();
   const panel=document.scrollingElement;panel.scrollTop=0;
   window.__wheelDone=false;window.__wheelSamples=[];
   function sample(){window.__wheelSamples.push(panel.scrollTop);if(!window.__wheelDone)requestAnimationFrame(sample);}requestAnimationFrame(sample);
   return {_wheel:true};
 })()"""),
 ('rapid-tabs', """(async()=>{
   await pengpeng.showCard('HERO_01aa'); const original=api;
   api=async(method,params)=>{if(['portrait','card_audio'].includes(method))await new Promise(r=>setTimeout(r,250));return original(method,params);};
   try {
     const sequence=['voices','art','voices','raw','render','voices','art'];
     for(const tab of sequence){document.querySelector(`[data-tab="${tab}"]`).click();await new Promise(r=>setTimeout(r,15));}
     await new Promise(r=>setTimeout(r,2500));
     const active=[...document.querySelectorAll('.detail-tabs > .active')].map(b=>b.dataset.tab);
     if(active.join()!=='art'||!document.querySelector('#large-portrait')||document.querySelector('[data-tab][aria-busy="true"]'))throw Error('stale tab '+active);
     return {sequence,active,portrait:true};
   } finally {api=original;}
 })()"""),
 ('stale-error', """(async()=>{
   const original=api;
   api=async(method,params)=>{if(method==='portrait'){await new Promise(r=>setTimeout(r,200));throw Error('QA old portrait failure');}return original(method,params);};
   try {const old=pengpeng.cardTab('art');await pengpeng.cardTab('voices');await old;
     if(!document.querySelector('#voice-list .voice-row')||document.querySelector('#detail-body').textContent.includes('QA old'))throw Error('stale error replaced current view');
     return {current:'voices',oldFailureIgnored:true};
   }finally{api=original;}
 })()"""),
 ('voice-ownership', """(async()=>{
   const report={};
   for(const cid of ['HERO_01aa','HERO_01','HERO_11aq']){
     await pengpeng.showCard(cid);await pengpeng.cardTab('voices');
     const items=pengpeng.state.voiceItems.filter(x=>x.kind==='voice');
     const bad=items.filter(x=>x.trigger_card && x.condition_raw?.m_SideToSearch!==3);
     if(bad.length||items.length<30)throw Error('ownership '+cid+' '+items.length);
     report[cid]={voices:items.length,conditions:items.filter(x=>x.condition).length};
   }return report;
 })()"""),
 ('latest-play-and-replay', """(async()=>{
   state.pairedAudio=false;state.generalAudio=false; const rows=state.voiceItems.filter(x=>x.kind==='voice');
   const a=rows[0].id,b=rows[1].id;
   await Promise.all([pengpeng.playAsset(a),pengpeng.playAsset(b)]);await new Promise(r=>setTimeout(r,350));
   if(currentAudio.assetid!==b||audio.paused)throw Error('latest playback lost');
   audio.currentTime=Math.min(.8,audio.duration*.6);await pengpeng.playAsset(b,true);
   await new Promise(r=>setTimeout(r,120));
   if(audio.paused||audio.currentTime>.6||state.preparingAudio)throw Error('replay failed');
   document.querySelector('#stop-player').click();return {latest:b,replay:true};
 })()"""),
 ('hero-card-music', """(async()=>{
   const result={};state.pairedAudio=true;state.generalAudio=false;
   for(const cid of ['ICC_829','ICC_830']){
     await pengpeng.showCard(cid);await pengpeng.cardTab('voices');
     const v=state.voiceItems.find(x=>x.kind==='voice'&&x.group==='play');
     const sounds=state.voiceItems.filter(x=>x.kind==='sound'&&x.group===v.group&&x.paired!==false);
     if(!sounds.some(x=>x.name.startsWith(cid==='ICC_829'?'DK_Uther':'DK_Anduin')))throw Error('missing hero music');
     await pengpeng.playAsset(v.id);await new Promise(r=>setTimeout(r,150));
     if(!document.querySelector('#audio-timeline').textContent.includes('待确定'))throw Error('missing timing evidence');
     result[cid]=sounds.map(x=>x.name);stopPlayback();
   }return result;
 })()"""),
 ('battlegrounds', """(async()=>{
   await pengpeng.changeView('battlegrounds');state.filters={tier:'3',race:'20'};renderFilters();await pengpeng.refresh();
   const data=await api('list_cards',{battlegrounds:true,filters:state.filters});
   if(!data.total||data.items.some(c=>!c.summary.battlegrounds||c.summary.tier!==3||!c.summary.races.includes('野兽')))throw Error('BG filters');
   await pengpeng.showCard(data.items[0].id);
   if(!document.querySelector('.card-facts').textContent.includes('酒馆等级'))throw Error('BG detail');
   return {total:data.total,first:data.items[0].id};
 })()"""),
 ('relations', """(async()=>{
   const report={};
   for(const cid of ['AV_337','CORE_AV_337','DINO_410','BOT_451','LOE_092','DAL_417','ONY_005']){
     await pengpeng.showCard(cid);const links=[...document.querySelectorAll('[data-text-relation]')];
     if(!links.length)throw Error('missing text link '+cid);
     report[cid]=links.map(b=>b.textContent);
   }
   document.querySelector('[data-text-relation]').click();await new Promise(r=>setTimeout(r,600));
   if(!document.querySelector('#relation-dialog').open||document.querySelectorAll('[data-choice]').length<20)throw Error('treasure choices');
   return report;
 })()"""),
 ('new-filter-and-update', """(async()=>{
   document.querySelector('#relation-dialog').close();closeDetail();await pengpeng.changeView('cards');
   document.querySelector('#only-new').checked=true;document.querySelector('#only-new').dispatchEvent(new Event('change'));
   await new Promise(r=>setTimeout(r,200));if(state.filters.new!=='1')throw Error('NEW filter');
   document.querySelector('#only-new').checked=false;document.querySelector('#only-new').dispatchEvent(new Event('change'));
   showGameUpdate({fingerprint:'qa-fixture',game_version:'测试版本'});
   if(!document.querySelector('#game-update-dialog').open)throw Error('update modal');
   return {newFilter:true,updateGuide:true};
 })()"""),
]
