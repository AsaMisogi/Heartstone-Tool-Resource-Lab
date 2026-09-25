"use strict";

// getBoundingClientRect 返回呈现后的坐标，而 CSS zoom 内部的固定定位使用
// 缩放前坐标。统一换算弹层位置；Qt 的原生页面 zoom 不需要这层换算。
function logicalViewport(rect = null) {
  const scale = window.NativeHost?.scale || 1;
  return {viewportWidth:innerWidth / scale, viewportHeight:innerHeight / scale,
    ...(rect ? {left:rect.left / scale, right:rect.right / scale, top:rect.top / scale,
      bottom:rect.bottom / scale, width:rect.width / scale, height:rect.height / scale} : {})};
}

// 业务桥与显示链路分离。Windows 原生浏览器不把滚轮送回 Python，也不使用
// QWebChannel 的 Qt 专用 URL；兼容引擎继续使用已有 WebChannel。
function connectHost(ready) {
  if (window.qt?.webChannelTransport) {
    const script = document.createElement('script');
    script.src = 'qrc:///qtwebchannel/qwebchannel.js';
    script.onload = () => new QWebChannel(qt.webChannelTransport, channel => ready(channel.objects.host));
    document.head.append(script);
    return;
  }
  const queue = [], callbacks = new Map();
  let serial = 0, receiver = () => {};
  const send = (method, args, callback) => {
    const id = ++serial;
    if (callback) callbacks.set(id, callback);
    queue.push({id, method, args});
  };
  window.NativeHost = {
    scale: 1,
    setScale(scale) {
      scale = Math.max(.8, Math.min(1.4, Number(scale) || 1));
      this.scale = scale;
      document.documentElement.style.zoom = scale;
      this.resize();
      // Qt WebView 未公开 controller.ZoomFactor。使用浏览器 CSS zoom 时，
      // px 媒体查询不会自动缩放；根据原始条件换算阈值，复用已有响应式设计，
      // 不能逐次乘上当前规则，否则反复调整会累积误差。
      for (const sheet of document.styleSheets) for (const rule of sheet.cssRules) {
        if (rule.type !== CSSRule.MEDIA_RULE) continue;
        if (!mediaRules.has(rule)) mediaRules.set(rule, rule.conditionText);
        rule.media.mediaText = mediaRules.get(rule).replace(
          /((?:min|max)-(?:width|height)\s*:\s*)([\d.]+)px/g,
          (_, prefix, value) => prefix + Number(value) * scale + 'px');
      }
    },
    resize() {
      document.documentElement.style.setProperty('--viewport-width', innerWidth / this.scale + 'px');
      document.documentElement.style.setProperty('--viewport-height', innerHeight / this.scale + 'px');
    },
    // Python 定期交换业务结果，队列消费是原子的，不执行网络请求。
    exchange(batch) {
      for (const item of batch) {
        if (item.kind === 'response') receiver(item.value);
        else if (item.kind === 'callback') {
          const callback = callbacks.get(item.id); callbacks.delete(item.id);
          callback?.(item.value);
        }
      }
      return queue.splice(0);
    }
  };
  const mediaRules = new WeakMap();
  window.addEventListener('resize', () => window.NativeHost.resize());
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (link && /^https?:/.test(link.href)) {
      event.preventDefault(); send('openExternal', [link.href]);
    }
  });
  document.addEventListener('contextmenu', event => event.preventDefault());
  WheelScroll.useNative();
  ready({
    request: value => send('request', [value]),
    chooseDirectory: (purpose, callback) => send('chooseDirectory', [purpose], callback),
    openFolder: path => send('openFolder', [path]),
    openLogs: () => send('openLogs', []),
    setInterfaceScale: scale => window.NativeHost.setScale(scale),
    setSmoothScrolling: enabled => WheelScroll.setReduced(!enabled),
    response: {connect: callback => {receiver = callback;}}
  });
}
