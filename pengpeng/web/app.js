/* 砰砰解析台桌面界面。所有数据来自 WebChannel，不连接远程卡牌 API。
 * request generation 防止慢请求覆盖新选择；缩略图仅两项在途，避免阻塞交互队列。
 */
"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (n) => Number(n || 0).toLocaleString("zh-CN");
const time = (n) =>
  `${Math.floor((n || 0) / 60)}:${String(Math.floor((n || 0) % 60)).padStart(2, "0")}`;
const plain = (s) =>
  String(s || "")
    .replace(/<[^>]*>/g, "")
    .replace(/\[x\]/g, "")
    .replace(/\[b\]/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\$(?=\d)/g, "");
let host,
  serial = 0,
  pending = new Map(),
  state = {
    view: "cards",
    query: "",
    locale: "zhcn",
    offset: 0,
    total: 0,
    limit: 24,
    favorites: false,
    category: "",
    filters: {},
    viewStates: {},
    catalogDisplay: {mode: "grid", size: 220},
    cardHistory: [],
    // 两个图鉴独立记忆排序，避免浏览皮肤后改变卡牌顺序。
    catalogOrder: {cards: {sort: "default", descending: false}, heroes: {sort: "default", descending: false}},
    voiceLocale: "zhcn",
    voiceKind: "voice",
    voiceItems: [],
    generation: 0,
    detailGeneration: 0,
    status: null,
    selected: new Set(),
    exports: [],
    audioExports: new Map(),
    infiniteScroll: false,
    logs: [],
    card: null,
    tab: "art",
    variant: 0,
    scanning: false,
  };
let currentAudio = null,
  fx = null;
// 查询条件按页面分开保存在工作区设置中。每次变更立即入队，关闭窗口时无需依赖防抖计时器。
function rememberView() {
  if (!state.preferencesReady || !["cards", "heroes", "audio", "effects"].includes(state.view)) return;
  const snapshot = {query: $("#search").value.trim(), locale: state.locale,
    category: state.category, favorites: state.favorites, filters: {...state.filters},
    display: {...state.catalogDisplay},
    order: {...(state.catalogOrder[state.view] || {sort: "default", descending: false})}};
  if (JSON.stringify(state.viewStates[state.view]) === JSON.stringify(snapshot)) return;
  state.viewStates[state.view] = snapshot;
  api("save_settings", {view_state: state.viewStates}).catch(e => toast("筛选保存失败：" + e.message));
}
function restoreView() {
  const saved = state.viewStates[state.view] || {};
  state.catalogDisplay = {mode: saved.display?.mode === "list" ? "list" : "grid",
    size: Math.min(300, Math.max(180, Number(saved.display?.size) || 220))};
  state.query = saved.query || "";
  state.locale = saved.locale || state.status?.settings.locale || "zhcn";
  state.category = saved.category || "";
  state.filters = {...saved.filters};
  state.favorites = !!saved.favorites;
  if (state.catalogOrder[state.view] && saved.order) state.catalogOrder[state.view] = {...saved.order};
  $("#search").value = state.query;
  $("#locale").value = state.locale;
  $("#category").value = state.category;
  $("#favorites").setAttribute("aria-pressed", String(state.favorites));
  $("#favorites").textContent = state.favorites ? "★ 仅收藏" : "☆ 收藏";
}
function resetView() {
  clearTimeout(state.searchTimer);
  // 重置搜索不改变用户选择的布局；布局有独立的恢复默认入口。
  state.viewStates[state.view] = {display: {...state.catalogDisplay}};
  if (state.catalogOrder[state.view]) state.catalogOrder[state.view] = {sort: "default", descending: false};
  restoreView();
  state.offset = 0;
  state.selected.clear();
  renderView();
  return refresh();
}
function applyDisplay(settings) {
  const ui = Math.min(1.4, Math.max(0.8, Number(settings.ui_scale) || 1));
  const font = Math.min(1.5, Math.max(0.85, Number(settings.font_scale) || 1));
  host.setInterfaceScale(ui);
  document.documentElement.style.fontSize = `${14 * font}px`;
}
function voiceExportOptions() {
  return {mix_voice: !!state.mixVoiceExport, paired_audio: !!state.pairedAudio, general_audio: !!state.generalAudio};
}
// 先转义，再仅恢复无属性的强调标签；客户端文字不能注入脚本、链接或任意样式。
function cardRichText(value) {
  return esc(String(value || "").replace(/\[x\]/g, "").replace(/\[b\]|\\n/g, "\n").replace(/\$(?=\d)/g, ""))
    .replace(/&lt;(\/?)(b|i)&gt;/gi, '<$1$2>').replace(/&lt;br\s*\/?&gt;/gi, '<br>');
}
function cardFacts(c) {
  const m = c.metadata || {};
  if (!Object.keys(m).length) return '';
  const facts = [["法力", m.cost], ["类别", m.type], ["稀有度", m.rarity],
    ["职业", m.classes?.join(" / ")], ["种族", m.races?.join(" / ") || "无"],
    ["攻击 / 生命", m.attack != null && m.health != null ? `${m.attack} / ${m.health}` : undefined],
    ["攻击", m.health == null ? m.attack : undefined], ["生命", m.attack == null ? m.health : undefined], ["耐久", m.durability],
    ["系列", m.sets?.join(" / ")]];
  return `<section class="detail-facts"><h3>基础信息</h3><dl class="card-facts">${facts.filter(([,v]) => v !== null && v !== undefined && v !== '').map(([k,v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
    <details class="related-cards" ${c.related?.length ? 'open' : ''}><summary>相关卡牌 · ${c.related?.length || 0}</summary>${c.related?.length ? `<div>${c.related.map(r => `<button class="related-card" data-related-card="${esc(r.id)}"><b>${esc(r.name)}</b><small>${esc(r.id)} ↗</small></button>`).join('')}</div>` : '<p class="note">客户端关系表未提供此卡的相关卡牌。</p>'}</details></section>`;
}
// 卡片只消费随列表返回的摘要；所有数据先转义，颜色只作辅助，文字仍可辨认。
function catalogTile(c) {
  const m = c.summary || {};
  const rarity = {免费: 'free', 普通: 'common', 稀有: 'rare', 史诗: 'epic', 传说: 'legendary'}[m.rarity] || 'unknown';
  const classes = (m.classes || []).map(v => v === '通用英雄 / 中立' ? (c.hero ? '通用 / 中立' : '中立') : v).join(' / ') || '职业未标注';
  const sets = m.sets?.join(' / ') || '系列未标注';
  // 0 是有效属性；法术不虚构攻击/生命，武器优先展示耐久。
  const stats = c.hero ? '' : [['费用', m.cost, 'mana'], ['攻击', m.attack, 'attack'],
    [m.durability != null ? '耐久' : '生命', m.durability ?? m.health, 'health']]
    .filter(([, value]) => value != null)
    .map(([label, value, style]) => `<span class="tile-stat ${style}"><span>${label}</span><strong>${esc(value)}</strong></span>`).join('');
  const kind = c.hero ? (m.battlegrounds ? '酒馆战棋 · 英雄 / 皮肤' : '英雄 / 皮肤') : (m.type || '类型未标注');
  const races = m.races?.join(' / ');
  return `<button class="card-tile rarity-${rarity}" data-card="${esc(c.id)}" title="${esc(c.name)} · ${esc(c.id)}">
    <div class="card-image"><span class="placeholder">◈</span></div>
    <div class="tile-info"><b class="tile-name">${esc(c.name)}</b>
      <div class="tile-kind ${m.battlegrounds ? 'is-battlegrounds' : ''}">${esc(kind)}${!c.hero ? `<span class="tile-rarity">${esc(m.rarity || '未标注')}</span>` : ''}</div>
      <div class="tile-traits"><span class="tile-class">${esc(classes)}</span>${!c.hero ? `<span class="tile-races">${esc(races || '无种族')}</span>` : ''}</div>
      ${stats ? `<div class="tile-stats">${stats}</div>` : ''}
      <div class="tile-set" title="${esc(sets)}">${esc(sets)}</div>
      <small class="tile-id">${esc(c.id)}</small>
      ${state.catalogOrder[state.view].sort === 'release' ? `<small class="release-date" title="${esc(c.release_source)}">${esc(c.release_date || '日期未知')}${c.release_source?.startsWith('系列') ? ' · 系列参考' : ''}</small>` : ''}
    </div></button>`;
}
const audio = $("#audio");
audio.volume = 0.75;

function api(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++serial;
    pending.set(id, { resolve, reject });
    host.request(JSON.stringify({ id, method, params }));
  });
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").hidden = true), 7000);
}
function guarded(fn) {
  return (...args) =>
    Promise.resolve()
      .then(() => fn(...args))
      .catch((e) => {
        toast(e.message);
        // 失败后移除失效的等待状态，避免界面一直显示“正在解析”。
        document.querySelectorAll(".empty .spinner").forEach((spinner) => {
          spinner.parentElement.textContent =
            "读取失败：" + e.message + "。详情请查看实时日志。";
        });
      });
}
window.addEventListener("unhandledrejection", (e) => {
  toast(e.reason?.message || String(e.reason));
  e.preventDefault();
});

function receive(encoded) {
  const data = JSON.parse(encoded);
  if (data.id) {
    const p = pending.get(data.id);
    if (p) {
      pending.delete(data.id);
      data.error ? p.reject(new Error(data.error)) : p.resolve(data.result);
    }
    return;
  }
  if (data.event === "speech_progress") {
    if (state.speechRun?.generation === state.detailGeneration && pending.has(data.request_id)) {
      state.speechNote = data.message;
      renderVoiceList();
    }
  } else if (data.event === "log") {
    state.logs.push(data.message);
    if (state.logs.length > 450) state.logs.shift();
    if (!$("#log-panel").hidden) renderLogs();
  } else if (data.event === "progress") {
    $("#jobbar").hidden = false;
    $("#job-message").textContent = data.message;
    $("#job-count").textContent = `${data.done} / ${data.total}`;
    $("#job-progress").max = data.total;
    $("#job-progress").value = data.done;
  } else if (data.event === "scan_finished") {
    state.scanning = false;
    $("#jobbar").hidden = true;
    $("#scan-button").disabled = false;
    updateStatus(data.status);
    toast(
      data.error ||
        (data.cancelled
          ? "索引已暂停，下次扫描将复用已完成部分。"
          : "索引完成。可以搜索、试听和导出了。"),
    );
    if (["audio", "effects"].includes(state.view)) refresh();
  } else if (data.event === "detail_progress") {
    if (state.card?.id === data.cardid && state.tab === "voices") {
      const loading = document.querySelector("#detail-body .empty");
      if (loading)
        loading.textContent = `正在解析关联声音 ${data.done} / ${data.total} · ${data.message}`;
    }
  } else if (data.event === "worker_dead") {
    $("#jobbar").hidden = true;
    toast(data.message);
    $("#connection").textContent = "解析进程已停止";
    for (const p of pending.values()) p.reject(new Error(data.message));
    pending.clear();
  }
}

