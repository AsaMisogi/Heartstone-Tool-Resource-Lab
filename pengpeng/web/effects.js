/* 实验性二维预览：读取 Unity Initial / Emission / Shape / Size / Color / UV 模块。
 * 使用确定性随机数，便于暂停后重播比较；不模拟游戏脚本触发和自定义 Shader。
 */
"use strict";
class EffectPreview {
  constructor(canvas, systems) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.systems = systems;
    this.images = new Map();
    this.active = false;
    this.sound = new Audio();
    this.sound.volume = 0.65;
    // 按真实发射窗口及生命周期确定观察区间，短爆发不会留下数秒空画面。
    this.duration = Math.min(15, Math.max(0.5, ...systems.map(s => {
      const em = s.EmissionModule || {};
      const rate = em.enabled ? this.curve(em.rateOverTime, 0) : 0;
      const lastBirth = rate > 0 ? (s.looping ? 3 : s.lengthInSec || 1) :
        Math.max(0, ...(em.m_Bursts || []).map(b => b.time || 0));
      return this.curve(s.startDelay, 0) + lastBirth + this.curve(s.InitialModule?.startLifetime, 0);
    })));
    for (const s of systems) {
      if (s._preview?.texture) {
        const img = new Image();
        img.src = s._preview.texture;
        this.images.set(s, img);
      }
    }
    // 等真实纹理就绪再开始短促特效，避免整段动画在图片加载期间流逝。
    this.ready = Promise.all([...this.images.values()].map(img => img.decode().catch(() => {})));
    this.draw(0);
  }
  random(seed) {
    const n = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
    return n - Math.floor(n);
  }
  curve(curve, t, seed = 1) {
    if (!curve) return 0;
    const scalar = curve.scalar ?? 1,
      mode = curve.minMaxState || 0;
    if (mode === 0) return scalar;
    if (mode === 3)
      return (
        (curve.minScalar || 0) +
        (scalar - (curve.minScalar || 0)) * this.random(seed)
      );
    const keys = curve.maxCurve?.m_Curve || [];
    if (!keys.length) return scalar;
    let v = keys[0].value;
    for (let i = 0; i < keys.length - 1; i++) {
      if (t >= keys[i].time) {
        const a = keys[i],
          b = keys[i + 1];
        const dt = b.time - a.time,
          u = Math.min(1, Math.max(0, (t - a.time) / (dt || 1)));
        v =
          (2 * u ** 3 - 3 * u * u + 1) * a.value +
          (u ** 3 - 2 * u * u + u) * dt * (a.outSlope || 0) +
          (-2 * u ** 3 + 3 * u * u) * b.value +
          (u ** 3 - u * u) * dt * (b.inSlope || 0);
      }
    }
    return scalar * v;
  }
  gradient(g, t) {
    if (!g) return { r: 1, g: 1, b: 1, a: 1 };
    const sample = (prefix, count, channel) => {
      const keys = Array.from({ length: count }, (_, i) => ({
        t: (g[prefix + i] || 0) / 65535,
        v: g["key" + i]?.[channel] ?? 1,
      }));
      if (!keys.length) return 1;
      let v = keys[0].v;
      for (let i = 0; i < keys.length - 1; i++) {
        if (t >= keys[i].t) {
          const a = keys[i],
            b = keys[i + 1],
            u = Math.min(1, (t - a.t) / Math.max(0.0001, b.t - a.t));
          v = a.v + (b.v - a.v) * u;
        }
      }
      return v;
    };
    return {
      r: sample("ctime", g.m_NumColorKeys, "r"),
      g: sample("ctime", g.m_NumColorKeys, "g"),
      b: sample("ctime", g.m_NumColorKeys, "b"),
      a: sample("atime", g.m_NumAlphaKeys, "a"),
    };
  }
  play(speed = 1, url, loop = true) {
    this.stop();
    this.active = true;
    this.speed = speed;
    this.start = performance.now();
    if (url) {
      this.sound.src = url;
      this.sound.play().catch(() => {});
    }
    const tick = () => {
      if (!this.active) return;
      const elapsed = ((performance.now() - this.start) / 1000) * this.speed;
      // 循环只作用于画面，关联声音仍只播放一次，避免循环叠音。
      const position = loop ? elapsed % (this.duration + 0.25) : Math.min(elapsed, this.duration);
      this.draw(position);
      this.onFrame?.(Math.min(position, this.duration));
      if (!loop && elapsed >= this.duration) { this.active = false; return; }
      this.frame = requestAnimationFrame(tick);
    };
    tick();
  }
  stop() {
    this.active = false;
    cancelAnimationFrame(this.frame);
    this.sound.pause();
  }
  draw(time) {
    const c = this.ctx,
      w = this.canvas.width,
      h = this.canvas.height;
    c.fillStyle = "#0b0f15";
    c.fillRect(0, 0, w, h);
    c.strokeStyle = "#1b2632";
    c.lineWidth = 1;
    for (let x = 0; x < w; x += 40) {
      c.beginPath();
      c.moveTo(x, 0);
      c.lineTo(x, h);
      c.stroke();
    }
    for (let y = 0; y < h; y += 40) {
      c.beginPath();
      c.moveTo(0, y);
      c.lineTo(w, y);
      c.stroke();
    }
    c.fillStyle = "#637084";
    c.font = "18px Segoe UI";
    c.fillText("2D PARTICLE STUDY · APPROXIMATE", 20, 30);
    c.fillText(time.toFixed(2) + " s", w - 105, 30);
    let rendered = 0;
    this.systems.forEach((s, si) => {
      const init = s.InitialModule || {},
        em = s.EmissionModule || {},
        shape = s.ShapeModule || {},
        duration = Math.max(0.1, s.lengthInSec || 1),
        delay = this.curve(s.startDelay, 0),
        t = time - delay;
      if (t < 0) return;
      const rate = em.enabled ? Math.max(0, this.curve(em.rateOverTime, 0)) : 0;
      const lifetime = Math.max(0.01, this.curve(init.startLifetime, 0));
      const births = [];
      if (rate > 0) {
        const end = s.looping ? t : Math.min(t, duration);
        for (
          let i = Math.max(0, Math.floor((t - lifetime) * rate));
          i <
          Math.min(
            Math.floor(end * rate),
            Math.max(0, Math.floor((t - lifetime) * rate)) + 500,
          );
          i++
        )
          births.push(i / rate);
      }
      for (const burst of (em.enabled ? em.m_Bursts || [] : [])) {
        const bt = burst.time || 0;
        const count = Math.min(
          300,
          Math.round(this.curve(burst.countCurve, 0) || burst.maxCount || 0),
        );
        const cycle = s.looping ? Math.floor(t / duration) : 0;
        for (let cyc = Math.max(0, cycle - 1); cyc <= cycle; cyc++)
          for (let i = 0; i < count; i++)
            births.push(cyc * duration + bt + i * 0.00001);
      }
      for (let i = 0; i < births.length && rendered < 4000; i++) {
        const birth = births[i],
          seed = si * 1049 + Math.floor(birth * 10000) + 1,
          life = Math.max(0.01, this.curve(init.startLifetime, 0, seed)),
          age = t - birth;
        if (age < 0 || age > life) continue;
        rendered++;
        const u = age / life,
          angle = this.random(seed) * Math.PI * 2,
          r = (shape.radius?.value || 0) * this.random(seed + 2),
          speed = this.curve(init.startSpeed, 0, seed + 3),
          gravity = this.curve(init.gravityModifier, 0) * 9.81;
        const x = w / 2 + Math.cos(angle) * (r + speed * age) * 42,
          y =
            h / 2 +
            (Math.sin(angle) * (r + speed * age) + gravity * age * age * 0.5) *
              42;
        let size = Math.max(0.01, this.curve(init.startSize, 0, seed + 4)) * 42;
        if (s.SizeModule?.enabled)
          size *= Math.max(0, this.curve(s.SizeModule.curve, u));
        const color = init.startColor?.maxColor || { r: 1, g: 1, b: 1, a: 1 },
          over = s.ColorModule?.enabled
            ? this.gradient(s.ColorModule.gradient?.maxGradient, u)
            : { r: 1, g: 1, b: 1, a: 1 };
        c.globalAlpha = Math.max(0, Math.min(1, color.a * over.a));
        const image = this.images.get(s);
        c.save();
        c.translate(x, y);
        c.rotate(this.curve(init.startRotation, 0, seed + 5));
        if (image?.complete && image.naturalWidth) {
          const uv = s.UVModule || {},
            cols = uv.enabled ? uv.tilesX || 1 : 1,
            rows = uv.enabled ? uv.tilesY || 1 : 1,
            frame = Math.floor(u * cols * rows) % Math.max(1, cols * rows),
            iw = image.naturalWidth / cols,
            ih = image.naturalHeight / rows;
          c.drawImage(
            image,
            (frame % cols) * iw,
            Math.floor(frame / cols) * ih,
            iw,
            ih,
            -size / 2,
            -size / 2,
            size,
            size,
          );
        } else {
          c.fillStyle = `rgb(${color.r * over.r * 255},${color.g * over.g * 255},${color.b * over.b * 255})`;
          c.beginPath();
          c.arc(0, 0, Math.min(150, size / 2), 0, Math.PI * 2);
          c.fill();
        }
        c.restore();
      }
    });
    c.globalAlpha = 1;
    c.fillStyle = "#7c8ca1";
    c.fillText(`${rendered} particles · 材质与运动为近似`, 20, h - 20);
  }
}
