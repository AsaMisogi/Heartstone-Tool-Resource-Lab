/* 交互层不依赖 WebChannel：分类可离线测试，播放器只负责已有音频的定位与绘制。 */
"use strict";
const VoiceGroups = (() => {
  const labels = {all: "全部", basic: "常规战斗", emote: "英雄表情", trigger: "卡牌触发",
    interaction: "对局互动", adventure: "冒险剧情", holiday: "节日彩蛋", prompt: "操作提示", scene: "场景播报", other: "其他条件"};
  function classify(item) {
    // 明确的资源关联优先于名称。只检查事件与资源键，绝不据台词内容推断剧情。
    if (item.adventure) return "adventure";
    if (item.trigger_card) return "trigger";
    const key = `${item.event || ""} ${item.name || ""}`;
    if (/场景|全局台词|Announcer|Innkeeper|VO_BACON_BOB/i.test(key)) return "scene";
    if (/Opponent|Against|Mirror|Response|SpecialGreeting|_(?:Start|Greetings?)_[A-Za-z]|对手|面对|互动/i.test(key)) return "interaction";
    if (/HAPPY_|FIRE_FESTIVAL|PIRATE_DAY|NOBLEGARDEN|节日/i.test(key)) return "holiday";
    if (/ERROR_|LowCards|NoCards|Thinking|_Time_|操作提示/i.test(key)) return "prompt";
    if (/_(?:Start|Victory|Attack|Death)_\d/i.test(item.name || "")) return "basic";
    if (/Emote|Greeting|Thanks|WellPlayed|Oops|Threaten|Concede|表情/i.test(key)) return "emote";
    if (/AdditionalPlay|Trigger|SubSpell|专属触发/i.test(key)) return "other";
    if (/Play|Attack|Death|Start|Victory|Summon|登场|攻击|死亡/i.test(key)) return "basic";
    return "other";
  }
  function select(items, kind, group, query) {
    const seen = new Set(), counts = Object.fromEntries(Object.keys(labels).map(k => [k, 0]));
    const rows = [];
    for (const item of items) {
      if ((item.kind || "voice") !== kind) continue;
      const identity = `${item.id}:${item.event}:${item.condition || ''}`;
      if (seen.has(identity)) continue;
      seen.add(identity);
      if (query && ![item.text, item.name, item.event, item.speech_text, item.condition].some(v => String(v || "").toLowerCase().includes(query))) continue;
      const category = classify(item);
      counts.all++; counts[category]++;
      if (kind !== "voice" || group === "all" || category === group) rows.push(item);
    }
    return {rows, counts};
  }
  return {labels, classify, select};
})();

// 跨语言使用资源名和完整触发上下文对齐，不按“登场”等展示标题或列表序号猜测。
// 同一事件可以包含多个随机分支，必须保留名称中的序号；路径 ID 则因语言包而异。
const VoiceLocales = (() => {
  function stable(value) {
    if (Array.isArray(value)) return value.map(stable);
    if (value && typeof value === 'object') return Object.fromEntries(
      Object.keys(value).sort().map(key => [key, stable(value[key])]));
    return value;
  }
  function key(item) {
    return JSON.stringify([String(item.name || '').toLowerCase(), item.kind || 'voice',
      item.event || '', item.trigger_card || '', stable(item.condition_raw || null)]);
  }
  function index(items) {
    const result = new Map();
    for (const item of items) {
      const identity = key(item);
      if (!result.has(identity)) result.set(identity, item);
      // 相同上下文出现不同文件时保留“不确定”，不把某个分支随意当作对应语音。
      else if (result.get(identity)?.id !== item.id) result.set(identity, null);
    }
    return result;
  }
  return {key, index};
})();