function updateStatus(s) {
  state.status = s;
  if (!s?.ready) return;
  state.pairedAudio = !!s.settings.paired_audio;
  state.generalAudio = !!s.settings.general_audio;
  state.mixVoiceExport = !!s.settings.mix_voice_export;
  state.infiniteScroll = !!s.settings.infinite_scroll;
  state.limit = Number(s.settings.page_size) || 24;
  $("#connection").textContent = "本地资源已连接";
  $("#connection-dot").style.background = "var(--green)";
  $("#sidebar-version").textContent = s.game_version
    ? `炉石 ${s.game_version}`
    : `快照 ${s.version.slice(0, 10)}`;
  const values = [
    s.cards,
    s.heroes,
    s.assets.AudioClip || 0,
    `${fmt(s.indexed)} / ${fmt(s.bundles)}`,
  ];
  document
    .querySelectorAll("#stats strong")
    .forEach(
      (el, i) =>
        (el.textContent =
          typeof values[i] === "number" ? fmt(values[i]) : values[i]),
    );
  if ($("#locale").options.length < 2)
    $("#locale").innerHTML = s.locales
      .map((l) => `<option value="${esc(l.code)}">${esc(l.name)}</option>`)
      .join("");
  $("#locale").value = state.locale;
  SelectUI.refresh();
  languageWarning();
}
async function initialize(path) {
  $("#connection").textContent = "正在读取资源…";
  $("#results").innerHTML =
    '<div class="empty"><span class="spinner"></span><strong>正在打开本地档案馆</strong>首次连接将读取卡牌文本，通常需要几秒钟。</div>';
  try {
    const s = await api("initialize", path ? { game_path: path } : {});
    state.locale = s.settings.locale || "zhcn";
    state.selected.clear();
    state.offset = 0;
    updateStatus(s);
    restoreView();
    renderView();
    $("#jobbar").hidden = true;
    renderFilters();
    await refresh();
    return true;
  } catch (e) {
    $("#jobbar").hidden = true;
    toast(e.message);
    state.view = "settings";
    renderView();
    return false;
  }
}

const views = {
  cards: [
    "卡牌图鉴",
    "THE CARD ARCHIVE",
    "每一张卡牌，都有故事。",
    "从本地游戏文件，探索原画、角色与声音。",
  ],
  heroes: [
    "英雄图鉴",
    "VOICES BEHIND THE HEROES",
    "熟悉的英雄，不同的灵魂。",
    "独立皮肤、独特台词与登场声音，尽在本地档案。",
  ],
  audio: [
    "声音资料库",
    "THE SOUND LIBRARY",
    "听见炉石世界的每个细节。",
    "语音、战斗、音乐与环境音，按命名规则分类，支持多选导出。",
  ],
  effects: [
    "特效实验室",
    "THE EFFECT LAB",
    "探寻法术背后的光与声音。",
    "浏览本地预制体，检查真实引用和实验性二维粒子预览。",
  ],
  exports: [
    "导出记录",
    "YOUR CREATIVE MATERIALS",
    "让灵感，成为创作素材。",
    "每次导出独立保存，并附带资源来源及错误报告。",
  ],
  settings: [
    "资源与设置",
    "YOUR LOCAL WORKSPACE",
    "一切，从你的本地文件开始。",
    "只读访问游戏目录；缓存、日志与导出保存在独立工作区。",
  ],
};
function renderView() {
  const v = views[state.view];
  $("#crumb").textContent = v[0];
  $("#eyebrow").textContent = v[1];
  $("#page-title").textContent = v[2];
  $("#page-subtitle").textContent = v[3];
  document
    .querySelectorAll(".nav")
    .forEach((b) =>
      b.classList.toggle("active", b.dataset.view === state.view),
    );
  $("#library").hidden = ["settings", "exports"].includes(state.view);
  $("#settings-page").hidden = state.view !== "settings";
  $("#exports-page").hidden = state.view !== "exports";
  // 设置与导出页优先展示操作内容，不让资源统计占用小窗口的第一屏。
  $("#stats").hidden = ["settings", "exports"].includes(state.view);
  $("#scan-button").hidden = ["settings", "exports"].includes(state.view);
  $("#category").hidden = state.view !== "audio";
  $("#export-selected").hidden = state.view !== "audio";
  $("#favorites").hidden = state.view === "effects";
  $("#search").placeholder = ["cards", "heroes"].includes(state.view)
    ? (state.view === "heroes" ? "搜索英雄、皮肤名称、文本或 ID…" : "搜索卡牌名称、文本或 ID…")
    : "搜索资源名称、资源包或 GUID…";
  $("#view-hint").textContent =
    state.view === "audio"
      ? "中文及通用音效 · WAV 导出"
      : state.view === "effects"
        ? "资源预制体 · 实验性预览"
        : "原始纹理 · 中文优先";
  renderFilters();
  renderOrder();
  renderCatalogDisplay();
  SelectUI.refresh();
  if (state.view === "settings") renderSettings();
  if (state.view === "exports") renderExports();
}
async function changeView(view) {
  clearTimeout(state.searchTimer);
  rememberView();
  state.composing = false;
  state.view = view;
  state.offset = 0;
  state.selected.clear();
  restoreView();
  state.generation++;
  closeDetail();
  renderView();
  if (!["settings", "exports"].includes(view)) await refresh();
}

async function refresh(append = false, preservePosition = false) {
  if (!state.status?.ready) return;
  rememberView();
  const generation = append ? state.generation : ++state.generation;
  const cards = ["cards", "heroes"].includes(state.view);
  if (!cards && !["audio", "effects"].includes(state.view)) return;
  // 保留旧列表及其高度，异步查询不会把浏览器滚动位置夹回页顶。
  const results = $("#results");
  const oldHeight = results.getBoundingClientRect().height;
  results.style.minHeight = preservePosition || append ? `${oldHeight}px` : "";
  results.setAttribute("aria-busy", "true");
  $("#result-count").textContent = "正在更新结果…";
  state.loading = generation;
  renderPagination();
  let data;
  try {
    data = await fetchPage(cards);
  } catch (error) {
    if (generation !== state.generation) return;
    if (generation === state.generation) {
      state.loading = 0;
      results.removeAttribute("aria-busy");
      renderPagination();
    }
    throw error;
  }
  if (generation !== state.generation) return;
  state.loading = 0;
  results.removeAttribute("aria-busy");
  if (!append) results.innerHTML = "";
  state.total = data.total;
  renderPagination();
  $("#result-count").textContent = `${fmt(data.total)} 项资源${state.query ? " · 搜索结果" : ""}${data.dated_total != null ? ` · ${fmt(data.dated_total)} 项有日期参考` : ""}`;
  $("#results").className = cards ? "card-grid" : "asset-list";
  applyCatalogDisplay();
  if (!data.items.length) {
    if (append) return;
    $("#results").innerHTML =
      `<div class="empty"><strong>${cards ? (state.view === "heroes" ? "没有匹配的英雄或皮肤" : "没有匹配的卡牌") : "尚无匹配资源"}</strong>${cards ? "试试其他关键词、重置筛选或取消收藏筛选。英雄图鉴请在左侧对应入口查找。" : "首次使用请点击右上角「建立完整资源索引」。扫描可暂停，已发现的资源立即可用。"}</div>`;
    return;
  }
  if (cards) {
    $("#results").insertAdjacentHTML("beforeend", data.items
      .map(catalogTile)
      .join(""));
    document
      .querySelectorAll("[data-card]")
      .forEach((b) => (b.onclick = guarded(() => showCard(b.dataset.card))));
    // 全列表共用两条缩略图队列，追加页面不取消前一批，也不增加并发数。
    if (!append) thumbnailQueue = [];
    thumbnailQueue.push(...data.items.map(card => ({card, generation})));
    drainThumbnails();
  } else {
    if (state.view === "audio") {
      $("#category").innerHTML =
        '<option value="">所有分类</option>' +
        data.categories
          .map((c) => `<option value="${esc(c)}">${esc(c)}</option>`)
          .join("");
      $("#category").value = state.category;
    }
    $("#results").insertAdjacentHTML("beforeend", data.items
      .map(
        (a) =>
          `<div class="list-row">${state.view === "audio" ? `<input type="checkbox" data-select="${esc(a.id)}" aria-label="选择 ${esc(a.name)}" ${state.selected.has(a.id) ? "checked" : ""}>` : "<span></span>"}<button class="sound-icon" data-asset="${esc(a.id)}" aria-label="${state.view === "audio" ? "试听" : "预览"}">${state.view === "audio" ? "▷" : "✧"}</button><div><b>${esc(a.name)}</b><small>${esc(a.bundle)}</small></div><span class="type">${esc(a.category)}<small>${esc(a.locale)}</small></span><span class="type">${a.duration ? time(a.duration) : "—"}</span><button class="icon-button" data-fav="${esc(a.id)}" aria-label="收藏">${a.favorite ? "★" : "☆"}</button></div>`,
      )
      .join(""));
    document
      .querySelectorAll("[data-asset]")
      .forEach(
        (b) =>
          (b.onclick = guarded(() =>
            state.view === "audio"
              ? playAsset(b.dataset.asset)
              : showEffect({ assetid: b.dataset.asset }),
          )),
      );
    document.querySelectorAll("[data-select]").forEach(
      (b) =>
        (b.onchange = () => {
          b.checked
            ? state.selected.add(b.dataset.select)
            : state.selected.delete(b.dataset.select);
          $("#export-selected").textContent =
            `导出选中 WAV (${state.selected.size})`;
        }),
    );
    document.querySelectorAll("[data-fav]").forEach(
      (b) =>
        (b.onclick = guarded(async () => {
          const enabled = b.textContent === "☆";
          await api("favorite", {
            kind: "asset",
            identity: b.dataset.fav,
            enabled,
          });
          b.textContent = enabled ? "★" : "☆";
          toast(enabled ? "已收藏" : "已取消收藏");
        })),
    );
  }
}

// 只缓存已成功解析的 URL，排序和翻页复用图片，失败项保留重试机会。
// 有界缓存避免连续浏览数万张卡牌后无限增长；快照和语言都参与键值。
let thumbnailQueue = [], thumbnailWorkers = 0;
const thumbnailCache = new Map();
function drainThumbnails() {
  while (thumbnailWorkers < 2 && thumbnailQueue.length) {
    thumbnailWorkers++;
    (async () => {
      try {
        while (thumbnailQueue.length) {
          const {card: c, generation} = thumbnailQueue.shift();
          if (generation !== state.generation) continue;
          try {
            const locale = state.locale;
            const key = `${state.status.version}:${locale}:${c.id}`;
            let image = thumbnailCache.get(key);
            if (!image) image = await api("thumbnail", {cardid: c.id, locale});
            if (image.url) {
              thumbnailCache.delete(key);
              thumbnailCache.set(key, image);
              if (thumbnailCache.size > 256) thumbnailCache.delete(thumbnailCache.keys().next().value);
            }
            if (generation !== state.generation) continue;
            const node = [...document.querySelectorAll("[data-card]")].find(n => n.dataset.card === c.id)?.querySelector(".card-image");
            if (node && image.url) node.innerHTML = `<img src="${esc(image.url)}" alt="${esc(c.name)}" loading="lazy">`;
          } catch (_) { /* 缺图保留占位，打开详情时显示具体原因。 */ }
        }
      } finally { thumbnailWorkers--; }
    })();
  }
}

