"use strict";

// 显示帧驱动的有限时长曲线。连续同向输入保留速度与距离；反向丢弃旧余量。
// 每帧直接写真实 scrollTop/Left，文字、图片、滚动条共享同一个位置，不做 transform。
class ScrollMotion {
  constructor() { this.tracks = []; this.duration = 90; }
  add(node, property, delta, max, now, immediate = false) {
    const previous = this.tracks.find(t => t.node === node && t.property === property);
    this.tracks = this.tracks.filter(t => t !== previous);
    const start = node[property];
    const same = previous && Math.sign(previous.target - start) === Math.sign(delta)
      && Math.abs(previous.last - start) <= 1;
    const target = Math.max(0, Math.min(max, (same ? previous.target : start) + delta));
    if (immediate) { node[property] = target; return; }
    const distance = target - start;
    if (!distance) return;
    // Hermite 曲线终点速度为零，起点沿用已呈现速度，避免连滚每次重新起步。
    // 限制初速以保持单调，不能越过目标再回弹。
    const velocity = same ? previous.velocity : 3 * distance / this.duration;
    const slope = Math.sign(distance) * Math.min(Math.abs(velocity), 3 * Math.abs(distance) / this.duration);
    // 新方向预进半帧，输入到达时就能呈现第一小步，不额外等待一次 rAF。
    // 连续同向输入不预进，以免每格多推进一次、破坏速度连续性。
    this.tracks.push({node, property, start, target, last:start, began:now-(same ? 0 : 8), velocity:slope, slope});
  }
  sample(now) {
    this.tracks = this.tracks.filter(t => {
      if (t.node.isConnected === false || Math.abs(t.node[t.property] - t.last) > 1) return false;
      const u = Math.max(0, Math.min(1, (now - t.began) / this.duration));
      const d = t.target - t.start, a = t.slope * this.duration;
      t.node[t.property] = u === 1 ? t.target : t.start + a*u + (3*d-2*a)*u*u + (a-2*d)*u*u*u;
      t.last = t.node[t.property];
      t.velocity = (a + 2*(3*d-2*a)*u + 3*(a-2*d)*u*u) / this.duration;
      return u < 1;
    });
    return this.tracks.length > 0;
  }
  clear() { this.tracks = []; }
}

const WheelScroll = (() => {
  const motion = new ScrollMotion();
  let frame = 0, reduced = false, mode = 'smooth', installed = false, native = false;
  function instantWheel(event) {
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 100 / 3 : 1;
    let dx = event.deltaX * unit, dy = event.deltaY * unit;
    if (event.shiftKey && !dx) {dx = dy; dy = 0;}
    input({x:event.clientX, y:event.clientY, dx, dy,
      pageX:event.deltaMode === 2, pageY:event.deltaMode === 2, precise:true});
  }
  function syncNativeWheel() {
    if (!native) return;
    document.removeEventListener('wheel', instantWheel);
    // 平滑模式根本不安装阻塞 wheel 监听器，输入可直接进入合成线程。
    if (reduced || mode === 'instant') document.addEventListener('wheel', instantWheel, {passive:false});
  }
  function stop() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0; motion.clear();
    document.documentElement.classList.remove('wheel-scrolling');
  }
  function tick(now) {
    frame = 0;
    if (motion.sample(now)) frame = requestAnimationFrame(tick);
    else document.documentElement.classList.remove('wheel-scrolling');
  }
  function install() {
    if (installed) return;
    installed = true;
    // 滚动条拖动、键盘导航与新的页面操作优先，剩余动画不能把位置拉回去。
    document.addEventListener('pointerdown', stop, {capture:true, passive:true});
    document.addEventListener('keydown', stop, {capture:true});
    document.addEventListener('visibilitychange', stop);
    window.addEventListener('blur', stop);
  }
  function input(event) {
    install();
    const hit = document.elementFromPoint(event.x, event.y);
    if (!hit || document.hidden) return;
    const immediate = reduced || mode === 'instant' || event.precise;
    if (immediate) stop();
    const now = performance.now();
    for (const [axis, delta, page] of [['Y',event.dy,event.pageY], ['X',event.dx,event.pageX]]) {
      if (!delta) continue;
      const vertical = axis === 'Y', property = vertical ? 'scrollTop' : 'scrollLeft';
      for (let node = hit; node; node = node.parentElement) {
        const root = node === document.scrollingElement, style = getComputedStyle(node);
        const boundary = ['contain','none'].includes(style['overscrollBehavior' + axis]);
        if (!root && !['auto','scroll'].includes(style['overflow' + axis])) continue;
        const size = vertical ? node.clientHeight : node.clientWidth;
        const max = (vertical ? node.scrollHeight : node.scrollWidth) - size;
        // 整页设置按命中的实际容器高度处理，预留少量上下文便于阅读。
        const amount = page ? delta * Math.max(1, size - 40) : delta;
        if (max > 0 && (amount < 0 && node[property] > 0 || amount > 0 && node[property] < max)) {
          motion.add(node, property, amount, max, now, immediate);
          break;
        }
        // 即使容器内容不足一屏，contain 也不能将事件泄漏给遮罩后的页面。
        if (root || boundary) break;
      }
    }
    motion.sample(now);
    if (motion.tracks.length && !frame) {
      document.documentElement.classList.add('wheel-scrolling');
      frame = requestAnimationFrame(tick);
    }
  }
  return {input, stop, useNative() {
    // 默认不拦截原生浏览器滚轮。合成线程自行处理 Windows 幅度、连续输入、
    // 拖拽和高刷新率显示；只有用户明确选择即时/减少动画时才直接定位。
    if (native) return;
    native = true; syncNativeWheel();
    // 被动观察真实滚动，包括拖拽；只抑制悬停渐变，不驱动位置或刷新时钟。
    document.addEventListener('scroll', () => document.documentElement.classList.add('wheel-scrolling'), {capture:true, passive:true});
    document.addEventListener('scrollend', () => document.documentElement.classList.remove('wheel-scrolling'), {capture:true, passive:true});
  }, setReduced(value) {if (reduced !== value) {reduced = value; stop(); syncNativeWheel();}},
    setMode(value) {const next = value === 'instant' ? 'instant' : 'smooth'; if (mode !== next) {mode = next; stop(); syncNativeWheel();}}};
})();
