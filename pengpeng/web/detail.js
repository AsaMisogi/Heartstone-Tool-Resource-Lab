"use strict";

// 详情宽度以界面逻辑像素保存：WebView2 的 CSS zoom 与 Qt 原生缩放使用
// 同一视口换算。缩小窗口只收缩当前显示宽度，不覆盖用户在大窗口的偏好。
const DetailResize = (() => {
  const MIN = 360, MAX = 2400, DEFAULT = 610;
  function clamp(value, viewport = MAX) {
    const preferred = Number.isFinite(value) ? value : DEFAULT;
    return Math.round(Math.min(Math.max(MIN, preferred), MAX, viewport));
  }
  function install(panel, handle, save, viewport) {
    let preferred = DEFAULT, drag = null;
    const apply = () => {
      const size = clamp(preferred, viewport().viewportWidth);
      document.documentElement.style.setProperty('--detail-width', `${preferred}px`);
      handle.setAttribute('aria-valuenow', String(size));
      handle.setAttribute('aria-valuemin', String(Math.min(MIN, viewport().viewportWidth)));
      handle.setAttribute('aria-valuemax', String(Math.min(MAX, viewport().viewportWidth)));
      handle.setAttribute('aria-valuetext', `详情宽度 ${size} 像素`);
    };
    const commit = () => save(Math.max(MIN, preferred));
    const finish = (cancelled) => {
      if (!drag) return;
      const previous = drag;
      drag = null;
      if (cancelled) preferred = previous.preferred;
      document.documentElement.classList.remove('detail-resizing');
      if (handle.hasPointerCapture(previous.id)) handle.releasePointerCapture(previous.id);
      apply();
      if (!cancelled) commit();
    };
    handle.addEventListener('pointerdown', e => {
      if (e.button !== 0 || drag) return;
      e.preventDefault();
      const geometry = viewport(panel.getBoundingClientRect());
      drag = {id:e.pointerId, x:e.clientX, width:geometry.width, preferred,
        scale:panel.getBoundingClientRect().width / geometry.width};
      handle.setPointerCapture(e.pointerId);
      handle.focus();
      document.documentElement.classList.add('detail-resizing');
    });
    const move = e => {
      if (!drag || e.pointerId !== drag.id || !Number.isFinite(e.clientX)) return;
      preferred = Math.max(MIN, clamp(drag.width + (drag.x - e.clientX) / drag.scale, viewport().viewportWidth));
      apply();
    };
    handle.addEventListener('pointermove', move);
    // 浏览器可能合并最后一帧 move；松手坐标才是用户最终选择的边缘位置。
    handle.addEventListener('pointerup', e => { if (drag?.id === e.pointerId) { move(e); finish(false); } });
    handle.addEventListener('pointercancel', () => finish(true));
    handle.addEventListener('lostpointercapture', () => finish(true));
    handle.addEventListener('dblclick', () => { preferred = DEFAULT; apply(); commit(); });
    handle.addEventListener('keydown', e => {
      if (e.key === 'Escape' && drag) { e.preventDefault(); e.stopPropagation(); finish(true); return; }
      if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(e.key)) return;
      e.preventDefault();
      preferred = e.key === 'Home' ? DEFAULT : Math.max(MIN, clamp(
        clamp(preferred, viewport().viewportWidth) + (e.key === 'ArrowLeft' ? 1 : -1) * (e.shiftKey ? 80 : 20), viewport().viewportWidth));
      apply(); commit();
    });
    window.addEventListener('resize', apply);
    // 字号/界面倍率变更也会改变可用宽度；只刷新 ARIA 与 CSS，不保存收缩值。
    const observer = new ResizeObserver(apply);
    observer.observe(panel);
    return {set(value) { preferred = clamp(value); apply(); }, cancel() { finish(true); }};
  }
  return {clamp, install};
})();
if (typeof module !== 'undefined') module.exports = DetailResize;