function fetchPage(cards) {
  return api(
    cards ? "list_cards" : "list_assets",
    cards
      ? {
          query: state.query,
          hero: state.view === "heroes",
          filters: state.filters,
          ...state.catalogOrder[state.view],
          offset: state.offset,
          limit: state.limit,
          favorites: state.favorites,
          locale: state.locale,
        }
      : {
          query: state.query,
          kind: state.view === "audio" ? "AudioClip" : "GameObject",
          locale: state.locale,
          category: state.category,
          offset: state.offset,
          limit: state.limit,
          favorites: state.favorites,
        },
  );
}

function closeDetail() {
  state.cardHistory = [];
  cancelSpeech();
  api("cancel_effect").catch(() => {});
  state.detailGeneration++;
  $("#detail").hidden = true;
  $("#detail-backdrop").hidden = true;
  document.querySelector("main").inert = false;
  document.querySelector(".sidebar").inert = false;
  if (state.returnFocus?.isConnected) state.returnFocus.focus({preventScroll: true});
  state.card = null;
  state.voiceItems = [];
  if (fx) {
    fx.stop();
    fx = null;
  }
}
async function showCard(cardid, navigation = "new") {
  if (navigation === "related" && state.card) state.cardHistory.push({id: state.card.id, tab: state.tab, variant: state.variant, scroll: $("#detail").scrollTop, voiceLocale: state.voiceLocale, voiceKind: state.voiceKind});
  if (navigation === "new") state.cardHistory = [];
  cancelSpeech();
  const generation = ++state.detailGeneration;
  state.variant = 0;
  state.voiceLocale = "zhcn";
  state.voiceKind = "voice";
  state.voiceItems = [];
  state.tab = "art";
  openDetail();
  $("#detail-kicker").textContent = "CARD INSPECTOR";
  $("#detail-content").innerHTML =
    '<div class="empty"><span class="spinner"></span> 正在解析卡牌…</div>';
  const card = await api("card", { cardid, locale: state.locale });
  if (generation !== state.detailGeneration) return;
  state.card = card;
  renderCard();
  await cardTab("art");
  $("#detail").scrollTop = 0;
}
function renderCard() {
  const c = state.card;
  $("#detail-content").innerHTML =
    `<div class="card-id">${esc(c.id)} · ${c.hero ? "英雄 / 皮肤" : "卡牌"}</div><h2>${esc(c.name)}</h2><div class="detail-actions"><button id="favorite-card" class="subtle">${c.favorite ? "★ 已收藏" : "☆ 收藏卡牌"}</button><button id="export-card" class="subtle">↓ 导出图像、文本与全部语音</button></div><div class="detail-tabs"><button data-tab="art" class="active">原画</button><button data-tab="render">完整卡面</button><button data-tab="voices">语音</button><button data-tab="effects">特效</button><button data-tab="raw">源数据</button></div><div id="detail-body"></div>`;
  $("#detail-content").insertAdjacentHTML("afterbegin", state.cardHistory.length ? '<button id="back-card" class="subtle">← 返回上一张卡牌</button>' : '');
  if ($("#back-card")) $("#back-card").onclick = guarded(async () => {
    const previous = state.cardHistory.pop();
    await showCard(previous.id, "back");
    state.variant = previous.variant;
    state.voiceLocale = previous.voiceLocale; state.voiceKind = previous.voiceKind;
    if (previous.tab !== "art" || previous.variant) await cardTab(previous.tab);
    $("#detail").scrollTop = previous.scroll;
  });
  // 基础信息独立放在动态标签内容之后，切换标签不会移动或重复创建。
  $("#detail-content").insertAdjacentHTML("beforeend", cardFacts(c));
  document.querySelectorAll('[data-related-card]').forEach(b => b.onclick = guarded(() => showCard(b.dataset.relatedCard, "related")));
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => (b.onclick = guarded(() => cardTab(b.dataset.tab))));
  $("#favorite-card").onclick = guarded(async () => {
    c.favorite = !c.favorite;
    await api("favorite", {
      kind: "card",
      identity: c.id,
      enabled: c.favorite,
    });
    $("#favorite-card").textContent = c.favorite ? "★ 已收藏" : "☆ 收藏卡牌";
  });
  $("#export-card").onclick = guarded(() =>
    exportFiles({ cardid: c.id, locale: state.voiceLocale, ...voiceExportOptions() }),
  );
}
async function cardTab(tab) {
  if (!state.card) return;
  cancelSpeech();
  state.tab = tab;
  const c = state.card;
  const generation = ++state.detailGeneration;
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  const body = $("#detail-body");
  body.innerHTML =
    '<div class="empty"><span class="spinner"></span> 正在解析真实资源…</div>';
  if (fx) {
    fx.stop();
    fx = null;
  }
  if (tab === "art") {
    body.innerHTML = `<div class="variant-tabs">${c.variants.map((v, i) => `<button data-variant="${i}" ${v.available ? "" : "disabled"} class="${state.variant === i ? "active" : ""}">${esc(v.label)}</button>`).join("")}</div><div class="card-text">${cardRichText(c.text || c.description || "本地数据库未提供描述文本。")}</div>${c.flavor ? `<div class="flavor">${esc(plain(c.flavor))}</div>` : ""}${c.artist ? `<div class="note card-artist">画师 · ${esc(c.artist)}</div>` : ""}<div id="portrait"><div class="empty"><span class="spinner"></span> 正在解码纹理…</div></div>`;
    document.querySelectorAll("[data-variant]").forEach(
      (b) =>
        (b.onclick = guarded(() => {
          state.variant = Number(b.dataset.variant);
          return cardTab("art");
        })),
    );
    const data = await api("portrait", {
      cardid: c.id,
      variant: state.variant,
      locale: state.locale,
    });
    if (generation !== state.detailGeneration) return;
    $("#portrait").innerHTML = data.images.length
      ? `<div class="portrait-stage"><img id="large-portrait" src="${esc(data.images[0].url)}" alt="卡牌本地纹理"></div><div class="note" id="image-info">${esc(data.images[0].name)} · ${data.images[0].width} × ${data.images[0].height} px<br>${esc(data.note)} 点击图片查看大图。</div>${data.images.length > 1 ? `<div class="layers">${data.images.map((im, i) => `<button class="layer" data-layer="${i}" title="${esc(im.slot)}"><img src="${esc(im.url)}" alt="${esc(im.slot)}"><small>${esc(im.slot)}</small></button>`).join("")}</div>` : ""}<button id="export-image" class="subtle">↓ 导出原始尺寸 PNG</button>`
      : `<div class="empty">${esc(data.note)}</div>`;
    let selected = 0;
    document.querySelectorAll("[data-layer]").forEach(
      (b) =>
        (b.onclick = () => {
          selected = Number(b.dataset.layer);
          $("#large-portrait").src = data.images[selected].url;
          const im = data.images[selected];
          $("#image-info").textContent = `${im.name} · ${im.width} × ${im.height} px · 原始尺寸`;
        }),
    );
    if ($("#large-portrait")) $("#large-portrait").onclick = () => viewImage(data.images[selected], c.name);
    if ($("#export-image"))
      $("#export-image").onclick = guarded(() =>
        exportFiles({ image_path: data.images[selected].path, context_cardid: c.id, label: `${c.variants[state.variant].label}_${data.images[selected].name}` }),
      );
    if (data.errors.length)
      $("#portrait").insertAdjacentHTML("beforeend", errorBox(data.errors));
  } else if (tab === "render") {
    body.innerHTML = '<div class="note">完整卡面来自 HearthstoneJSON，首次查看需要联网；成功后可离线查看。图源是在线最新普通卡面，可能与本地补丁不同。</div><div id="render-stage" class="empty"><span class="spinner"></span> 正在读取完整卡面…</div>';
    try {
      const image = await api("card_render", {cardid: c.id, locale: state.locale});
      if (generation !== state.detailGeneration) return;
      $("#render-stage").className = "render-stage";
      $("#render-stage").innerHTML = `<img id="full-card" src="${esc(image.url)}" alt="${esc(c.name)}完整卡面"><p class="note">${image.width} × ${image.height} px · ${esc(image.source)} · 点击放大</p><button id="export-render" class="subtle">↓ 导出完整卡面 PNG</button>`;
      $("#full-card").onclick = () => viewImage(image, c.name + " · 完整卡面");
      $("#export-render").onclick = guarded(() => exportFiles({image_path: image.path, context_cardid: c.id, label: "完整卡面"}));
    } catch (e) {
      if (generation !== state.detailGeneration) return;
      $("#render-stage").innerHTML = `<p>${esc(e.message)}</p><button id="retry-render" class="subtle">重新加载</button>`;
      $("#retry-render").onclick = guarded(() => cardTab("render"));
    }
  } else if (tab === "voices") {
    state.voiceItems = [];
    state.voiceErrors = [];
    state.transcriptNote = "";
    state.transcriptPending = null;
    state.transcriptSources = [];
    // 语音语言独立于图鉴文本，首次打开卡牌默认简体中文。
    body.innerHTML = `<div class="voice-toolbar"><label>资源语言<select id="voice-locale" aria-label="语音语言">${state.status.locales.map(l => `<option value="${l.code}" ${l.code === state.voiceLocale ? "selected" : ""}>${esc(l.name)}${l.audioInstalled ? "" : " · 未安装"}</option>`).join("")}</select></label><label class="pair-label"><input id="paired-audio" type="checkbox" ${state.pairedAudio ? "checked" : ""}>配套音效 / 音乐</label><label class="pair-label" title="随从登场时叠加落地、嘲讽和圣盾，以及卡牌实际引用的种族 / 材质垫音；下次播放生效"><input id="general-audio" type="checkbox" ${state.generalAudio ? "checked" : ""}>通用音效</label></div><div id="voice-warning" class="warning" hidden></div><div class="detail-tabs voice-tabs"><button data-voice-kind="voice">角色语音</button><button data-voice-kind="sound">音效与音乐</button></div><div id="voice-list" class="empty"><span class="spinner"></span>正在解析声音引用…</div>`;
    SelectUI.refresh();
    $("#voice-locale").onchange = guarded(async () => {
      state.voiceLocale = $("#voice-locale").value;
      stopPlayback();
      await cardTab("voices");
    });
    $(".voice-toolbar").insertAdjacentHTML("beforeend", `<label class="pair-label mix-export"><input id="mix-voice-export" type="checkbox" ${state.mixVoiceExport ? "checked" : ""}>导出时合并已勾选音效</label><p class="note">合并为一个 WAV，保留配音尾声（最长 15 秒）；未勾选时导出原始声音。</p><button id="export-voices" class="subtle" disabled>↓ 导出全部角色语音</button>`);
    $("#export-voices").onclick = guarded(() => exportFiles({assetids: [...new Set(state.voiceItems.filter(x => x.kind === "voice").map(x => x.id))], context_cardid: c.id, locale: state.voiceLocale, ...voiceExportOptions()}));
    for (const [selector, key, setting] of [["#mix-voice-export", "mixVoiceExport", "mix_voice_export"], ["#paired-audio", "pairedAudio", "paired_audio"], ["#general-audio", "generalAudio", "general_audio"]]) {
      $(selector).onchange = guarded(async () => {
        const checkbox = $(selector), enabled = checkbox.checked;
        checkbox.disabled = true;
        // 使在途的配音准备失效，避免取消勾选后旧请求仍启动声音。
        ++playGeneration;
        stopCompanions();
        try {
          await api("save_settings", {[setting]: enabled});
          state[key] = enabled;
          state.status.settings[setting] = enabled;
          renderVoiceList();
        } finally {
          // 保存失败时恢复真实状态，不能显示已记忆但实际未保存的勾选。
          checkbox.checked = !!state[key];
          checkbox.disabled = false;
        }
      });
    }
    document.querySelectorAll("[data-voice-kind]").forEach(b => b.onclick = () => {
      state.voiceKind = b.dataset.voiceKind;
      renderVoiceList();
    });
    const installed = state.status.locales.find(l => l.code === state.voiceLocale)?.audioInstalled;
    $("#voice-warning").hidden = !!installed;
    $("#voice-warning").textContent = "当前客户端未安装此语言的语音资源。可在战网客户端安装相应语言后重新连接；通用音效不代表该语言已安装。";
    const data = await api("card_audio", { cardid: c.id, locale: state.voiceLocale });
    if (generation !== state.detailGeneration) return;
    state.voiceItems = data.items;
    $("#export-voices").disabled = !data.items.some(x => x.kind === "voice");
    state.voiceErrors = data.errors;
    renderVoiceList();
    supplementTranscripts(c, generation);
  } else if (tab === "effects") {
    const effects = c.effects.filter((e) => !e.speech);
    body.innerHTML = `<div class="warning">实验性预览会显示真实粒子参数与关联声音，不能完整还原游戏专用脚本和动态材质。</div>${effects.map((e, i) => `<div class="voice-row"><div class="voice-top"><b>${esc(e.name)}</b><button data-effect="${i}" aria-label="预览特效">▷</button></div><small>${esc(eventName(e.field))}</small></div>`).join("") || '<div class="empty">此卡没有独立特效引用。</div>'}`;
    body
      .querySelectorAll("[data-effect]")
      .forEach(
        (b) =>
          (b.onclick = guarded(() =>
            showEffect({ reference: effects[Number(b.dataset.effect)].ref }),
          )),
      );
  } else
    body.innerHTML = `<p class="note">本地 CardDef 原始数据。字段与路径均来自当前资源包。</p><pre class="raw">${esc(JSON.stringify(c.definition, null, 2))}</pre>`;
}
function eventName(name) {
  const map = {
    LowCards: "牌库将空", NoCards: "牌库耗尽", NeedMana: "法力不足",
    Well_Played: "称赞", Think: "思考", Timer: "回合将结束", Picked: "选择英雄",
    Greetings: "问候", Thanks: "感谢", WellPlayed: "称赞", Wow: "惊叹",
    Oops: "失误", Threaten: "威胁", Concede: "认输", Sorry: "抱歉",
    Start: "开场", Victory: "胜利",
    Play: "登场",
    Attack: "攻击",
    Death: "死亡",
    Lifetime: "持续",
    Trigger: "触发",
    Emote: "英雄表情",
    AdditionalPlay: "额外登场",
    SubSpell: "子法术",
    ResetGame: "重置",
    Announcer: "播报",
  };
  for (const [key, value] of Object.entries(map)) {
    if (name.includes(key)) return value;
  }
  return "其他 / 条件触发";
}
// 台词里的 <死亡>、<笑声> 是演出标记，并非 HTML 标签；不能用卡牌文本的
// 通用去标签规则删掉。先去除已知排版标签，再整体转义后插入页面。
function speechText(value) {
  return String(value || "").replace(/<\/?(?:b|i|strong|em|color|size)(?:[ =][^>]*)?>/gi, "")
    .replace(/\\n/g, "\n").replace(/\[x\]/g, "");
}
function voiceRows(items) {
  return (
    items
      .map(
        (a) =>
          `<div class="voice-row"><div class="voice-top"><b>${esc(eventName(a.name + " " + (a.event || "")))}</b><div><button data-play="${esc(a.id)}" aria-label="试听">▷</button><button data-export="${esc(a.id)}" aria-label="导出 WAV">↓</button></div></div>${a.text ? `<p class="transcript">${esc(speechText(a.text))}</p>${a.source ? `<a class="transcript-source" href="${esc(a.source)}">${esc(a.source_name)} · ${a.match_method === "audio_key" ? "按音频键精确匹配" : "按唯一事件匹配"}${a.stale ? " · 离线缓存" : ""} ↗</a>` : '<small>客户端字幕</small>'}` : a.kind === "voice" ? `${a.speech_text ? `<p class="transcript">${esc(a.speech_text)}</p><small class="speech-label">语音识别 · 不保证准确性${a.speech_cached ? " · 缓存" : ""}</small>` : `<p class="transcript-missing">${esc(a.speech_error || (a.speech_done ? "未识别出文字，可能是笑声、喘息或背景音。" : "本地字幕表未找到此音频的准确台词。"))}</p>`}${state.status.settings.speech_recognition !== false && ["zhcn", "enus"].includes(state.voiceLocale) ? `<button class="subtle speech-retry" data-speech="${esc(a.id)}" ${state.speechRun || state.transcriptPending === state.detailGeneration ? "disabled" : ""}>${a.speech_done || a.speech_error ? "重试语音识别" : "语音识别"}</button>` : ""}` : ""}<small>${esc(a.name)}<br>${esc(a.event || "")} · ${esc(a.locale)}</small></div>`,
      )
      .join("") || '<div class="empty">此引用下未发现可读取的音频。</div>'
  );
}
function bindVoices(node) {
  node.querySelectorAll("[data-speech]").forEach(button => {
    button.onclick = guarded(() => recognizeMissing(state.detailGeneration, button.dataset.speech));
  });
  node
    .querySelectorAll("[data-play]")
    .forEach((b) => (b.onclick = guarded(() => playAsset(b.dataset.play))));
  node
    .querySelectorAll("[data-export]")
    .forEach(
      (b) =>
        (b.onclick = guarded(() =>
          exportFiles({ assetids: [b.dataset.export], context_cardid: state.card?.id || "", locale: state.voiceLocale, ...voiceExportOptions() }),
        )),
    );
}
function errorBox(errors) {
  return errors?.length
    ? `<details class="warning"><summary>${errors.length} 项资源未完整解析，查看原因</summary>${errors.map((e) => `<div>${esc(e)}</div>`).join("")}</details>`
    : "";
}

