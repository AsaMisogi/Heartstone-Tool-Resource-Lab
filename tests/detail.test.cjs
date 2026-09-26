// 拖动以逻辑像素计算；完成一次才写设置，取消恢复且不保存。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {clamp, install} = require('../pengpeng/web/detail.js');

test('width bounds preserve small viewports', () => {
  assert.equal(clamp(undefined), 610);
  assert.equal(clamp(300), 360);
  assert.equal(clamp(3000), 2400);
  assert.equal(clamp(900, 320), 320);
});

test('scaled drag, cancellation, keyboard and persistence', () => {
  const listeners = {}, attributes = {}, saved = [], styles = {};
  let capture = null, physicalWidth = 750;
  global.document = {documentElement: {style:{setProperty:(k,v)=>styles[k]=v}, classList:{add(){},remove(){}}}};
  global.window = {addEventListener(){}};
  global.ResizeObserver = class {observe(){}};
  const handle = {setAttribute:(k,v)=>attributes[k]=v,addEventListener:(k,f)=>listeners[k]=f,focus(){},
    setPointerCapture:id=>capture=id,hasPointerCapture:id=>capture===id,releasePointerCapture:()=>capture=null};
  const panel = {getBoundingClientRect:()=>({width:physicalWidth})};
  const control = install(panel,handle,v=>saved.push(v),rect=>({viewportWidth:1000,...(rect?{width:rect.width/1.25}:{})}));
  control.set(600);
  const down = {button:0,pointerId:1,clientX:500,preventDefault(){}};
  listeners.pointerdown(down);
  listeners.pointermove({pointerId:1,clientX:375}); // 125 屏幕像素 = 100 逻辑像素。
  assert.equal(styles['--detail-width'], '700px');
  assert.equal(saved.length,0);
  listeners.pointerup({pointerId:1,clientX:375});
  assert.deepEqual(saved,[700]);
  physicalWidth=875;
  listeners.pointerdown(down);
  listeners.pointermove({pointerId:1,clientX:250});
  listeners.pointercancel();
  assert.equal(styles['--detail-width'],'700px');
  assert.deepEqual(saved,[700]);
  listeners.keydown({key:'ArrowLeft',preventDefault(){}});
  assert.equal(saved.at(-1),720);
  listeners.keydown({key:'Home',preventDefault(){}});
  assert.equal(saved.at(-1),610);
  listeners.pointerdown(down);
  listeners.pointerup({pointerId:1,clientX:375});
  assert.equal(saved.at(-1),800); // 没有最后一帧 move，仍采用 pointerup 的坐标。
});