class WaveformControl {
  constructor(canvas, audio, output, formatTime) {
    Object.assign(this, {canvas, audio, output, formatTime, peaks: [], frame: 0, drag: null, lastLabel: ""});
    this.base = document.createElement("canvas");
    this.fill = document.createElement("canvas");
    this.motion = matchMedia("(prefers-reduced-motion: reduce)");
    // 仅有一条 rAF 链；暂停、最小化和停止播放后不继续空转。
    this.tick = () => {
      this.frame = 0;
      this.paint();
      if (!audio.paused && !audio.ended && !document.hidden && !this.motion.matches) this.request();
    };
    for (const event of ["play", "pause", "ended", "timeupdate", "durationchange", "seeked", "loadedmetadata"])
      audio.addEventListener(event, () => this.request());
    audio.addEventListener("emptied", () => this.cancelDrag());
    document.addEventListener("visibilitychange", () => { this.cancelDrag(); this.request(); });
    window.addEventListener("blur", () => this.cancelDrag());
    this.motion.addEventListener("change", () => this.request());
    new ResizeObserver(() => this.rebuild()).observe(canvas);
    canvas.addEventListener("pointerdown", e => {
      if (e.button !== 0 || this.drag || !this.duration()) return;
      e.preventDefault(); canvas.focus({preventScroll: true});
      this.drag = {id: e.pointerId, ratio: this.ratio(e)};
      canvas.setPointerCapture(e.pointerId);
      canvas.classList.add("scrubbing"); this.request();
    });
    canvas.addEventListener("pointermove", e => {
      if (this.drag?.id !== e.pointerId) return;
      this.drag.ratio = this.ratio(e); this.request();
    });
    canvas.addEventListener("pointerup", e => {
      if (this.drag?.id !== e.pointerId) return;
      // 拖动时逐帧预览，释放时只提交一次解码定位，配套音轨沿用 seeking 同步。
      const position = this.ratio(e) * this.duration();
      this.cancelDrag();
      if (this.duration()) audio.currentTime = position;
      this.request();
    });
    for (const event of ["pointercancel", "lostpointercapture"]) canvas.addEventListener(event, () => this.cancelDrag());
    canvas.addEventListener("keydown", e => {
      if (e.key === "Escape" && this.drag) { e.stopPropagation(); this.cancelDrag(); return; }
      if (!this.duration() || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      const delta = (e.shiftKey ? 1 : 5) * (["ArrowLeft", "ArrowDown"].includes(e.key) ? -1 : 1);
      audio.currentTime = e.key === "Home" ? 0 : e.key === "End" ? this.duration() : Math.min(this.duration(), Math.max(0, audio.currentTime + delta));
      this.request();
    });
  }
  duration() { return Number.isFinite(this.audio.duration) && this.audio.duration > 0 ? this.audio.duration : 0; }
  ratio(e) {
    const rect = this.canvas.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
  }
  request() { if (!this.frame && !document.hidden) this.frame = requestAnimationFrame(this.tick); }
  cancelDrag() {
    const id = this.drag?.id;
    this.drag = null; this.canvas.classList.remove("scrubbing");
    if (id !== undefined && this.canvas.hasPointerCapture(id)) this.canvas.releasePointerCapture(id);
    this.request();
  }
  reset() { this.cancelDrag(); this.peaks = []; this.rebuild(); }
  setPeaks(peaks) { this.cancelDrag(); this.peaks = peaks || []; this.rebuild(); }
  rebuild() {
    const rect = this.canvas.getBoundingClientRect(), scale = devicePixelRatio || 1;
    if (!rect.width || !rect.height) return;
    const width = Math.round(rect.width * scale), height = Math.round(rect.height * scale);
    this.canvas.width = width; this.canvas.height = height;
    // 波形几何只在换轨/缩放时生成，播放帧只合成两张缓存图和一条定位线。
    for (const [layer, color] of [[this.base, "#63788c"], [this.fill, "#f0c891"]]) {
      layer.width = width; layer.height = height;
      const ctx = layer.getContext("2d"); ctx.fillStyle = color;
      const step = width / Math.max(1, this.peaks.length);
      this.peaks.forEach((value, i) => {
        const h = Math.max(2 * scale, Math.min(1, Math.max(0, Number(value) || 0)) * height * .78);
        ctx.fillRect(i * step, (height - h) / 2, Math.max(scale, step - scale), h);
      });
    }
    this.request();
  }
  paint() {
    const {canvas, audio} = this, duration = this.duration();
    const position = this.drag ? this.drag.ratio * duration : audio.currentTime || 0;
    const ratio = duration ? Math.min(1, position / duration) : 0;
    const ctx = canvas.getContext("2d"), x = ratio * canvas.width;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(this.base, 0, 0);
    if (x > 0) ctx.drawImage(this.fill, 0, 0, x, canvas.height, 0, 0, x, canvas.height);
    if (duration) { ctx.fillStyle = "#ffe0b8"; ctx.fillRect(Math.min(x, canvas.width - 2), 0, 2, canvas.height); }
    const label = `${this.formatTime(position)} / ${this.formatTime(duration)}`;
    // 可访问标签按显示秒数更新，避免每帧制造 DOM 变更与读屏播报。
    if (label !== this.lastLabel) {
      this.lastLabel = label; this.output.textContent = label;
      canvas.setAttribute("aria-valuetext", label);
      canvas.setAttribute("aria-valuenow", String(Math.floor(position)));
    }
    canvas.setAttribute("aria-valuemax", String(duration));
    canvas.setAttribute("aria-disabled", String(!duration));
  }
}