async function playAsset(assetid) {
  if (fx) fx.stop();
  const generation = ++playGeneration;
  stopCompanions();
  audio.pause();
  toast("正在准备音频…");
  const contextCardid = state.card?.id || "";
  const voiceLocale = contextCardid ? state.voiceLocale : state.locale;
  const selected = state.voiceItems.find(x => x.id === assetid && x.kind === "voice");
  const voiceItems = state.voiceItems;
  const data = await api("audio", { assetid, locale: voiceLocale });
  if (generation !== playGeneration) return;
  const companions = [];
  // 两类配音共用一个 ID 集合，避免 Underlay 同时被两个选项重复播放。
  const ids = new Set();
  if (state.pairedAudio && selected?.group) {
    voiceItems.filter(x => x.group === selected.group && x.kind === "sound").forEach(x => ids.add(x.id));
  }
  if (state.generalAudio && selected && contextCardid) {
    try {
      const shared = await api("general_audio", {cardid: contextCardid, assetid, locale: voiceLocale});
      if (generation !== playGeneration) return;
      shared.items.forEach(x => ids.add(x.id));
      if (shared.errors.length) toast("部分通用音效不可用：" + shared.errors.join("；"));
    } catch (e) { if (generation !== playGeneration) return; toast("通用音效不可用：" + e.message); }
  }
  for (const id of ids) {
    try {
      const related = await api("audio", {assetid: id});
      if (generation !== playGeneration) return;
      companions.push(...related.samples);
    } catch (e) { if (generation !== playGeneration) return; toast("配套声音不可用：" + e.message); }
  }
  if (generation !== playGeneration) return;
  companionTracks = companions.map(sample => { const track = new Audio(sample.url); track.volume = audio.volume;
    // 以媒体时间计算剩余额度；精确定时器负责截停，timeupdate 处理拖动兜底。
    const cap = () => { track.capped = true; track.pause(); syncPlaybackButton(); };
    const schedule = () => {
      clearTimeout(track.capTimer);
      if (track.currentTime >= 15) cap();
      else if (!track.paused) track.capTimer = setTimeout(cap, (15 - track.currentTime) * 1000 / track.playbackRate);
      syncPlaybackButton();
    };
    track.onplaying = schedule;
    track.onseeked = schedule;
    track.onpause = () => { clearTimeout(track.capTimer); syncPlaybackButton(); };
    track.onended = () => { clearTimeout(track.capTimer); syncPlaybackButton(); };
    track.ontimeupdate = () => { if (track.currentTime >= 15) cap(); };
    return track; });
  currentAudio = { assetid, samples: data.samples, index: 0, context_cardid: contextCardid, locale: voiceLocale };
  playSample(data.samples[0]);
  // 主音轨的 play 事件统一启动配套音轨，避免重复调用 play。
  if (data.samples.length > 1)
    toast(
      `此资源含 ${data.samples.length} 个子采样，将依次播放；导出会保留全部。`,
    );
}
function playSample(sample) {
  $("#player").hidden = false;
  $("#track-name").textContent = sample.name;
  $("#track-info").textContent =
    `PCM WAV · ${sample.rate} Hz · ${sample.channels} 声道`;
  audio.src = sample.url;
  audio.play().catch(e => { if (e.name !== "AbortError") toast("无法播放：" + e.message); });
  drawWave(sample.peaks, 0);
}
audio.onplay = () => ($("#play-pause").textContent = "Ⅱ");
audio.onpause = () => ($("#play-pause").textContent = "▶");
audio.ontimeupdate = () => {
  $("#track-time").textContent =
    `${time(audio.currentTime)} / ${time(audio.duration)}`;
  if (currentAudio)
    drawWave(
      currentAudio.samples[currentAudio.index].peaks,
      audio.currentTime / (audio.duration || 1),
    );
};
audio.onended = () => {
  if (currentAudio && currentAudio.index + 1 < currentAudio.samples.length)
    playSample(currentAudio.samples[++currentAudio.index]);
};
audio.onerror = () =>
  toast("播放器无法读取该文件，请查看日志。WAV 仍可导出供其他播放器打开。");
