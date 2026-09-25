const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync('pengpeng/web/scrolling.js', 'utf8');
const ScrollMotion = runInNewContext(source + '; ScrollMotion;');

test('不同帧率总距离一致，90ms 有限收尾而非渐近追赶', () => {
  for (const hz of [30,60,120,144]) {
    const m = new ScrollMotion(), n = {scrollTop:0};
    m.add(n,'scrollTop',100,10000,0);
    let last = 0;
    for (let t=1000/hz;t<160;t+=1000/hz) {
      m.sample(t); assert.ok(n.scrollTop >= last && n.scrollTop <= 100); last=n.scrollTop;
    }
    assert.equal(n.scrollTop,100); assert.equal(m.tracks.length,0);
  }
});
test('连续同向输入保留距离和帧速度，反向首帧立即反向', () => {
  const m = new ScrollMotion(), n = {scrollTop:500};
  for(let i=0;i<10;i++) {m.sample(i*25);m.add(n,'scrollTop',100,10000,i*25);}
  m.sample(400); assert.equal(n.scrollTop,1500);
  m.add(n,'scrollTop',100,10000,500);m.sample(530);
  const before=n.scrollTop;
  m.add(n,'scrollTop',-100,10000,531);m.sample(547);
  assert.ok(n.scrollTop<before);m.sample(650);assert.equal(n.scrollTop,before-100);
});
test('滚动条/程序定位改变实际位置后，旧动画不回拉；即时输入无动画', () => {
  const m=new ScrollMotion(), n={scrollTop:0};
  m.add(n,'scrollTop',100,1000,0);m.sample(16);n.scrollTop=700;m.sample(32);
  assert.equal(n.scrollTop,700);assert.equal(m.tracks.length,0);
  m.add(n,'scrollTop',500,1000,50,true);assert.equal(n.scrollTop,1000);
  assert.equal(m.tracks.length,0);
});

function page() {
  const root={scrollTop:0,scrollLeft:0,clientHeight:500,scrollHeight:2000,clientWidth:300,scrollWidth:600,
    style:{overflowY:'auto',overflowX:'auto'},parentElement:null};
  let hit=root;const frames=[];
  const api=runInNewContext(source+'; WheelScroll;',{
    performance:{now:()=>0},requestAnimationFrame:fn=>(frames.push(fn),frames.length),cancelAnimationFrame:()=>{},
    document:{hidden:false,scrollingElement:root,elementFromPoint:()=>hit,addEventListener:()=>{},
      documentElement:{classList:{add:()=>{},remove:()=>{}}}},window:{addEventListener:()=>{}},getComputedStyle:n=>n.style
  });
  api.setMode('instant');
  return {api,root,frames,hit:n=>hit=n};
}
test('整页按容器高度换算，嵌套容器边界不穿透，横向独立',()=>{
  const p=page();p.api.input({x:1,y:1,dy:1,dx:0,pageY:true});assert.equal(p.root.scrollTop,460);
  const child={...p.root,scrollTop:0,clientHeight:100,scrollHeight:100,parentElement:p.root,
    style:{overflowY:'auto',overscrollBehaviorY:'contain'}};
  p.hit(child);p.api.input({x:1,y:1,dy:100,dx:0});assert.equal(p.root.scrollTop,460);
  child.scrollHeight=500;p.api.input({x:1,y:1,dy:100,dx:0});assert.equal(child.scrollTop,100);
  p.hit(p.root);p.api.input({x:1,y:1,dy:0,dx:50});assert.equal(p.root.scrollLeft,50);
});
test('减少动画与像素手势直接定位，不创建补间动画',()=>{
  const p=page();p.api.setMode('smooth');p.api.setReduced(true);
  p.api.input({x:1,y:1,dy:50,dx:0});assert.equal(p.root.scrollTop,50);
  p.api.setReduced(false);p.api.input({x:1,y:1,dy:12.5,dx:0,precise:true});
  assert.equal(p.root.scrollTop,62.5);assert.equal(p.frames.length,0);
});

test('原生平滑模式没有阻塞 wheel 监听器，即时/减少动画按需接管',()=>{
  const listeners=new Map();const document={addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),
    documentElement:{classList:{add:()=>{},remove:()=>{}}}};
  const api=runInNewContext(source+';WheelScroll;', {document,cancelAnimationFrame:()=>{}});
  api.useNative();assert.equal(listeners.has('wheel'),false);
  api.setMode('instant');assert.equal(listeners.has('wheel'),true);
  api.setMode('smooth');assert.equal(listeners.has('wheel'),false);
  api.setReduced(true);assert.equal(listeners.has('wheel'),true);
  api.setReduced(false);assert.equal(listeners.has('wheel'),false);
});