function drawWave(peaks, progress) {
  const c = $("#waveform"),
    ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const w = c.width / Math.max(1, peaks.length);
  peaks.forEach((v, i) => {
    ctx.fillStyle = i / peaks.length < progress ? "#e9ad78" : "#687181";
    const h = Math.max(2, v * 36);
    ctx.fillRect(i * w, (42 - h) / 2, Math.max(1, w - 1), h);
  });
}

async function showEffect(params) {
  cancelSpeech();
  const generation = ++state.detailGeneration;
  stopPlayback();
  if (fx) fx.stop();
  openDetail();
  $("#detail-kicker").textContent = "EFFECT LAB / EXPERIMENTAL";
  $("#detail-content").innerHTML =
    '<div class="empty"><span class="spinner"></span><strong>正在解析特效引用图</strong>读取粒子参数、纹理和关联声音…</div>';
  let data;
  try { data = await api("effect", { ...params, locale: state.locale }); }
  catch (error) {
    if (generation !== state.detailGeneration) return;
    $("#detail-content").innerHTML = `<div class="warning">${esc(error.message)}</div><button id="retry-effect" class="subtle">重试解析</button>`;
    $("#retry-effect").onclick = guarded(() => showEffect(params));
    return;
  }
  if (generation !== state.detailGeneration) return;
  $("#detail-content").innerHTML =
    `<h2 style="font-size:1.2857rem;overflow-wrap:anywhere">${esc(data.name)}</h2><div class="warning">${esc(data.note)}</div><div class="note">${data.particles.length} 个粒子系统 · ${data.sounds.length} 个关联音频</div><canvas id="fx-canvas" class="fx-canvas" width="800" height="620"></canvas><div class="fx-controls"><button id="fx-play" class="primary">▶ 播放 / 重播</button><button id="fx-stop" class="subtle">停止</button><label>速度 <input id="fx-speed" type="range" min="0.2" max="2" value="1" step="0.1"></label></div><div class="fx-timeline"><label><input id="fx-loop" type="checkbox" checked>循环画面</label><label>时间 <input id="fx-time" type="range" min="0" max="15" value="0" step="0.01"></label><output id="fx-time-label">0.00 s</output></div><div class="note">声音轨道（手动播放时启动一次；画面循环不重复声音）</div><select id="fx-sound" class="subtle"><option value="-1">静音</option>${data.sounds.map((s, i) => `<option value="${i}">${esc(s.name)}</option>`).join("")}</select>${!data.particles.length ? '<p class="warning">此预制体没有可预览的粒子系统。网格、动画及脚本特效暂不支持渲染。</p>' : ""}<details class="note"><summary>组件统计</summary><pre class="raw">${esc(JSON.stringify(data.components, null, 2))}</pre></details>${errorBox(data.errors)}`;
  fx = new EffectPreview($("#fx-canvas"), data.particles);
  $("#fx-time").max = fx.duration;
  fx.onFrame = position => {
    $("#fx-time").value = position;
    $("#fx-time-label").textContent = position.toFixed(2) + " s";
  };
  $("#fx-time").oninput = () => {
    fx.stop();
    const position = Number($("#fx-time").value);
    fx.draw(position); fx.onFrame(position);
  };
  if (data.sounds.length) {
    $("#fx-sound").insertAdjacentHTML(
      "afterend",
      '<button id="export-fx-audio" class="subtle">↓ 导出关联 WAV</button>',
    );
    $("#export-fx-audio").onclick = guarded(() =>
      exportFiles({ media_paths: data.sounds.map((s) => s.path) }),
    );
  }
  if (data.sounds.length) $("#fx-sound").value = "0";
  $("#fx-play").onclick = () => {
    audio.pause();
    fx.play(
      Number($("#fx-speed").value),
      data.sounds[Number($("#fx-sound").value)]?.url,
      $("#fx-loop").checked,
    );
  };
  $("#fx-stop").onclick = () => fx.stop();
  $("#fx-loop").onchange = () => fx.play(Number($("#fx-speed").value), undefined, $("#fx-loop").checked);
  await fx.ready;
  if (generation === state.detailGeneration && data.particles.length) fx.play();
}

async function exportFiles(params) {
  if (state.exporting) {
    toast("当前导出还在进行，请等待完成。");
    return;
  }
  state.exporting = true;
  toast("正在导出，请稍候；批量卡牌资源可能需要较长时间。");
  let result;
  try {
    result = await api("export", {locale: state.locale, ...params});
  } finally {
    state.exporting = false;
  }
  state.exports.unshift({ ...result, when: new Date().toLocaleString() });
  $("#open-last-export").hidden = false;
  $("#open-last-export").onclick = () => host.openFolder(result.folder);
  // 每个音频保留自己的目录；切换页签、再次导出其他音频也不会覆盖它。
  if (params.assetids?.length === 1 && result.files.length) {
    state.audioExports.set(params.assetids[0], result.folder);
    attachAudioExports(document);
  }
  // 非音频导出继续使用原有的区域入口。
  const anchor = $("#detail").hidden ? $("#export-selected") : $("#detail-content .detail-actions");
  if (anchor && !params.assetids?.length) {
    let button = anchor.parentElement.querySelector(".open-export-inline");
    if (!button) { button = document.createElement("button"); button.className = "subtle open-export-inline"; anchor.after(button); }
    button.textContent = "打开导出文件夹 ↗";
    button.onclick = () => host.openFolder(result.folder);
  }
  toast(
    `已导出 ${result.files.length} 个媒体文件${result.errors.length ? `，${result.errors.length} 项失败，详见导出报告` : ""}`,
  );
  if (state.view === "exports") renderExports();
}
function renderExports() {
  $("#export-history").className = "";
  $("#export-history").innerHTML = state.exports.length
    ? state.exports
        .map(
          (r, i) =>
            `<div class="export-result"><b>${r.files.length} 个媒体文件 · ${r.errors.length ? "部分失败" : "完成"}</b><p>${esc(r.when)}<br>${esc(r.folder)}</p><button class="subtle" data-folder="${i}">打开导出目录 ↗</button>${errorBox(r.errors)}</div>`,
        )
        .join("")
    : '<div class="empty">本次启动还没有导出记录。以前的导出保存在设置中的导出目录。</div>';
  document
    .querySelectorAll("[data-folder]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          host.openFolder(state.exports[Number(b.dataset.folder)].folder)),
    );
}
function renderSettings() {
  const s = state.status?.settings || {
    game_path: "",
    export_path: "",
  };
  $("#settings-page").innerHTML =
    `<div class="setting"><label>炉石安装目录</label><div class="path-row"><input id="game-path" value="${esc(s.game_path)}" aria-label="炉石目录"><button id="browse-game" class="subtle">浏览…</button><button id="connect-game" class="primary">连接</button></div><p>读取 Data/Win 与 Strings；无需运行游戏，不修改安装文件。</p></div><div class="setting"><label>导出位置</label><div class="path-row"><input id="export-path" value="${esc(s.export_path)}" aria-label="导出目录"><button id="browse-export" class="subtle">浏览…</button><button id="save-export" class="subtle">保存</button></div><p>每次导出新建目录，避免覆盖已有作品。缓存与日志保存在工具的 workspace 文件夹。</p></div><div class="setting"><label>资源索引与诊断</label><p>索引覆盖本地资源包。未安装语言包的声音无法读取；卡牌文本可切换本地 DBF 内的语言。扫描支持断点继续，游戏更新后按文件指纹使用新快照。</p><button id="diagnostics" class="subtle">查看索引诊断</button><div id="diagnostics-result"></div></div><div class="setting"><label>关于砰砰解析台 BOOM LAB</label><p>版本 0.2.0 · 作者 朝禊ASOGI<br>参考 Hermes 的资源清单解析思路，独立实现可视化工作台。游戏美术与声音属于相应权利人，发布代码不包含游戏素材。</p><a href="https://space.bilibili.com/315312" class="subtle">B站 · 朝禊ASOGI ↗</a><p>原画与声音读取本地资源；完整卡面按需从 HearthstoneJSON 获取并缓存。特效为实验性二维预览，完整 Unity 运行时渲染尚未实现。</p></div>`;
  $("#browse-game").onclick = () =>
    host.chooseDirectory("game", (p) => {
      if (p) $("#game-path").value = p;
    });
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting display-settings"><h2>显示与阅读</h2><label>界面缩放 <input id="ui-scale" type="range" min="80" max="140" step="5" value="${Math.round((s.ui_scale || 1) * 100)}"><output id="ui-scale-value"></output></label><label>字体缩放 <input id="font-scale" type="range" min="85" max="150" step="5" value="${Math.round((s.font_scale || 1) * 100)}"><output id="font-scale-value"></output></label><button id="reset-display" class="subtle">恢复 100%</button><p>界面缩放改变控件与布局；字体缩放独立调整文字大小。使用本机可用字体，退出后保留设置。</p></div>`);
  for (const [id, key] of [["ui-scale", "ui_scale"], ["font-scale", "font_scale"]]) {
    const input = $("#" + id), output = $("#" + id + "-value");
    const preview = () => {output.textContent = input.value + "%";};
    preview();
    input.oninput = preview;
    input.onchange = guarded(async () => {
      const settings = await api("save_settings", {[key]: Number(input.value) / 100});
      state.status.settings = settings;
      applyDisplay(settings);
    });
  }
  $("#reset-display").onclick = guarded(async () => {
    state.status.settings = await api("save_settings", {ui_scale: 1, font_scale: 1});
    applyDisplay(state.status.settings); renderSettings();
  });
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting"><label><input id="infinite-scroll" type="checkbox" ${state.infiniteScroll ? "checked" : ""}> 滚动加载更多</label><p>默认关闭。开启后，滚动至列表底部自动追加内容，隐藏页码跳转；每次加载数量由列表下方设置。</p></div>`);
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting"><label><input id="online-transcripts" type="checkbox" ${s.online_transcripts !== false ? "checked" : ""}> 按需补充公开台词</label><p>本地字幕优先；缺失时按下方顺序查询已启用来源，失败或未命中则继续补齐，缓存结果并显示来源。支持按音频键精确匹配简中字幕；Wiki 仅补充可唯一匹配的基础事件。关闭后不发起查询。</p></div>`);
  // 独立保存来源列表，开关、顺序与新增操作共享后端校验和持久化。
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting"><label><input id="speech-recognition" type="checkbox" ${s.speech_recognition !== false ? "checked" : ""}> 缺失台词自动语音识别</label><p>现有台词来源未命中时，离线识别简中 / 英语短语音。首次使用每种语言下载约 40–42 MB 小模型；不上传音频、不使用显卡。逐条处理，闲置 15 秒释放模型；关闭后立即停止。其他语言暂不识别。</p><p>识别文字会标明“不保证准确性”，可逐条重试。笑声、音效及特殊角色声线可能无法正确识别。</p></div>`);
  $("#speech-recognition").onchange = guarded(async () => {
    const box = $("#speech-recognition"), enabled = box.checked;
    try {
      state.status.settings = await api("save_settings", {speech_recognition: enabled});
      if (!enabled) cancelSpeech();
      toast(enabled ? "语音识别已开启，重新打开语音页后生效" : "语音识别已关闭");
    } catch (error) { box.checked = !enabled; throw error; }
  });
  const sources = s.transcript_sources || [];
  $("#online-transcripts").closest(".setting").insertAdjacentHTML("beforeend", `
    <div id="transcript-provider-list">${sources.map((source, index) => `
      <div class="path-row" style="margin:8px 0;flex-wrap:wrap">
        <label style="flex:1;min-width:200px"><input type="checkbox" data-provider="${index}" ${source.enabled ? "checked" : ""}> ${esc(source.name)}</label>
        <button class="subtle" data-source-up="${index}" ${index === 0 ? "disabled" : ""}>上移</button>
        ${["hsdata","wikigg","huiji","baidu"].includes(source.id) ? "" : `<button class="subtle" data-source-edit="${index}">编辑</button><button class="subtle" data-source-delete="${index}">删除</button>`}
      </div>`).join("")}</div>
    <p>hsdata 按完整音频键匹配客户端字幕；Wiki.gg、百度百科、灰机按唯一基础事件补充。各来源覆盖不完整，灰机可能要求浏览验证。</p>
    <details><summary>添加 / 编辑自定义来源</summary>
      <p>支持固定 JSON 协议或灰机式 MediaWiki 台词接口；普通网页地址不能直接作为通用接口。</p>
      <div class="path-row" style="flex-wrap:wrap;margin:10px 0">
        <input id="source-name" placeholder="来源名称" aria-label="来源名称" maxlength="80">
        <select id="source-kind" aria-label="来源格式"><option value="json">JSON 台词接口</option><option value="mediawiki">MediaWiki 展开 HTML</option></select>
      </div>
      <input id="source-url" style="width:100%;box-sizing:border-box" placeholder="https://example.org/quotes/{dbfid}?locale={locale}" aria-label="来源接口 URL">
      <p>占位符：{dbfid} 原卡编号、{cardid} 原卡 ID、{name} 原卡中文名、{locale} 语言。自动进行 URL 编码。</p>
      <p>JSON 示例：<code>{"dbfid":724,"locale":"zhcn","quotes":{"Play":"登场台词","Attack":"攻击台词"}}</code>。未收录返回空 quotes；不要将多个随机分支合为一条。</p>
      <button id="save-transcript-source" class="primary">保存来源</button>
      <span id="source-save-status" role="status"></span>
    </details>`);
  let editingSource = null;
  const saveSources = async (next) => {
    const settings = await api("save_settings", {transcript_sources: next});
    state.status.settings = settings;
    renderSettings();
    toast("台词来源已保存，重新打开语音页后使用");
  };
  document.querySelectorAll("[data-provider]").forEach(box => box.onchange = guarded(async () => {
    try { await saveSources(sources.map((source, index) => index === Number(box.dataset.provider) ? {...source, enabled:box.checked} : source)); }
    catch (error) { box.checked = !box.checked; throw error; }
  }));
  document.querySelectorAll("[data-source-up]").forEach(button => button.onclick = guarded(async () => {
    const index = Number(button.dataset.sourceUp), next = sources.slice();
    [next[index-1], next[index]] = [next[index], next[index-1]];
    await saveSources(next);
  }));
  document.querySelectorAll("[data-source-delete]").forEach(button => button.onclick = guarded(() =>
    saveSources(sources.filter((_, index) => index !== Number(button.dataset.sourceDelete)))));
  document.querySelectorAll("[data-source-edit]").forEach(button => button.onclick = () => {
    const source = sources[Number(button.dataset.sourceEdit)];
    editingSource = source.id;
    $("#source-name").value = source.name; $("#source-kind").value = source.kind; $("#source-url").value = source.url;
    $("#source-name").closest("details").open = true;
    $("#source-name").focus();
  });
  $("#save-transcript-source").onclick = guarded(async () => {
    const source = {id:editingSource || `custom_${Date.now()}`, name:$("#source-name").value.trim(),
      kind:$("#source-kind").value, url:$("#source-url").value.trim(),
      enabled:editingSource ? sources.find(s => s.id === editingSource).enabled : true};
    const button = $("#save-transcript-source"); button.disabled = true;
    try { await saveSources(editingSource ? sources.map(s => s.id === editingSource ? source : s) : [...sources, source]); }
    catch(error) { $("#source-save-status").textContent = error.message || String(error); }
    finally { button.disabled = false; }
  });
  $("#online-transcripts").onchange = guarded(async () => {
    const enabled = $("#online-transcripts").checked;
    await api("save_settings", {online_transcripts: enabled});
    state.status.settings.online_transcripts = enabled;
  });
  $("#infinite-scroll").onchange = guarded(async () => {
    const enabled = $("#infinite-scroll").checked;
    await api("save_settings", {infinite_scroll: enabled});
    state.infiniteScroll = enabled;
    state.status.settings.infinite_scroll = enabled;
    state.offset = 0;
  });
  $("#browse-export").onclick = () =>
    host.chooseDirectory("export", (p) => {
      if (p) $("#export-path").value = p;
    });
  $("#connect-game").onclick = guarded(async () => {
    await initialize($("#game-path").value);
    if (state.status?.ready) await changeView("cards");
  });
  $("#save-export").onclick = guarded(async () => {
    const settings = await api("save_settings", {
      export_path: $("#export-path").value,
    });
    state.status = { ...(state.status || {}), settings };
    toast("导出位置已保存");
  });
  $("#diagnostics").onclick = guarded(async () => {
    const d = await api("diagnostics");
    $("#diagnostics-result").innerHTML =
      `<p>已索引 ${d.status.indexed} / ${d.status.bundles} 个资源包；失败 ${d.bundle_errors.length} 项。</p>${errorBox(d.bundle_errors.map((e) => e.name + ": " + e.error))}`;
  });
  $("#settings-page").prepend($(".display-settings"));
}
function renderLogs() {
  const el = $("#log-output");
  el.textContent = state.logs.join("\n");
  el.scrollTop = el.scrollHeight;
}

document
  .querySelectorAll(".nav")
  .forEach((b) => (b.onclick = guarded(() => changeView(b.dataset.view))));
// 输入法组词期间不发送中间拼音；输入即使旧请求失效，避免旧结果闪回。
$("#search").addEventListener("compositionstart", () => {
  state.composing = true;
  clearTimeout(state.searchTimer);
  state.generation++;
});
$("#search").addEventListener("compositionend", () => {
  state.composing = false;
  scheduleSearch();
});
function scheduleSearch() {
  clearTimeout(state.searchTimer);
  state.generation++;
  if (state.composing) return;
  rememberView();
  state.searchTimer = setTimeout(
    guarded(() => {
      state.query = $("#search").value.trim();
      state.offset = 0;
      return refresh();
    }),
    280,
  );
}
$("#search").oninput = scheduleSearch;
$("#reset-search").onclick = guarded(resetView);

function renderOrder() {
  const order = state.catalogOrder[state.view];
  $("#catalog-order").hidden = !order;
  if (!order) return;
  $("#sort-by").value = order.sort;
  const button = $("#sort-direction");
  button.textContent = order.descending ? "↓ 倒序" : "↑ 正序";
  button.setAttribute("aria-pressed", String(order.descending));
  button.setAttribute("aria-label", `${order.descending ? "当前倒序，切换为正序" : "当前正序，切换为倒序"}`);
  $("#sort-hint").textContent = {
    default: "保持原有浏览顺序",
    name: "按当前显示名称的文字顺序排列",
    id: "按卡牌资源 ID 排列",
    release: "系列上线日期仅供参考；独立皮肤日期缺失时置后",
  }[order.sort];
  SelectUI.refresh();
}
async function changeOrder() {
  state.offset = 0;
  // 合并尚未触发的搜索，切换排序时立即使用输入框的最新内容。
  clearTimeout(state.searchTimer);
  state.query = $("#search").value.trim();
  renderOrder();
  try { localStorage.setItem("pengpeng.catalogOrder", JSON.stringify(state.catalogOrder)); }
  catch (_) { /* 禁用本地存储时，本次会话的排序仍正常工作。 */ }
  await refresh();
}
$("#sort-by").onchange = guarded(() => {
  state.catalogOrder[state.view].sort = $("#sort-by").value;
  return changeOrder();
});
$("#sort-direction").onclick = guarded(() => {
  const order = state.catalogOrder[state.view];
  order.descending = !order.descending;
  return changeOrder();
});
try {
  const saved = JSON.parse(localStorage.getItem("pengpeng.catalogOrder") || "{}");
  for (const view of ["cards", "heroes"]) {
    const order = saved?.[view];
    if (["default", "name", "id", "release"].includes(order?.sort) && typeof order.descending === "boolean")
      state.catalogOrder[view] = {sort: order.sort, descending: order.descending};
  }
} catch (_) { /* 旧值损坏时只回退排序，不影响收藏和其他工作区设置。 */ }
renderOrder();
$("#locale").onchange = guarded(async () => {
  state.locale = $("#locale").value;
  state.offset = 0;
  state.selected.clear();
  languageWarning();
  await api("save_settings", { locale: state.locale });
  const language = state.status.locales.find((l) => l.code === state.locale);
  if (!language?.audioInstalled)
    toast("本地未安装此语言语音。文本仍可读取；不会用其他语言冒充；通用音效仍可使用。");
  await refresh();
  if (state.card) await showCard(state.card.id);
});
$("#category").onchange = guarded(() => {
  state.category = $("#category").value;
  state.offset = 0;
  return refresh();
});
$("#favorites").onclick = guarded(() => {
  state.favorites = !state.favorites;
  $("#favorites").setAttribute("aria-pressed", String(state.favorites));
  $("#favorites").textContent = state.favorites ? "★ 仅收藏" : "☆ 收藏";
  state.offset = 0;
  return refresh();
});
$("#scan-button").onclick = guarded(async () => {
  await api("scan");
  state.scanning = true;
  $("#scan-button").disabled = true;
  $("#jobbar").hidden = false;
});
$("#cancel-scan").onclick = guarded(() => api("cancel_scan"));
$("#close-detail").onclick = closeDetail;
$("#logs-toggle").onclick = () => {
  $("#log-panel").hidden = !$("#log-panel").hidden;
  renderLogs();
};
$("#close-logs").onclick = () => ($("#log-panel").hidden = true);
$("#open-logs").onclick = () => host.openLogs();
$("#play-pause").onclick = () => {
  const tails = companionTracks.filter(t => !t.ended && !t.capped);
  if (audio.ended && tails.length) {
    const playing = tails.some(t => !t.paused);
    tails.forEach(t => playing ? t.pause() : t.play().catch(e => toast(e.message)));
  } else if (audio.ended && currentAudio) guarded(() => playAsset(currentAudio.assetid))();
  else if (audio.paused) audio.play().catch(e => toast(e.message));
  else audio.pause();
};
function syncPlaybackButton() {
  $("#play-pause").textContent = !audio.paused || companionTracks.some(t => !t.paused && !t.ended && !t.capped) ? "Ⅱ" : "▶";
}
$("#volume").oninput = () => { audio.volume = Number($("#volume").value); companionTracks.forEach(t => t.volume = audio.volume); };
$("#waveform").onclick = (e) => {
  if (audio.duration)
    audio.currentTime =
      (e.offsetX / $("#waveform").clientWidth) * audio.duration;
};
$("#stop-player").onclick = stopPlayback;
function stopPlayback() {
  playGeneration++;
  stopCompanions();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  currentAudio = null;
  $("#player").hidden = true;
};
$("#export-track").onclick = guarded(
  () => currentAudio && exportFiles({ assetids: [currentAudio.assetid], context_cardid: currentAudio.context_cardid, locale: currentAudio.locale, ...voiceExportOptions() }),
);
$("#export-selected").onclick = guarded(() => {
  if (!state.selected.size) {
    toast("请先勾选要导出的声音");
    return;
  }
  return exportFiles({ assetids: [...state.selected] });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if ($("#image-viewer").open || $("#welcome").open) return;
    closeDetail();
    $("#log-panel").hidden = true;
  }
  if (
    e.key === "/" &&
    !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)
  ) {
    e.preventDefault();
    $("#search").focus();
  }
});
// 页面辅助交互集中在此处，解析逻辑仍在后端；不引入前端构建链。
let companionTracks = [], playGeneration = 0;
function stopCompanions() {
  companionTracks.forEach(track => { clearTimeout(track.capTimer); track.pause(); track.removeAttribute("src"); track.load(); });
  companionTracks = [];
}
audio.addEventListener("pause", () => { if (!audio.ended) companionTracks.forEach(t => t.pause()); });
audio.addEventListener("play", () => companionTracks.forEach(t => { if (!t.ended && !t.capped) t.play().catch(e => { if (e.name !== "AbortError" && companionTracks.includes(t)) toast("配套声音播放失败：" + e.message); }); }));
audio.addEventListener("seeking", () => companionTracks.forEach(t => { if (Number.isFinite(t.duration)) t.currentTime = Math.min(audio.currentTime, t.duration, 15); }));
audio.addEventListener("ended", syncPlaybackButton);
// 主语音自然结束后，配套声音继续播放至自身结束或 15 秒上限。
function openDetail() {
  if ($("#detail").hidden) state.returnFocus = document.activeElement;
  $("#detail").hidden = false;
  $("#detail-backdrop").hidden = false;
  document.querySelector("main").inert = true;
  document.querySelector(".sidebar").inert = true;
  $("#close-detail").focus();
}
$("#detail-backdrop").onclick = closeDetail;
$("#detail").addEventListener("keydown", e => {
  if (e.key !== "Tab") return;
  const nodes = [...$("#detail").querySelectorAll('button:not(:disabled),input,select,summary,a[href],[tabindex="0"]')].filter(n => n.getClientRects().length);
  const first = nodes[0], last = nodes.at(-1);
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
});
function viewImage(image, title) {
  $("#viewer-image").src = image.url;
  $("#viewer-title").textContent = `${title} · ${image.width} × ${image.height} px`;
  $("#viewer-image").classList.remove("actual-size");
  $("#viewer-zoom").textContent = "原始尺寸";
  $("#image-viewer").showModal();
}
$("#viewer-close").onclick = () => $("#image-viewer").close();
$("#viewer-zoom").onclick = () => {
  const actual = $("#viewer-image").classList.toggle("actual-size");
  $("#viewer-zoom").textContent = actual ? "适应窗口" : "原始尺寸";
};
$("#image-viewer").onclick = e => { if (e.target === $("#image-viewer")) $("#image-viewer").close(); };
function languageWarning() {
  const missing = state.status?.locales?.find(l => l.code === state.locale)?.audioInstalled === false;
  $("#language-warning").hidden = !missing;
  $("#language-warning").textContent = "此客户端未安装所选语言的声音资源，卡牌文本仍可查看。请安装对应语言包后重新连接；通用音效不属于特定语言。";
}
function renderVoiceList() {
  if (!$("#voice-list")) return;
  const items = state.voiceItems.filter(x => (x.kind || "voice") === state.voiceKind);
  document.querySelectorAll("[data-voice-kind]").forEach(b => {
    b.classList.toggle("active", b.dataset.voiceKind === state.voiceKind);
    const count = new Set(state.voiceItems.filter(x => (x.kind || "voice") === b.dataset.voiceKind).map(x => x.id)).size;
    b.textContent = (b.dataset.voiceKind === "voice" ? "角色语音" : "音效与音乐") + ` (${count})`;
  });
  $("#voice-list").className = "";
  const unique = [...new Map(items.map(x => [x.id + ":" + x.event, x])).values()];
  // 台词优先露出，避免首屏被无字幕的攻击喘息、死亡和播报占满。
  if (state.voiceKind === "voice") unique.sort((a, b) => Number(!!b.text) - Number(!!a.text));
  const transcribed = unique.filter(x => x.text).length;
  const recognized = unique.filter(x => !x.text && x.speech_text).length;
  const coverage = state.voiceKind === "voice" ? `<p class="transcript-summary">${transcribed} / ${unique.length} 条语音已有来源台词${recognized ? ` · ${recognized} 条语音识别（仅供参考）` : ""} · 来源台词优先显示</p>` : "";
  $("#voice-list").innerHTML = coverage + `<p class="note">${esc(state.transcriptNote || "")}</p>` + `<p class="note">${state.generalAudio ? "通用音效已开启：随从登场附加落地与初始关键词声音，并播放该事件引用的种族 / 材质垫音；时序为试听近似。 " : ""}${state.pairedAudio ? "配套播放已开启：仅混合同一事件引用中的音效 / 音乐，起始时间为试听近似。" : "未开启卡牌配套音效 / 音乐。"}</p>` + voiceRows(unique) + errorBox(state.voiceErrors);
  bindVoices($("#voice-list"));
  if (state.voiceKind === "voice") {
    const note = document.createElement("p");
    note.className = "note";
    note.setAttribute("role", "status");
    note.textContent = state.speechNote || "";
    $("#voice-list").prepend(note);
  }
  // 重试只补缺失台词；请求期间禁用，切卡/切语言后旧结果由 generation 丢弃。
  if (state.voiceKind === "voice" && state.voiceLocale === "zhcn" &&
      state.status.settings.online_transcripts !== false && state.card?.record.dbfid &&
      state.voiceItems.some(item => item.kind === "voice" && !item.text)) {
    const retry = document.createElement("button");
    retry.id = "retry-transcripts";
    retry.className = "subtle";
    retry.disabled = state.transcriptPending === state.detailGeneration;
    retry.textContent = retry.disabled ? "正在查询台词…" : "重试获取缺失台词";
    retry.onclick = guarded(() => supplementTranscripts(state.card, state.detailGeneration, true));
    $("#voice-list").prepend(retry);
  }

  if (state.voiceKind === "voice" && state.transcriptSources?.length) {
    const controls = document.createElement("div");
    controls.className = "transcript-actions";
    controls.innerHTML = state.transcriptSources.map(id => `<button class="subtle" data-transcript-source="${Number(id)}">在浏览窗口读取台词${state.transcriptSources.length > 1 ? ` · ${Number(id)}` : ""}</button>`).join("");
    $("#voice-list").prepend(controls);
    controls.querySelectorAll("button").forEach(button => button.onclick = guarded(async () => {
      const card = state.card, generation = state.detailGeneration;
      const result = await api("transcript_browser", {dbfid: Number(button.dataset.transcriptSource)});
      if (result.saved && generation === state.detailGeneration) await supplementTranscripts(card, generation);
    }));
  }
  attachAudioExports($("#voice-list"));
}
// 只改变布局样式，保留卡片节点、已解码图片和进行中的缩略图队列。
// input 负责即时预览，change 才保存，拖拽过程中不反复写入工作区设置。
function applyCatalogDisplay() {
  const active = ["cards", "heroes"].includes(state.view);
  const {mode, size} = state.catalogDisplay;
  $("#results").classList.toggle("catalog-list", active && mode === "list");
  $("#results").style.setProperty("--tile-width", `${size}px`);
  $("#results").style.setProperty("--tile-space", `${Math.round(7 + (size - 180) / 20)}px`);
}
function renderCatalogDisplay() {
  const node = $("#catalog-display");
  node.hidden = !["cards", "heroes"].includes(state.view);
  if (node.hidden) return;
  const sync = () => {
    const {mode, size} = state.catalogDisplay;
    node.querySelectorAll("[data-layout]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.layout === mode)));
    $("#catalog-size").value = size;
    $("#catalog-size").disabled = mode === "list";
    $("#catalog-size-value").textContent = mode === "list" ? "列表" : `${Math.round(size / 220 * 100)}%`;
    applyCatalogDisplay();
  };
  node.querySelectorAll("[data-layout]").forEach(b => b.onclick = () => {
    state.catalogDisplay.mode = b.dataset.layout; sync(); rememberView();
  });
  $("#catalog-size").oninput = e => { state.catalogDisplay.size = Number(e.target.value); sync(); };
  $("#catalog-size").onchange = rememberView;
  $("#reset-layout").onclick = () => {
    state.catalogDisplay = {mode: "grid", size: 220}; sync(); rememberView();
  };
  sync();
}
function renderFilters() {
  const node = $("#card-filters");
  node.hidden = !["cards", "heroes"].includes(state.view);
  if (node.hidden || !state.status?.filters) return;
  const f = state.status.filters;
  const make = (key, label, values) => `<label>${label}<select data-filter="${key}" aria-label="${label}"><option value="">全部${label}</option>${values.map(x => `<option value="${esc(x.value)}" ${String(state.filters[key]) === String(x.value) ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>`;
  const cost = Array.from({length: 11}, (_, i) => ({value: i === 10 ? "10+" : String(i), label: i === 10 ? "10 费及以上" : `${i} 费`}));
  node.innerHTML = state.view === "heroes"
    ? make("hero_group", "英雄职业", f.hero_groups) + make("battlegrounds", "酒馆皮肤", [{value: "exclude", label: "不显示酒馆皮肤"}, {value: "only", label: "只显示酒馆皮肤"}])
    : make("set", "系列", f.sets) + make("format", "赛制", f.formats) + make("class", "职业", f.classes) + make("rarity", "稀有度", f.rarities) + make("cost", "法力消耗", cost) + make("type", "类别", f.types) + make("collectible", "收集状态", [{value: "1", label: "可收集"}, {value: "0", label: "衍生 / 非收集"}]);
  if (state.view === "heroes") node.querySelector('[data-filter="battlegrounds"] option').textContent = "显示酒馆皮肤";
  node.insertAdjacentHTML("beforeend", '<button id="reset-filters" class="subtle">重置筛选</button>');
  node.querySelectorAll("select").forEach(select => select.onchange = guarded(() => {
    state.filters[select.dataset.filter] = select.value;
    state.offset = 0;
    return refresh();
  }));
  $("#reset-filters").onclick = guarded(resetView);
  SelectUI.refresh();
}
async function startup() {
  const status = await api("status");
  state.status = status;
  state.viewStates = status.settings.view_state || {};
  state.preferencesReady = true;
  applyDisplay(status.settings);
  restoreView();
  if (status.settings.game_path?.trim()) await initialize();
  else { $("#connection").textContent = "请选择炉石目录"; $("#welcome").showModal(); }
  SelectUI.refresh();
}
$("#welcome-browse").onclick = () => host.chooseDirectory("game", p => { if (p) $("#welcome-path").value = p; });
$("#welcome-later").onclick = () => { $("#welcome").close(); changeView("settings"); };
$("#welcome-connect").onclick = guarded(async () => {
  const path = $("#welcome-path").value.trim();
  if (!path) { $("#welcome-error").hidden = false; $("#welcome-error").textContent = "请先选择安装目录。"; return; }
  $("#welcome-connect").disabled = true;
  $("#welcome-connect").textContent = "正在连接…";
  try {
    if (await initialize(path)) { $("#welcome").close(); await changeView("cards"); }
    else { $("#welcome-error").hidden = false; $("#welcome-error").textContent = "目录无法读取，请选择包含 Data/Win 的炉石目录。"; }
  } finally { $("#welcome-connect").disabled = false; $("#welcome-connect").textContent = "打开资源工作台 →"; }
});
$("#welcome").addEventListener("cancel", () => changeView("settings"));
new QWebChannel(qt.webChannelTransport, (channel) => {
  host = channel.objects.host;
  host.response.connect(receive);
  guarded(startup)();
});
// 提供只读状态与用户流程入口，供 Qt 集成测试驱动真实页面（不启用远程调试端口）。
window.pengpeng = {
  state,
  api,
  showCard,
  cardTab,
  changeView,
  refresh,
  playAsset,
  showEffect,
};

// 页码围绕当前页显示最多十页，跳页与页容量均以同一 limit 计算偏移。
function renderPagination() {
  const pages = Math.max(1, Math.ceil(state.total / state.limit));
  const current = Math.floor(state.offset / state.limit) + 1;
  $("#page-number").textContent = `${current} / ${pages}`;
  $("#prev").disabled = !!state.loading || current <= 1;
  $("#next").disabled = !!state.loading || current >= pages;
  for (const id of ["prev", "next", "page-number", "page-shortcuts", "page-jump-form"])
    $("#" + id).hidden = state.infiniteScroll;
  const first = Math.max(1, Math.min(current - 4, pages - 9));
  $("#page-shortcuts").innerHTML = Array.from({length: Math.min(10, pages)}, (_, i) => {
    const page = first + i;
    return `<button class="subtle" data-page="${page}" ${page === current ? 'aria-current="page" disabled' : ''}>${page}</button>`;
  }).join("");
  $("#page-shortcuts").querySelectorAll("button").forEach(b => b.onclick = guarded(() => goPage(Number(b.dataset.page))));
  $("#jump-page").max = pages;
  $("#page-size").value = String(state.limit);
  $("#load-more").hidden = !state.infiniteScroll;
  $("#load-more").disabled = !!state.loading || state.offset + state.limit >= state.total;
  $("#load-more").textContent = state.loading ? "正在加载…" : state.offset + state.limit >= state.total ? "已显示全部" : "加载更多";
  SelectUI.refresh();
}
async function goPage(page) {
  if (state.loading) return;
  const pages = Math.max(1, Math.ceil(state.total / state.limit));
  if (!Number.isInteger(page) || page < 1 || page > pages) { toast(`请输入 1 至 ${pages} 的整数页码`); return; }
  const previous = state.offset;
  state.offset = (page - 1) * state.limit;
  try { await refresh(false, true); } catch (error) { state.offset = previous; renderPagination(); throw error; }
}
$("#prev").onclick = guarded(() => goPage(Math.floor(state.offset / state.limit)));
$("#next").onclick = guarded(() => goPage(Math.floor(state.offset / state.limit) + 2));
$("#page-jump-form").onsubmit = e => {
  // 在 Promise 微任务之前阻止表单导航，按 Enter 与点击跳转行为一致。
  e.preventDefault();
  guarded(() => goPage(Number($("#jump-page").value)))();
};
$("#page-size").onchange = guarded(async () => {
  const size = Number($("#page-size").value);
  await api("save_settings", {page_size: size});
  state.limit = size; state.status.settings.page_size = size; state.offset = 0;
  $("#results").style.minHeight = "";
  await refresh();
});
async function loadMore() {
  if (!state.infiniteScroll || state.loading || state.offset + state.limit >= state.total || $("#library").hidden) return;
  const previous = state.offset;
  state.offset += state.limit;
  try { await refresh(true); } catch (error) { state.offset = previous; renderPagination(); throw error; }
}
$("#load-more").onclick = guarded(loadMore);
// 滚动事件只在哨兵接近视口时发出一页请求，失败后可用按钮重试。
window.addEventListener("scroll", () => {
  if (state.infiniteScroll && $("#load-more").getBoundingClientRect().top < innerHeight + 180)
    guarded(loadMore)();
}, {passive: true});
function attachAudioExports(root) {
  root.querySelectorAll("[data-export]").forEach(source => {
    const folder = state.audioExports.get(source.dataset.export);
    if (!folder) return;
    const row = source.closest(".voice-row");
    let button = row.querySelector(".open-audio-export");
    if (!button) { button = document.createElement("button"); button.className = "subtle open-audio-export"; row.append(button); }
    button.textContent = "打开导出文件夹 ↗";
    button.onclick = () => host.openFolder(folder);
  });
}

async function supplementTranscripts(card, generation, force = false) {
  if (state.transcriptPending === generation) return;
  if (generation !== state.detailGeneration) return;
  if (state.status.settings.online_transcripts === false || state.voiceLocale !== "zhcn" || !card.record.dbfid || !state.voiceItems.some(a => a.kind === "voice" && !a.text)) {
    await recognizeMissing(generation);
    return;
  }
  state.transcriptPending = generation;
  state.transcriptNote = "正在按需查询公开台词，本地试听与导出可继续使用…";
  renderVoiceList();
  try {
    const data = await api("transcripts", {dbfid: card.record.dbfid, locale: state.voiceLocale, items: state.voiceItems, force});
    if (generation !== state.detailGeneration) return;
    const matches = new Map(data.items.map(item => [item.id, item]));
    state.voiceItems = state.voiceItems.map(item => !item.text && matches.has(item.id) ? {...item, ...matches.get(item.id)} : item);
    state.transcriptNote = data.note;
    state.transcriptSources = data.sources || [];
  } catch (error) {
    if (generation !== state.detailGeneration) return;
    state.transcriptNote = error.message + "；可重试获取缺失台词，本地试听不受影响。";
    state.transcriptSources = [...new Set(state.voiceItems.filter(x => x.kind === "voice" && !x.text).map(x => x.transcript_dbfid || card.record.dbfid))];
  } finally {
    if (state.transcriptPending === generation) state.transcriptPending = null;
  }
  if (generation === state.detailGeneration) {
    renderVoiceList();
    await recognizeMissing(generation);
  }
}

// 每页只维护一个顺序任务，不提前把全部音频塞入后端队列。切卡、切语言、
// 关闭页签会同时使 token 失效并终止独立进程，旧结果不能污染新页面。
function cancelSpeech() {
  state.speechRun = null;
  state.speechNote = "";
  if (host) api("cancel_speech").catch(() => {});
}
async function recognizeMissing(generation, retryId = null) {
  if (generation !== state.detailGeneration || state.speechRun || state.status.settings.speech_recognition === false) return;
  if (!["zhcn", "enus"].includes(state.voiceLocale)) {
    state.speechNote = "此语言暂不支持轻量语音识别；原有台词查询、试听和导出仍可使用。";
    renderVoiceList();
    return;
  }
  const items = [...new Map(state.voiceItems.filter(a => a.kind === "voice" && !a.text &&
    (retryId ? a.id === retryId : !a.speech_done && !a.speech_error)).map(a => [a.id, a])).values()];
  if (!items.length) return;
  const run = {generation}, locale = state.voiceLocale;
  state.speechRun = run;
  const active = () => state.speechRun === run && generation === state.detailGeneration;
  let completed = 0;
  try {
    for (const item of items) {
      if (!active()) return;
      // 网络来源重试可能在等待期间补齐文字；识别永远不盖过已有台词。
      if (state.voiceItems.some(a => a.id === item.id && a.text)) continue;
      state.speechNote = `正在准备离线识别 ${++completed} / ${items.length}；试听与导出可继续使用…`;
      renderVoiceList();
      try {
        const audioData = await api("audio", {assetid: item.id, locale});
        if (!active()) return;
        const result = await api("speech", {paths: audioData.samples.map(s => s.path), locale, force: !!retryId});
        if (!active()) return;
        state.voiceItems.forEach(a => { if (a.id === item.id && !a.text) Object.assign(a, {
          speech_text: result.text, speech_cached: result.cached, speech_done: true, speech_error: ""
        }); });
      } catch (error) {
        if (!active()) return;
        state.voiceItems.forEach(a => { if (a.id === item.id) a.speech_error = error.message; });
        // 模型/网络故障不应对同一页数十条语音反复重试；保留逐条主动重试入口。
        state.speechNote = "自动识别已暂停：" + error.message + "。可点击语音识别按钮重试。";
        return;
      }
    }
    if (active()) state.speechNote = "离线识别完成；语音识别文字仅供参考，不保证准确性。";
  } finally {
    if (active()) { state.speechRun = null; renderVoiceList(); }
  }
}
