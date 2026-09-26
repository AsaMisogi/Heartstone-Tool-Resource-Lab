/* 砰砰解析台桌面界面。所有数据来自本地业务桥，不连接远程卡牌 API。
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
    subgroup: "",
    filters: {},
    viewStates: {},
    catalogDisplay: {mode: "grid", size: 180},
    cardHistory: [],
    // 两个图鉴独立记忆排序，避免浏览皮肤后改变卡牌顺序。
    catalogOrder: {cards: {sort: "default", descending: false}, heroes: {sort: "default", descending: false}, battlegrounds: {sort: "default", descending: false}},
    voiceLocale: "zhcn",
    otherVoicesExpanded: true,
    voiceKind: "voice",
    voiceGroup: "all",
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
  if (!state.preferencesReady || !["cards", "heroes", "battlegrounds", "audio", "effects"].includes(state.view)) return;
  const snapshot = {query: $("#search").value.trim(), locale: state.locale,
    category: state.category, subgroup: state.subgroup, favorites: state.favorites, filters: {...state.filters},
    display: {...state.catalogDisplay},
    order: {...(state.catalogOrder[state.view] || {sort: "default", descending: false})}};
  if (JSON.stringify(state.viewStates[state.view]) === JSON.stringify(snapshot)) return;
  state.viewStates[state.view] = snapshot;
  api("save_settings", {view_state: state.viewStates}).catch(e => toast("筛选保存失败：" + e.message));
}
function restoreView() {
  const saved = state.viewStates[state.view] || {};
  state.catalogDisplay = {mode: saved.display?.mode === "list" ? "list" : "grid",
    size: Math.min(280, Math.max(140, Number(saved.display?.size) || 180))};
  state.query = saved.query || "";
  state.locale = saved.locale || state.status?.settings.locale || "zhcn";
  state.category = saved.category || "";
  state.subgroup = saved.subgroup || "";
  // 旧分类已拆成大类与场景，迁移筛选记忆，避免升级后卡在不存在的分类。
  if (state.view === 'audio' && ['音乐 / 登场曲', '环境 / 棋盘', '界面 / 交互', '战斗 / 法术', '其他音效'].includes(state.category)) {
    state.category = state.category === '音乐 / 登场曲' ? '' : '音效';
    state.subgroup = '';
  }
  state.filters = {...saved.filters};
  if (state.view === 'heroes') { delete state.filters.race; delete state.filters.keyword; }
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
  const rich = esc(String(value || "").replace(/\[x\]/g, "").replace(/\[b\]|\\n/g, "\n").replace(/\$(?=\d)/g, ""))
    .replace(/&lt;(\/?)(b|i)&gt;/gi, '<$1$2>').replace(/&lt;br\s*\/?&gt;/gi, '<br>');
  // 只替换文本节点，不在 HTML 属性里做字符串匹配；词条解释来自客户端词表。
  const template = document.createElement('template');
  template.innerHTML = rich;
  const keywords = (state.card?.keywords || []).filter(k => plain(k.name).length > 1)
    .sort((a,b) => b.name.length-a.name.length);
  const terms = [...(state.card?.text_links || []).map((r,index)=>({name:r.label, relation:index})), ...keywords]
    .sort((a,b)=>b.name.length-a.name.length);
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.textContent, fragment = document.createDocumentFragment();
    let cursor = 0;
    while (cursor < text.length) {
      let next = null, at = text.length;
      for (const keyword of terms) {
        const index = text.indexOf(plain(keyword.name), cursor);
        if (index >= 0 && index < at) { next = keyword; at = index; }
      }
      fragment.append(text.slice(cursor, at));
      if (!next) break;
      const button = document.createElement('button');
      button.className = 'keyword';
      if (next.relation !== undefined) button.dataset.textRelation = next.relation;
      else button.dataset.keyword = next.name;
      button.textContent = plain(next.name); button.setAttribute('aria-label', `${next.relation !== undefined ? '查看关联卡牌' : '解释'}：${button.textContent}`);
      fragment.append(button); cursor = at + button.textContent.length;
    }
    node.replaceWith(fragment);
  }
  return template.innerHTML;
}

function encyclopediaFacts(c) {
  const versions = c.versions || [], links = c.voice_links || [];
  const linked = [...new Map(links.map(r => [r.name, r])).values()];
  return `${versions.length > 1 ? `<details class="edition-panel"><summary>同名版本 <span>${versions.length}</span></summary><div class="edition-list">${versions.map(v => `<button class="edition-item ${v.id === c.id ? 'active' : ''}" data-related-card="${esc(v.id)}"><img class="edition-thumb" data-edition-image="${esc(v.id)}" alt="${esc(v.name)}缩略图"><b>${esc(v.context)} · ${esc(v.summary.sets?.join(' / '))}</b><small>${esc(v.id)}</small><p>${esc(plain(v.text) || v.summary.type || '')}</p></button>`).join('')}</div></details>` : ''}
    ${linked.length ? `<details class="related-cards voice-relations" open><summary>${c.hero ? '专属语音 · 触发卡牌' : '使用此牌时有专属语音的英雄'} · ${linked.length}</summary><div>${linked.map(r => `<button class="related-card" data-related-card="${esc(r.id)}"><img class="relation-thumb" data-relation-image="${esc(r.id)}" alt=""><b>${esc(r.name)}</b><small>查看${r.hero ? '英雄' : '卡牌'} ↗</small></button>`).join('')}</div></details>` : ''}`;
}
function cardFacts(c) {
  const m = c.metadata || {};
  if (!Object.keys(m).length) return '';
  const facts = [[m.battlegrounds ? "酒馆等级" : "法力", m.battlegrounds ? m.tier : m.cost], ["战棋版本", m.battlegrounds ? (m.bg_golden ? "金色随从" : "普通随从") : null], ["随从池", m.battlegrounds ? (m.bg_pool ? "客户端标记可入池" : "衍生 / 非入池") : null], ["类别", m.type], ["稀有度", m.battlegrounds ? null : m.rarity],
    ["职业", m.battlegrounds ? null : m.classes?.join(" / ")], ["种族", m.races?.join(" / ") || "无"],
    ["攻击 / 生命", m.attack != null && m.health != null ? `${m.attack} / ${m.health}` : undefined],
    ["攻击", m.health == null ? m.attack : undefined], ["生命", m.attack == null ? m.health : undefined], ["耐久", m.durability],
    ["系列", m.sets?.join(" / ")], ["发行批次", m.editions?.join(" / ")]];
  return `<section class="detail-facts"><h3>基础信息</h3><dl class="card-facts">${facts.filter(([,v]) => v !== null && v !== undefined && v !== '').map(([k,v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
    <details class="related-cards" ${c.related?.length ? 'open' : ''}><summary>相关卡牌 · ${c.related?.length || 0}</summary>${c.related?.length ? `<div>${c.related.map(r => `<button class="related-card" data-related-card="${esc(r.id)}"><img class="relation-thumb" data-relation-image="${esc(r.id)}" alt=""><b>${esc(r.name)}</b><small>${esc(r.variant || r.id)} ↗</small></button>`).join('')}</div>` : '<p class="note">客户端关系表未提供此卡的相关卡牌。</p>'}</details></section>`;
}
// 卡片只消费随列表返回的摘要；所有数据先转义，颜色只作辅助，文字仍可辨认。
function catalogTile(c) {
  const m = c.summary || {};
  const rarity = {免费: 'free', 普通: 'common', 稀有: 'rare', 史诗: 'epic', 传说: 'legendary'}[m.rarity] || 'unknown';
  const classes = (m.classes || []).map(v => v === '通用英雄 / 中立' ? (c.hero ? '通用 / 中立' : '中立') : v).join(' / ') || '职业未标注';
  const sets = m.sets?.join(' / ') || '系列未标注';
  // 0 是有效属性；法术不虚构攻击/生命，武器优先展示耐久。
  const stats = c.hero ? '' : [[m.battlegrounds ? '星级' : '费用', m.battlegrounds ? m.tier : m.cost, 'mana'], ['攻击', m.attack, 'attack'],
    [m.durability != null ? '耐久' : '生命', m.durability ?? m.health, 'health']]
    .filter(([, value]) => value != null)
    .map(([label, value, style]) => `<span class="tile-stat ${style}"><span>${label}</span><strong>${esc(value)}</strong></span>`).join('');
  const kind = c.hero ? (m.battlegrounds ? '酒馆战棋 · 英雄 / 皮肤' : '英雄 / 皮肤') : m.battlegrounds ? `战棋 · ${m.bg_golden ? '金色' : '普通'}随从` : (m.type || '类型未标注');
  const races = m.races?.join(' / ');
  return `<button class="card-tile rarity-${rarity}" data-card="${esc(c.id)}" title="${esc(c.name)} · ${esc(c.id)}">
    <div class="card-image"><span class="placeholder">◈</span>${c.is_new ? `<span class="new-badge">NEW</span>` : ""}${c.version_count > 1 ? `<span class="version-badge">${c.version_count} 个版本</span>` : ""}</div>
    <div class="tile-info"><b class="tile-name">${esc(c.name)}</b>
      <div class="tile-kind ${m.battlegrounds ? 'is-battlegrounds' : ''}">${esc(kind)}${!c.hero && !m.battlegrounds ? `<span class="tile-rarity">${esc(m.rarity || '未标注')}</span>` : ''}</div>
      <div class="tile-traits">${!m.battlegrounds ? `<span class="tile-class">${esc(classes)}</span>` : ''}${!c.hero ? `<span class="tile-races">${esc(races || '无种族')}</span>` : ''}</div>
      ${stats ? `<div class="tile-stats">${stats}</div>` : ''}
      <div class="tile-set" title="${esc(sets)}">${esc(sets)}</div>
      <small class="tile-id">${esc(c.id)}</small>
      ${state.catalogOrder[state.view].sort === 'release' ? `<small class="release-date" title="${esc(c.release_source)}">${esc(c.release_date || '日期未知')}${c.release_source?.startsWith('系列') ? ' · 系列参考' : ''}</small>` : ''}
    </div></button>`;
}
const audio = $("#audio");
audio.volume = 0.75;

function api(method, params = {}, background = false) {
  return new Promise((resolve, reject) => {
    const id = ++serial;
    pending.set(id, { resolve, reject, method, started: performance.now() });
    const scope = ['card','portrait','card_render','card_audio','invalidate_detail'].includes(method) ? 'detail' :
      ['list_cards','list_assets'].includes(method) ? 'catalog' : '';
    host.request(JSON.stringify({ id, method, params, scope: background ? "voice-background" : scope, background }));
    updateActivity();
  });
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").hidden = true), 7000);
}
function guarded(fn) {
  return (...args) => {
    // 在事件分发结束前保留按钮引用；只标记当前操作，避免慢任务被连点重复入队。
    const target = args[0]?.currentTarget;
    // 导航永远可重入：点击新标签立即更新选择，旧请求只允许填充自己的代次。
    const navigation = target?.matches?.('[data-tab],[data-variant],[data-render-variant],.nav,[data-play],[data-replay]');
    const button = !navigation && target instanceof HTMLButtonElement ? target : null;
    if (button?.getAttribute("aria-busy") === "true") return Promise.resolve();
    button?.setAttribute("aria-busy", "true");
    return Promise.resolve()
      .then(() => fn(...args))
      .catch((e) => {
        toast(e.message);
        // 错误的内容区域由请求所有者处理，不能把新页面的加载状态改成旧错误。
      }).finally(() => button?.removeAttribute("aria-busy"));
  };
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
      updateActivity();
      data.error ? p.reject(Object.assign(new Error(data.error), {cancelled: !!data.cancelled})) : p.resolve(data.result);
    }
    return;
  }
  if (data.event === 'export_progress') {
    const task = [...pending.values()].find(p => p.method === 'export');
    if (task) { task.stage = `${data.message} · ${data.done} / ${data.total}`; updateActivity(); }
  } else if (data.event === 'transcript_progress') {
    if (pending.has(data.request_id) && state.transcriptPending === state.detailGeneration) {
      const note = $('#speech-inline-status');
      if (note) note.textContent = `${data.message} · ${data.done} / ${data.total} · 音频可直接播放`;
    }
  } else if (data.event === "speech_progress") {
    if ($("#speech-status-result") && pending.has(data.request_id)) $("#speech-status-result").textContent = data.message;
    if (state.speechRun?.generation === state.detailGeneration && pending.has(data.request_id)) {
      state.speechNote = `${state.speechCount || ""} · ${data.message}`;
      if ($("#speech-inline-status")) $("#speech-inline-status").textContent = state.speechNote;
    }
  } else if (data.event === "log") {
    state.logs.push(data.message);
    if (state.logs.length > 450) state.logs.shift();
    if (!$("#log-panel").hidden) renderLogs();
  } else if (data.event === "progress") {
    $("#jobbar").hidden = false;
    $("#job-message").textContent = data.message;
    $("#job-count").textContent = data.total > 0 ? `${fmt(data.done)} / ${fmt(data.total)}` : '正在处理…';
    $("#job-progress").max = data.total || 1;
    if (data.total > 0) $("#job-progress").value = data.done;
    else $("#job-progress").removeAttribute('value');
    if ($("#welcome").open) {
      $("#welcome-progress").hidden = false;
      $("#welcome-progress").textContent = `${data.message} · ${$("#job-count").textContent}`;
    }
  } else if (data.event === "scan_finished") {
    state.scanning = false;
    $("#jobbar").hidden = true;
    $("#scan-button").disabled = false;
    updateStatus(data.status);
    toast(
      data.error ||
        (data.cancelled
          ? "索引已暂停，下次扫描将复用已完成部分。"
          : data.scope === "all" ? "完整资源索引已完成，可以检索、试听和导出全部已索引资源。"
          : "当前页面索引已完成，可以试听和导出本页资源；本次仅建立页面所需索引。"),
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
  if (s?.app_version) {
    $('#app-version').textContent = `BOOM LAB · v${s.app_version}`;
    document.title = `砰砰解析台 v${s.app_version}`;
  }
  if (!s?.ready) return;
  state.pairedAudio = !!s.settings.paired_audio;
  state.generalAudio = !!s.settings.general_audio;
  state.mixVoiceExport = !!s.settings.mix_voice_export;
  state.infiniteScroll = !!s.settings.infinite_scroll;
  WheelScroll.setMode(s.settings.scroll_mode);
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
async function initialize(path, accepted = false) {
  if (!accepted) {
    const report = await api('game_status', path ? {game_path:path} : {});
    if (report.changed) { showGameUpdate(report, path); return false; }
  }
  state.initializing = true;
  closeDetail(); stopPlayback();
  thumbnailCache.clear();
  $("#connection").textContent = "正在读取资源…";
  $("#jobbar").hidden = false;
  $("#cancel-scan").hidden = true;
  $("#job-message").textContent = '正在验证安装目录';
  $("#job-progress").removeAttribute('value');
  $("#results").innerHTML =
    '<div class="empty"><span class="spinner"></span><strong>正在打开本地档案馆</strong>首次连接或升级会建立卡牌、版本和语音关系索引。</div>';
  try {
    const s = await api("initialize", path ? { game_path: path } : {});
    state.locale = s.settings.locale || "zhcn";
    state.selected.clear();
    state.offset = 0;
    updateStatus(s);
    if (s.string_warnings?.length) toast(`${s.string_warnings.length} 个字幕文件未读取，其他资源可正常使用；详情见资源与设置。`);
    restoreView();
    renderView();
    $("#jobbar").hidden = true;
    renderFilters();
    await refresh();
    $("#cancel-scan").hidden = false;
    if ($("#welcome").open) $("#welcome").close();
    // 首次连接后才提示；用户选择会持久保存，重启不反复打扰。
    if (s.update_index_pending || (!s.settings.index_guide_seen && s.indexed < s.bundles)) {
      $('#index-guide-title').textContent = s.update_index_pending ? '游戏已更新，建议重建完整资源索引' : '让资源查找更顺畅';
      $('#index-guide').showModal();
    }
    return true;
  } catch (e) {
    $("#jobbar").hidden = true;
    $("#cancel-scan").hidden = false;
    state.status = await api('status');
    $('#connection').textContent = '连接未完成，请重试';
    toast(e.message);
    state.view = "settings";
    renderView();
    return false;
  } finally {
    state.initializing = false;
  }
}

function showGameUpdate(report, path) {
  if ($('#game-update-dialog').open) return;
  $('#game-update-message').textContent = `检测到客户端资源变化${report.game_version ? ' · ' + report.game_version : ''}。重新分析后将切换到新快照，并对照上次分析标记新增卡牌。`;
  $('#game-analyze').onclick = guarded(async()=>{ $('#game-update-dialog').close(); await initialize(path, true); });
  $('#game-update-dialog').showModal();
  state.gameUpdateNotified = report.fingerprint;
}
async function checkGameUpdate() {
  if (!state.status?.ready || state.initializing || state.checkingGame) return;
  state.checkingGame = true;
  try {
    const report = await api('game_status');
    if (report.changed && report.fingerprint !== state.gameUpdateNotified) showGameUpdate(report);
  } catch (e) { console.warn('客户端版本检查：', e.message); }
  finally { state.checkingGame = false; }
}
window.addEventListener('focus', checkGameUpdate);
setInterval(checkGameUpdate, 60000);
$('#game-update-later').onclick = () => $('#game-update-dialog').close();
$('#relation-close').onclick = () => $('#relation-dialog').close();
// 仅完整点击空白处才关闭，避免从卡牌拖动到外侧释放时误关。
let relationBlankDown = false;
$('#relation-dialog').addEventListener('pointerdown', e => {
  relationBlankDown = !e.target.closest('button, h2, img');
});
$('#relation-dialog').addEventListener('click', e => {
  if (relationBlankDown && !e.target.closest('button, h2, img')) $('#relation-dialog').close();
  relationBlankDown = false;
});
$('#relation-dialog').addEventListener('close', () => {
  state.relationObservers = (state.relationObservers || []).filter(item => {
    if (item.root !== $('#relation-dialog')) return true;
    item.observer.disconnect(); return false;
  });
});

const views = {
  battlegrounds: ["战棋图鉴", "BATTLEGROUNDS", "走进酒馆，认识每一位伙伴。", "按酒馆等级、种族和词条浏览本地战棋随从，查看普通与金色版本。"],
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
  $("#audio-subgroup").hidden = state.view !== "audio";
  $("#audio-library-note").hidden = state.view !== "audio";
  $("#export-selected").hidden = state.view !== "audio";
  $("#favorites").hidden = state.view === "effects";
  $("#search").placeholder = ["cards", "heroes", "battlegrounds"].includes(state.view)
    ? (state.view === "heroes" ? "搜索英雄、皮肤名称、文本或 ID…" : "搜索卡牌名称、文本或 ID…")
    : "搜索资源名称、资源包或 GUID…";
  $("#view-hint").textContent =
    state.view === "audio"
      ? "中文及通用音效 · WAV 导出"
      : state.view === "effects"
        ? "资源预制体 · 实验性预览"
        : "原始纹理 · 中文优先";
  $("#only-new").checked = state.filters.new === "1";
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
  window.scrollTo({top:0, behavior:'instant'});
  if (!["settings", "exports"].includes(view)) await refresh();
}

async function refresh(append = false, preservePosition = false) {
  if (!state.status?.ready) {
    $('#results').innerHTML = '<div class="empty">本地资源尚未连接，请在「资源与设置」中连接炉石目录。</div>';
    return;
  }
  rememberView();
  const generation = append ? state.generation : ++state.generation;
  const cards = ["cards", "heroes", "battlegrounds"].includes(state.view);
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
    // 只为可见区域及下一屏附近的卡牌解码缩略图，避免 96 条/无限列表
    // 在滚动时为屏外图片持续解码、上传纹理。追加页面共用同一观察器。
    if (!append || !thumbnailObserver) {
      thumbnailQueue = [];
      thumbnailGeneration++;
      thumbnailObserver?.disconnect();
      const observer = new IntersectionObserver(entries => {
        if (thumbnailObserver !== observer) return;
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          observer.unobserve(entry.target);
          thumbnailQueue.push({card:thumbnailCards.get(entry.target), generation:thumbnailGeneration});
        }
        drainThumbnails();
      }, {rootMargin:'300px'});
      thumbnailObserver = observer;
    }
    const cardsById = new Map(data.items.map(card=>[card.id,card]));
    document.querySelectorAll('[data-card]').forEach(node=>{
      const card = cardsById.get(node.dataset.card);
      if (card) { thumbnailCards.set(node,card); thumbnailObserver.observe(node); }
    });
  } else {
    if (state.view === "audio") {
      thumbnailObserver?.disconnect(); thumbnailObserver = null;
      thumbnailQueue = []; thumbnailGeneration++;
      $("#category").innerHTML =
        '<option value="">所有分类</option>' +
        data.categories
          .map((c) => `<option value="${esc(c)}">${esc(c)}</option>`)
          .join("");
      $("#category").value = state.category;
      const subgroups = [...(data.subgroups || [])];
      if (state.subgroup && !subgroups.some(g => g.subgroup === state.subgroup))
        subgroups.push({subgroup:state.subgroup, count:0});
      $('#audio-subgroup').innerHTML = '<option value="">所有场景</option>' + subgroups
        .map(g=>`<option value="${esc(g.subgroup)}">${esc(g.subgroup)} · ${g.count}</option>`).join('');
      $('#audio-subgroup').value = state.subgroup;
      $('#audio-library-note').textContent = (data.index_complete ? '完整资源索引' : data.music_indexed ? '音乐与环境索引就绪 · 其他声音可建立完整索引' : state.musicScanRequested && !state.scanning ? '音乐索引尚未完成 · 可点击建立完整索引继续' : '当前仅为部分资源 · 正在补齐音乐与环境索引') + '。中文注释来自命名规则，原始名称保留以供核对。';
      if (!data.music_indexed && !state.scanning && state.musicScanRequested !== state.status.version) {
        state.musicScanRequested = state.status.version;
        startScan('music').catch(e=>{ state.musicScanRequested = false; toast(e.message); });
      }
    }
    $("#results").insertAdjacentHTML("beforeend", data.items
      .map(
        (a) =>
          `<div class="list-row">${state.view === "audio" ? `<input type="checkbox" data-select="${esc(a.id)}" aria-label="选择 ${esc(a.name)}" ${state.selected.has(a.id) ? "checked" : ""}>` : "<span></span>"}<button class="sound-icon" data-asset="${esc(a.id)}" aria-label="${state.view === "audio" ? "试听" : "预览"}">${state.view === "audio" ? "▷" : "✧"}</button><div><b>${esc(a.name)} ${a.is_new ? `<span class="new-badge inline">NEW</span>` : ""}</b>${a.annotation ? `<p class="audio-annotation" title="资源命名规则注释">${esc(a.annotation)}</p>` : ""}<small>${esc(a.bundle)}</small></div><span class="type">${esc(a.category)}<small>${esc(a.locale)}</small></span><span class="type">${a.duration ? time(a.duration) : "—"}</span><button class="icon-button" data-fav="${esc(a.id)}" aria-label="收藏">${a.favorite ? "★" : "☆"}</button></div>`,
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
let thumbnailObserver = null, thumbnailGeneration = 0;
const thumbnailCards = new WeakMap();
const thumbnailCache = new Map();
function drainThumbnails() {
  while (thumbnailWorkers < 2 && thumbnailQueue.length) {
    thumbnailWorkers++;
    (async () => {
      try {
        while (thumbnailQueue.length) {
          const {card: c, generation} = thumbnailQueue.shift();
          if (generation !== thumbnailGeneration) continue;
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
            if (generation !== thumbnailGeneration) continue;
            const node = [...document.querySelectorAll("[data-card]")].find(n => n.dataset.card === c.id)?.querySelector(".card-image");
            if (node && image.url) {
              // 解码完成后一次替换，保留固定容器和版本角标；不会先移除占位，
              // 再让大图解码/上传造成滚动中的空白帧。并发仍受两个 worker 限制。
              const decoded = new Image();
              decoded.alt = c.name; decoded.src = image.url;
              await decoded.decode();
              if (generation !== thumbnailGeneration || !node.isConnected) continue;
              const old = node.querySelector('img,.placeholder');
              if (old) old.replaceWith(decoded); else node.prepend(decoded);
            }
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
          battlegrounds: state.view === "battlegrounds",
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
          new: state.filters.new === "1",
          locale: state.locale,
          category: state.category,
          subgroup: state.subgroup,
          offset: state.offset,
          limit: state.limit,
          favorites: state.favorites,
        },
  );
}

function closeDetail() {
  detailResize.cancel();
  // 已播放的声音可以继续听；尚未完成的旧详情试听不应在离开后突然响起。
  if (state.preparingAudio) stopPlayback();
  if (host) api('invalidate_detail').catch(() => {});
  state.relationObservers?.forEach(x=>x.observer.disconnect()); state.relationObservers = [];
  // 详情关闭后停止动态卡面解码，避免隐藏视频继续占用 GPU。
  document.querySelectorAll('#detail video').forEach(video => video.pause());
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
  state.otherVoices = [];
  api('invalidate_detail').catch(() => {});
  if (fx) {
    fx.stop();
    fx = null;
  }
}
async function showCard(cardid, navigation = "new") {
  if (state.preparingAudio) stopPlayback();
  if (navigation === "related" && state.card) state.cardHistory.push({id: state.card.id, tab: state.tab, variant: state.variant, renderVariant: state.renderVariant || 0, scroll: $("#detail").scrollTop, voiceLocale: state.voiceLocale, voiceKind: state.voiceKind, voiceGroup: state.voiceGroup});
  if (navigation === "new") state.cardHistory = [];
  cancelSpeech();
  const generation = ++state.detailGeneration;
  state.variant = 0;
  state.renderVariant = 0;
  // 同步模式跟随当前页面筛选；独立模式跨卡牌、跨重启沿用同一偏好。
  state.voiceLocale = state.status.settings.sync_voice_locale !== false
    ? state.locale : (state.status.settings.voice_locale || "zhcn");
  state.voiceKind = "voice";
  state.voiceGroup = "all";
  state.voiceItems = [];
  state.card = null;
  state.tab = "art";
  openDetail();
  $("#detail-kicker").textContent = "CARD INSPECTOR";
  $("#detail-content").innerHTML =
    '<div class="empty"><span class="spinner"></span> 正在解析卡牌…</div>';
  let card;
  try { card = await api("card", { cardid, locale: state.locale }); }
  catch (e) {
    if (generation === state.detailGeneration) $("#detail-content").innerHTML = `<p class="warning">${esc(e.message)}</p>`;
    return;
  }
  if (generation !== state.detailGeneration) return;
  state.card = card;
  renderCard();
  await cardTab("art");
  if (state.card === card && state.tab === 'art') $("#detail").scrollTop = 0;
}
function renderCard() {
  state.relationObservers?.forEach(x=>x.observer.disconnect()); state.relationObservers = [];
  const c = state.card;
  $("#detail-kicker").textContent = c.metadata?.battlegrounds ? "BATTLEGROUNDS INSPECTOR" : "CARD INSPECTOR";
  $("#detail-content").innerHTML =
    `<div class="card-id">${esc(c.id)} · ${c.hero ? "英雄 / 皮肤" : "卡牌"}</div><h2>${esc(c.name)}</h2>${c.metadata?.battlegrounds ? `<p class="bg-summary">★ ${esc(c.metadata.tier || "—")} 星 · ${c.metadata.bg_golden ? "金色" : "普通"}随从 · ${esc(c.metadata.races?.join(" / ") || "无种族")} · ${esc(c.metadata.attack)} / ${esc(c.metadata.health)}</p>` : ""}<div class="detail-actions"><button id="favorite-card" class="subtle">${c.favorite ? "★ 已收藏" : "☆ 收藏卡牌"}</button><button id="export-card" class="subtle">↓ 导出图像、文本与全部语音</button></div><div class="detail-tabs"><button data-tab="art" class="active">原画</button><button data-tab="render">完整卡面</button><button data-tab="voices">语音</button><button data-tab="effects">特效</button><button data-tab="raw">源数据</button></div><div id="detail-body"></div>`;
  $("#detail-content").insertAdjacentHTML("afterbegin", state.cardHistory.length ? '<button id="back-card" class="subtle">← 返回上一张卡牌</button>' : '');
  if ($("#back-card")) $("#back-card").onclick = guarded(async () => {
    const previous = state.cardHistory.pop();
    await showCard(previous.id, "back");
    if (state.card?.id !== previous.id) return;
    const restoredCard = state.card;
    state.variant = previous.variant;
    state.renderVariant = previous.renderVariant;
    state.voiceKind = previous.voiceKind; state.voiceGroup = previous.voiceGroup || "all";
    if (previous.tab !== "art" || previous.variant) await cardTab(previous.tab);
    if (state.card === restoredCard) $("#detail").scrollTop = previous.scroll;
  });
  // 基础信息独立放在动态标签内容之后，切换标签不会移动或重复创建。
  $("#detail-content").insertAdjacentHTML("beforeend", encyclopediaFacts(c) + cardFacts(c));
  // 关系列表按可见区域读取缩略图；同一时刻仅一项，关闭详情即停止后续工作。
  loadRelationImages($('#detail-content'));
  const editions = document.querySelector('.edition-panel');
  if (editions) editions.addEventListener('toggle', async () => {
    if (!editions.open || editions.dataset.loading) return;
    editions.dataset.loading = 'true';
    // 展开才顺序读取缩略图，避免一次排入几十个请求挡住试听。
    for (const image of editions.querySelectorAll('[data-edition-image]')) {
      if (!editions.isConnected) break;
      try {
        const result = await api('thumbnail', {cardid:image.dataset.editionImage, locale:state.locale});
        image.src = result.url;
      } catch { image.alt = '原画未安装'; }
    }
  });
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
function loadRelationImages(root) {
  const queue = []; let busy = false;
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      observer.unobserve(entry.target); queue.push(entry.target);
    }
    drain();
  }, {root:root.tagName === 'DIALOG' ? root : $('#detail'), rootMargin:'100px'});
  async function drain() {
    if (busy) return;
    busy = true;
    while (queue.length && root.isConnected && (root.tagName !== 'DIALOG' || root.open)) {
      const image = queue.shift();
      if (!image.isConnected) continue;
      try { const result = await api('thumbnail', {cardid:image.dataset.relationImage,locale:state.locale}); image.src=result.url; }
      catch { image.alt='暂无原画'; }
    }
    busy = false;
  }
  root.querySelectorAll('[data-relation-image]').forEach(image=>observer.observe(image));
  // 详情重建后释放观察器，避免保存历史页面和失效缩略图节点。
  state.relationObservers ||= [];
  state.relationObservers = state.relationObservers.filter(item=>{
    if (!item.root.isConnected || (item.root.tagName === 'DIALOG' && !item.root.open)) {item.observer.disconnect();return false;}return true;
  });
  state.relationObservers.push({root,observer});
}
async function cardTab(tab) {
  if (!state.card) return;
  cancelSpeech();
  state.tab = tab;
  const c = state.card;
  const generation = ++state.detailGeneration;
  SelectUI.close();
  api('invalidate_detail').catch(() => {});
  document.querySelectorAll('#detail video').forEach(v => v.pause());
  api('cancel_effect').catch(() => {});
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
  try {
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
    const quality = state.renderVariant || 0;
    body.innerHTML = `<div class="variant-tabs">${['普通','金卡','异画','钻石'].map((label,i) => `<button data-render-variant="${i}" class="${i===quality?'active':''}">${label}</button>`).join('')}</div><p class="note">在线最新卡面 · 首次联网，缓存后可离线查看</p><div id="render-stage" class="empty"><span class="spinner"></span> 正在读取完整卡面…</div>`;
    body.querySelectorAll('[data-render-variant]').forEach(b => b.onclick = guarded(() => {
      state.renderVariant = Number(b.dataset.renderVariant); return cardTab('render');
    }));
    try {
      const image = await api("card_render", {cardid: c.id, locale: state.locale, variant: quality});
      if (generation !== state.detailGeneration) return;
      $("#render-stage").className = "render-stage";
      const video = image.media === 'video';
      $("#render-stage").innerHTML = `${video ? `<video id="full-card" src="${esc(image.url)}" ${matchMedia("(prefers-reduced-motion: reduce)").matches ? "" : "autoplay"} muted loop controls playsinline aria-label="${esc(c.name)}动态卡面"></video>` : `<img id="full-card" src="${esc(image.url)}" alt="${esc(c.name)}完整卡面">`}<p class="note">${esc(image.source)}</p><button id="export-render" class="subtle">↓ 导出完整卡面 ${video?'WebM':'PNG'}</button>`;
      if (!video) $("#full-card").onclick = () => viewImage(image, c.name + " · 完整卡面");
      $("#export-render").onclick = guarded(() => exportFiles({image_path: image.path, context_cardid: c.id, label: "完整卡面"}));
    } catch (e) {
      if (generation !== state.detailGeneration) return;
      $("#render-stage").innerHTML = `<p>${esc(e.message)}</p><button id="retry-render" class="subtle">重新加载</button>`;
      $("#retry-render").onclick = guarded(() => cardTab("render"));
    }
  } else if (tab === "voices") {
    state.voiceLoading = generation;
    state.voiceItems = [];
    state.voicePage = 1;
    state.voicePageSize = state.status.settings.voice_page_size || 24;
    state.voiceErrors = [];
    state.transcriptNote = "";
    state.transcriptPending = null;
    state.transcriptSources = [];
    state.otherVoices = [];
    // 主语言先完成并显示，再串行补充已安装语言。
    body.innerHTML = `<div class="voice-toolbar"><label>资源语言<select id="voice-locale" aria-label="语音语言">${state.status.locales.map(l => `<option value="${l.code}" ${l.code === state.voiceLocale ? "selected" : ""}>${esc(l.name)}${l.audioInstalled ? "" : " · 未安装"}</option>`).join("")}</select></label><label class="other-language-toggle"><input id="other-voice-locales" type="checkbox" ${state.status.settings.show_other_voice_locales ? "checked" : ""}>显示其他已安装语言</label><label class="pair-label"><input id="paired-audio" type="checkbox" ${state.pairedAudio ? "checked" : ""}>配套音效 / 音乐</label><label class="pair-label" title="已知时间按时播放；未知时间随语音叠加播放；下次播放生效"><input id="general-audio" type="checkbox" ${state.generalAudio ? "checked" : ""}>通用音效</label></div><div id="voice-warning" class="warning" hidden></div><div class="detail-tabs voice-tabs"><button data-voice-kind="voice">角色语音</button><button data-voice-kind="sound">音效与音乐</button></div><input id="voice-search" type="search" placeholder="筛选台词、事件或音频名…" aria-label="筛选语音"><div id="voice-list" class="empty"><span class="spinner"></span>正在解析声音引用…</div><p id="other-voice-status" class="note" role="status"></p>`;
    SelectUI.refresh();
    $('.other-language-toggle').insertAdjacentHTML('afterend', `<button id="toggle-other-voices" class="subtle" ${state.status.settings.show_other_voice_locales ? '' : 'hidden'} aria-expanded="${state.otherVoicesExpanded}">${state.otherVoicesExpanded ? '折叠其他语言' : '展开其他语言'}</button>`);
    $('#toggle-other-voices').onclick = () => {
      state.otherVoicesExpanded = !state.otherVoicesExpanded;
      const button = $('#toggle-other-voices');
      button.textContent = state.otherVoicesExpanded ? '折叠其他语言' : '展开其他语言';
      button.setAttribute('aria-expanded', String(state.otherVoicesExpanded));
      // 折叠只改变可见性，不重建主行或重读音频；展开时补上已加载的对应条目。
      renderOtherVoiceLists();
    };
    $("#voice-search").oninput = () => { state.voicePage = 1; renderVoiceList(); queueVisibleSpeech(); };
    $("#voice-locale").onchange = guarded(async () => {
      const locale = $("#voice-locale").value;
      const linked = state.status.settings.sync_voice_locale !== false;
      try {
        state.status.settings = await api('save_settings', {voice_locale: locale, ...(linked ? {locale} : {})});
      } catch (error) { $('#voice-locale').value = state.voiceLocale; SelectUI.refresh(); throw error; }
      state.voiceLocale = locale;
      if (linked) {
        state.locale = locale; $('#locale').value = locale;
        rememberView(); languageWarning();
        // 列表在详情后方更新，保留正在看的语音页。
        refresh();
      }
      stopPlayback();
      await cardTab("voices");
    });
    $('#other-voice-locales').onchange = guarded(async e => {
      const checkbox = e.target, enabled = checkbox.checked;
      checkbox.disabled = true;
      try {
        state.status.settings = await api('save_settings', {show_other_voice_locales: enabled});
        // 重开语音页使旧任务失效；主语言命中后端缓存，不重复解析资源图。
        await cardTab('voices');
      } catch (error) { checkbox.checked = !!state.status.settings.show_other_voice_locales; throw error; }
      finally { checkbox.disabled = false; }
    });
    $(".voice-toolbar").insertAdjacentHTML("beforeend", `<button id="export-voices" class="subtle" title="导出当前资源语言的全部角色语音" disabled>↓ 导出全部语音</button><details id="voice-options" open><summary>试听与导出选项</summary><div><label class="pair-label" title="合并为一个 WAV，保留配音尾声（最长 15 秒）"><input id="mix-voice-export" type="checkbox" ${state.mixVoiceExport ? "checked" : ""}>导出时合并音效</label></div></details>`);
    document.querySelectorAll('.voice-toolbar > .pair-label').forEach(label => $("#voice-options > div").prepend(label));
    $("#export-voices").onclick = guarded(() => exportFiles({assetids: [...new Set(state.voiceItems.filter(x => x.kind === "voice").map(x => x.id))], context_cardid: c.id, locale: state.voiceLocale, ...voiceExportOptions()}));
    for (const [selector, key, setting] of [["#mix-voice-export", "mixVoiceExport", "mix_voice_export"], ["#paired-audio", "pairedAudio", "paired_audio"], ["#general-audio", "generalAudio", "general_audio"]]) {
      $(selector).onchange = guarded(async () => {
        const checkbox = $(selector), enabled = checkbox.checked;
        const previous = state[key];
        // 勾选立即影响下一次试听，磁盘保存不应成为播放开关的生效延迟。
        // 否则“勾选后马上播放”会仍然使用旧选项，表现为配套音效丢失。
        state[key] = enabled;
        checkbox.disabled = true;
        // 使在途的配音准备失效，避免取消勾选后旧请求仍启动声音。
        ++playGeneration;
        state.preparingAudio = null;
        syncPlaybackButton();
        try {
          await api("save_settings", {[setting]: enabled});
          state.status.settings[setting] = enabled;
          renderVoiceList();
        } catch (error) {
          state[key] = previous;
          ++playGeneration;
          state.preparingAudio = null;
          syncPlaybackButton();
          throw error;
        } finally {
          // 保存失败时恢复真实状态，不能显示已记忆但实际未保存的勾选。
          checkbox.checked = !!state[key];
          checkbox.disabled = false;
        }
      });
    }
    document.querySelectorAll("[data-voice-kind]").forEach(b => b.onclick = () => {
      state.voiceKind = b.dataset.voiceKind;
      state.voicePage = 1;
      renderVoiceList();
      queueVisibleSpeech();
    });
    const installed = state.status.locales.find(l => l.code === state.voiceLocale)?.audioInstalled;
    $("#voice-warning").hidden = !!installed;
    $("#voice-warning").textContent = "当前客户端未安装此语言的语音资源。可在战网客户端安装相应语言后重新连接；通用音效不代表该语言已安装。";
    const data = await api("card_audio", { cardid: c.id, locale: state.voiceLocale });
    if (generation !== state.detailGeneration) return;
    state.voiceLoading = 0;
    state.voiceItems = data.items;
    $("#export-voices").disabled = !data.items.some(x => x.kind === "voice");
    state.voiceErrors = data.errors;
    renderVoiceList();
    supplementTranscripts(c, generation);
    loadOtherVoices(c, generation);
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
  } catch (e) {
    if (generation !== state.detailGeneration || !body.isConnected) return;
    body.innerHTML = `<p class="warning">${esc(e.message)}</p><button class="subtle" id="retry-tab">重试读取</button>`;
    $('#retry-tab').onclick = () => cardTab(tab);
  }
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
function voiceRows(items, locale = state.voiceLocale, primary = true) {
  return (
    items
      .map(
        (a) =>
          `<div class="voice-row" data-voice-row="${esc(a.id)}" data-voice-locale="${esc(locale)}"><div class="voice-top"><b>${esc((a.trigger_card || a.adventure) ? a.event : eventName(a.name + " " + (a.event || "")))}</b><div><button data-play="${esc(a.id)}" aria-label="试听">▷</button><button data-replay="${esc(a.id)}" aria-label="从头重播">↻</button><button data-export="${esc(a.id)}" aria-label="导出 WAV">↓</button></div></div>${a.condition ? `<p class="voice-condition">${esc(a.condition)}</p>` : ""}${a.condition_targets?.length ? `<details class="voice-targets"><summary>查看适用对象 · ${a.condition_targets.length}</summary><div class="condition-targets">${a.condition_targets.map(t=>`<button class="subtle" data-trigger-card="${esc(t.id)}">${esc(t.name)} ↗</button>`).join("")}</div></details>` : ""}${a.text ? `<p class="transcript">${esc(speechText(a.text))}</p>${a.source ? `<a class="transcript-source" href="${esc(a.source)}">${esc(a.source_name)} · ${a.match_method === "audio_key" ? "按音频键精确匹配" : "按唯一事件匹配"}${a.stale ? " · 离线缓存" : ""} ↗</a>` : '<small>客户端字幕</small>'}` : a.kind === "voice" ? `${a.speech_text ? `<p class="transcript">${esc(a.speech_text)}</p><small class="speech-label">语音识别 · 不保证准确性${a.speech_cached ? " · 缓存" : ""}</small>` : `<p class="transcript-missing">${esc(a.speech_error || (a.speech_done ? "未识别出文字" : "暂无台词"))}</p>`}${primary && state.status.settings.speech_recognition !== false && ["zhcn", "enus"].includes(locale) ? `<button class="subtle speech-retry" data-speech="${esc(a.id)}" ${state.speechRun || state.transcriptPending === state.detailGeneration ? "disabled" : ""}>${a.speech_done || a.speech_error ? "重试语音识别" : "语音识别"}</button>` : ""}` : ""}<details class="voice-technical"><summary>资源信息与触发时间</summary><p>${esc(a.timing?.label || "触发时间依赖游戏状态，尚未确定")}</p><small>${esc(a.name)}<br>${esc(a.event || "")} · ${esc(a.locale)}</small></details>${a.trigger_card ? `<button class="subtle" data-trigger-card="${esc(a.trigger_card)}">查看触发卡牌 ↗</button>` : ""}${primary && a.locale !== "global" ? `<div class="voice-translations" data-voice-match="${esc(VoiceLocales.key(a))}"></div>` : ""}</div>`,
      )
      .join("") || '<div class="empty">此引用下未发现可读取的音频。</div>'
  );
}
function bindVoices(node) {
  node.querySelectorAll("[data-replay]").forEach(b => b.onclick = guarded(() => playAsset(b.dataset.replay, true, b.closest("[data-voice-locale]")?.dataset.voiceLocale)));
  node.querySelectorAll("[data-trigger-card]").forEach(b => b.onclick = guarded(() => showCard(b.dataset.triggerCard, "related")));
  node.querySelectorAll("[data-speech]").forEach(button => {
    button.onclick = guarded(() => recognizeMissing(state.detailGeneration, button.dataset.speech));
  });
  node
    .querySelectorAll("[data-play]")
    .forEach((b) => (b.onclick = guarded(() => playAsset(b.dataset.play, false, b.closest("[data-voice-locale]")?.dataset.voiceLocale))));
  node
    .querySelectorAll("[data-export]")
    .forEach(
      (b) =>
        (b.onclick = guarded(() =>
          exportFiles({ assetids: [b.dataset.export], context_cardid: state.card?.id || "", locale: b.closest("[data-voice-locale]")?.dataset.voiceLocale || state.voiceLocale, ...voiceExportOptions() }),
        )),
    );
}
// 额外语言只读取元数据和客户端字幕，不批量解码/识别/联网补词。
// 同时最多一项请求；每次返回检查页面代次，离开详情后不再排队或更新 DOM。
async function loadOtherVoices(card, generation) {
  if (!state.status.settings.show_other_voice_locales) return;
  const active = () => generation === state.detailGeneration && state.tab === 'voices' &&
    state.status.settings.show_other_voice_locales;
  const groups = state.status.locales.filter(l => l.audioInstalled && l.code !== state.voiceLocale)
    .map(l => ({locale:l.code, name:l.name, items:[], errors:[], index:new Map(), status:'waiting'}));
  state.otherVoices = groups;
  renderOtherVoiceLists();
  for (const group of groups) {
    // 给主语言播放/解码和用户输入一次入队机会，后台任务不能抢在它们前面。
    while (active()) {
      await new Promise(resolve => setTimeout(resolve, 180));
      if (!active()) return;
      if (state.preparingAudio) continue;
      group.status = 'loading'; renderOtherVoiceLists(group.locale);
      try {
        const data = await api('card_audio', {cardid:card.id, locale:group.locale}, true);
        if (!active()) return;
        group.items = data.items; group.index = VoiceLocales.index(data.items); group.errors = data.errors; group.status = 'ready';
        break;
      } catch (error) {
        if (!active()) return;
        // 主语言试听会中断资源图遍历；待交互完成后继续同一种语言。
        if (error.cancelled) { group.status = 'waiting'; continue; }
        group.status = 'error'; group.errors = [error.message]; break;
      }
    }
    if (!active()) return;
    renderOtherVoiceLists(group.locale);
  }
}

function renderOtherVoiceLists(onlyLocale = null, root = $('#voice-list')) {
  if (!root) return;
  const enabled = !!state.status.settings.show_other_voice_locales;
  const groups = state.otherVoices || [];
  const note = $('#other-voice-status');
  if (note) {
    note.hidden = !enabled;
    const pending = groups.filter(group => ['waiting', 'loading'].includes(group.status)).length;
    note.textContent = !groups.length ? '未发现其他已安装的语音语言。' : pending ?
      `其他语言逐种加载中 · 剩余 ${pending} 种；主语言可立即试听。` : '其他语言已按对应语音显示；通用音效不重复列出。';
  }
  // 只访问当前页的槽位。每种语言预建查找表，更新成本随当前页行数线性增长。
  // 不改主语言 DOM；收起时不生成其他语言按钮，翻页/切组也沿用统一展开状态。
  root.querySelectorAll('[data-voice-match]').forEach(slot => {
    slot.hidden = !enabled || !state.otherVoicesExpanded;
    if (slot.hidden) return;
    for (const group of groups) {
      if (onlyLocale && group.locale !== onlyLocale) continue;
      let block = slot.querySelector(`[data-other-language="${group.locale}"]`);
      if (!block) {
        block = document.createElement('div'); block.className = 'voice-translation';
        block.dataset.otherLanguage = group.locale; block.dataset.voiceLocale = group.locale;
        slot.append(block);
      }
      const item = group.index.get(slot.dataset.voiceMatch);
      const label = `<b>${esc(group.name)}</b>`;
      if (group.status !== 'ready' || !item) {
        const message = group.status === 'error' ? '读取失败，可重试' :
          group.status === 'ready' ? '未找到可确认对应的语音' : '等待加载对应语音…';
        block.innerHTML = `<div class="voice-top">${label}</div><p class="transcript-missing">${message}</p>` +
          (group.status === 'error' ? errorBox(group.errors) + '<button class="subtle" data-retry-language>重试读取</button>' : '');
        const retry = block.querySelector('[data-retry-language]');
        if (retry) retry.onclick = guarded(() => cardTab('voices'));
        continue;
      }
      block.innerHTML = `<div class="voice-top">${label}<div><button data-play="${esc(item.id)}" aria-label="试听 ${esc(group.name)}">▷</button><button data-replay="${esc(item.id)}" aria-label="重播 ${esc(group.name)}">↻</button><button data-export="${esc(item.id)}" aria-label="导出 ${esc(group.name)} WAV">↓</button></div></div>` +
        (item.text ? `<p class="transcript">${esc(speechText(item.text))}</p><small>客户端字幕</small>` : '<p class="transcript-missing">暂无台词</p>');
      bindVoices(block); attachAudioExports(block);
    }
  });
  syncPlaybackButton();
}

function errorBox(errors) {
  return errors?.length
    ? `<details class="warning"><summary>${errors.length} 项资源未完整解析，查看原因</summary>${errors.map((e) => `<div>${esc(e)}</div>`).join("")}</details>`
    : "";
}

async function playAsset(assetid, replay = false, resourceLocale = null) {
  // 同一资源再次点击直接暂停/继续，避免重新解码和从头播放。
  if (!replay && !state.preparingAudio && currentAudio?.assetid === assetid && (!resourceLocale || currentAudio.locale === resourceLocale) && !audio.ended) {
    $("#play-pause").click();
    return;
  }
  if (fx) fx.stop();
  const generation = ++playGeneration;
  state.preparingAudio = assetid;
  $('#player').hidden = false;
  $('#track-name').textContent = '正在准备试听…';
  $('#track-info').textContent = '读取音频与配套声音';
  syncPlaybackButton();
  try {
  audio.pause();
  // 解码进度由统一状态区显示，避免播放开始后残留七秒的“准备中”提示。
  const contextCardid = state.card?.id || "";
  const voiceLocale = resourceLocale || (contextCardid ? state.voiceLocale : state.locale);
  const items = voiceLocale === state.voiceLocale ? state.voiceItems :
    (state.otherVoices?.find(group => group.locale === voiceLocale)?.items || []);
  const selected = items.find(x => x.id === assetid && x.kind === "voice");
  // 配音只经后端生成一条时间轴 WAV。浏览器只有一个媒体时钟，暂停、
  // 拖动和重播不再依赖多条 Audio 的 play 事件及不精确的 setTimeout。
  const data = selected && (state.pairedAudio || state.generalAudio)
    ? await api('playback_audio', {assetid, cardid:contextCardid, locale:voiceLocale,
        paired_audio:state.pairedAudio, general_audio:state.generalAudio})
    : await decodedAudio(assetid, voiceLocale);
  if (generation !== playGeneration) return;
  currentAudio = { assetid, samples: data.samples, index: 0, context_cardid: contextCardid, locale: voiceLocale };
  playSample(data.samples[0]);
  if (data.timeline?.errors?.length) toast('部分配套未能读取：' + data.timeline.errors.join('；'));
  // 播放与导出使用相同的样本偏移，时间轴包含配套音轨尾声。
  if (data.samples.length > 1)
    toast(
      `此资源含 ${data.samples.length} 个子采样，将依次播放；导出会保留全部。`,
    );
  } catch (e) {
    if (generation === playGeneration) {
      $('#track-name').textContent = '试听未成功';
      $('#track-info').textContent = e.message + ' · 可再次点击重试';
      toast(e.message);
    }
  } finally {
    if (generation === playGeneration) { state.preparingAudio = null; syncPlaybackButton(); }
  }
}
// 复用已解码的 WAV 元数据与在途请求；不缓存失败，重播无需再次排队读取。
const decodedTracks = new Map();
function decodedAudio(assetid, locale) {
  const key = `${state.status.version}:${locale}:${assetid}`;
  if (decodedTracks.has(key)) return decodedTracks.get(key);
  const result = api('audio', {assetid, locale}).then(data => {
    if (!data.samples?.length) throw Error('此音频没有可播放的采样');
    return data;
  }).catch(e=>{ decodedTracks.delete(key); throw e; });
  decodedTracks.set(key, result);
  while (decodedTracks.size > 32) decodedTracks.delete(decodedTracks.keys().next().value);
  return result;
}
function playSample(sample) {
  $("#player").hidden = false;
  $("#track-name").textContent = sample.name;
  $("#track-info").textContent =
    `PCM WAV · ${sample.rate} Hz · ${sample.channels} 声道`;
  audio.src = sample.url;
  audio.play().catch(e => { if (e.name !== "AbortError") toast("无法播放：" + e.message); });
  waveform.setPeaks(sample.peaks);
}
audio.onplay = syncPlaybackButton;
audio.onpause = syncPlaybackButton;
const waveform = new WaveformControl($("#waveform"), audio, $("#track-time"), time);
audio.onended = () => {
  if (currentAudio && currentAudio.index + 1 < currentAudio.samples.length)
    playSample(currentAudio.samples[++currentAudio.index]);
};
audio.onerror = () =>
  toast("播放器无法读取该文件，请查看日志。WAV 仍可导出供其他播放器打开。");

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
    state.audioExports.set(`${params.locale || state.locale}:${params.assetids[0]}`, result.folder);
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
    `<div class="setting"><label>炉石安装目录</label><div class="path-row"><input id="game-path" value="${esc(s.game_path)}" aria-label="炉石目录"><button id="browse-game" class="subtle">浏览…</button><button id="connect-game" class="primary">连接</button></div><p>读取 Data/Win 与 Strings；无需运行游戏，不修改安装文件。</p></div><div class="setting"><label>导出位置</label><div class="path-row"><input id="export-path" value="${esc(s.export_path)}" aria-label="导出目录"><button id="browse-export" class="subtle">浏览…</button><button id="save-export" class="subtle">保存</button></div><p>每次导出新建目录，避免覆盖已有作品。缓存与日志保存在工具的 workspace 文件夹。</p></div><div class="setting"><label>资源索引与诊断</label><p>索引覆盖本地资源包。未安装语言包的声音无法读取；卡牌文本可切换本地 DBF 内的语言。扫描支持断点继续，游戏更新后按文件指纹使用新快照。</p><button id="diagnostics" class="subtle">查看索引诊断</button><div id="diagnostics-result"></div></div><div class="setting"><label>关于砰砰解析台 BOOM LAB</label><p>版本 ${esc(state.status?.app_version || "—")} · 作者 朝禊ASOGI<br>参考 Hermes 的资源清单解析思路，独立实现可视化工作台。游戏美术与声音属于相应权利人，发布代码不包含游戏素材。</p><div class="about-links"><a href="https://github.com/AsaMisogi/Heartstone-Tool-Resource-Lab" class="subtle repository-link" title="在浏览器中打开 GitHub 仓库">GitHub · 源码与反馈 ↗</a><a href="https://space.bilibili.com/315312" class="subtle">B站 · 朝禊ASOGI ↗</a></div><p>原画与声音读取本地资源；完整卡面按需从 HearthstoneJSON 获取并缓存。特效为实验性二维预览，完整 Unity 运行时渲染尚未实现。</p></div>`;
  $('#settings-page').insertAdjacentHTML('afterbegin', `<div class="setting"><h3>语音语言</h3><label><input id="sync-voice-locale" type="checkbox" ${s.sync_voice_locale !== false ? 'checked' : ''}> 筛选器与资源语言同步</label><p>默认同步，修改任意一处会更新另一处。关闭后，筛选语言按页面记忆，资源语言跨卡牌和重启独立记忆。其他已安装语言可在语音页勾选显示，逐种加载并放在对应语音下方，支持统一折叠和展开。</p></div>`);
  $('#sync-voice-locale').onchange = guarded(async e => {
    const checkbox = e.target;
    try {
      state.status.settings = await api('save_settings', {sync_voice_locale: checkbox.checked});
      state.voiceLocale = checkbox.checked ? state.locale : state.status.settings.voice_locale;
    } catch (error) { checkbox.checked = s.sync_voice_locale !== false; throw error; }
  });
  if (state.status?.string_warnings?.length) $('#settings-page').insertAdjacentHTML('afterbegin',
    `<div class="setting">${errorBox(state.status.string_warnings)}<p>以上字幕文件已跳过；图鉴和音频仍可使用。客户端下载完成后可重新连接。</p></div>`);
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
  $("#settings-page").insertAdjacentHTML('afterbegin', `<div class="setting"><h3>软件更新</h3><label><input id="auto-check-updates" type="checkbox" ${s.auto_check_updates !== false ? 'checked' : ''}> 启动时自动检查更新</label><p>查询 GitHub 稳定版 Release，有新版本时提示前往下载。</p><button id="check-updates" class="subtle">检查更新</button><p id="update-status" role="status"></p></div>`);
  $("#auto-check-updates").onchange = guarded(async e => { state.status.settings = await api('save_settings', {auto_check_updates:e.target.checked}); });
  $("#check-updates").onclick = () => checkUpdates(true);
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting"><label>滚动方式 <select id="scroll-mode"><option value="smooth">${window.NativeHost ? "浏览器平滑" : "轻量平滑"}</option><option value="instant">即时滚动</option></select></label><p>${window.NativeHost ? "Windows 原生浏览器：滚轮、拖拽和显示同步由浏览器处理，支持高刷新率屏幕。" : "Qt 兼容引擎：未使用 Windows 原生浏览器显示链路。"} 幅度跟随 Windows 鼠标设置；即时滚动直接到位，系统减少动画优先。</p></div>`);
  $('#scroll-mode').value = s.scroll_mode || 'smooth';
  $('#scroll-mode').onchange = guarded(async () => {
    const mode = $('#scroll-mode').value, previous = state.status.settings.scroll_mode;
    WheelScroll.setMode(mode);
    try { await api('save_settings', {scroll_mode:mode}); state.status.settings.scroll_mode = mode; }
    catch(error) { WheelScroll.setMode(previous); $('#scroll-mode').value = previous || 'smooth'; throw error; }
  });
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting"><label><input id="infinite-scroll" type="checkbox" ${state.infiniteScroll ? "checked" : ""}> 滚动加载更多</label><p>默认关闭。开启后，滚动至列表底部自动追加内容，隐藏页码跳转；每次加载数量由列表下方设置。</p></div>`);
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting"><label><input id="online-transcripts" type="checkbox" ${s.online_transcripts !== false ? "checked" : ""}> 按需补充公开台词</label><p>本地字幕优先；缺失时按下方顺序查询已启用来源，失败或未命中则继续补齐，缓存结果并显示来源。支持按音频键精确匹配简中字幕；Wiki 仅补充可唯一匹配的基础事件。关闭后不发起查询。</p></div>`);
  // 独立保存来源列表，开关、顺序与新增操作共享后端校验和持久化。
  $("#settings-page").insertAdjacentHTML("afterbegin", `<div class="setting"><label><input id="speech-recognition" type="checkbox" ${s.speech_recognition !== false ? "checked" : ""}> 缺失台词自动语音识别</label><p>现有台词来源未命中时，识别简中 / 英语短语音。默认使用随包内置模型，无需下载、不上传音频、不使用显卡。逐条处理，闲置 15 秒释放模型；关闭后立即停止。其他语言暂不识别。</p><p>识别文字会标明“不保证准确性”，可逐条重试。笑声、音效及特殊角色声线可能无法正确识别。</p></div>`);

  // 配置与检查分开：输入未保存时，检查按钮明确要求先保存，避免检查旧配置。
  const sc = s.speech_config || {provider: "bundled"};
  $("#speech-recognition").closest(".setting").insertAdjacentHTML("beforeend", `
    <div class="speech-config">
      <label for="speech-provider">识别方式</label>
      <select id="speech-provider">
        <option value="bundled">内置离线模型（推荐）</option>
        <option value="local">自定义本地 Vosk 模型</option>
        <option value="api">在线语音识别 API</option>
      </select>
      <div id="speech-local-fields">
        <p>选择解压后直接包含 am 和 conf 的 Vosk 模型目录；留空的语言继续使用内置模型。Whisper 等其他模型请通过兼容 API 使用。</p>
        ${["zhcn", "enus"].map((locale, index) => `<label for="speech-${locale}">${index ? "英语" : "简体中文"}模型</label><div class="path-row"><input id="speech-${locale}" value="${esc(sc[locale + "_path"] || "")}" placeholder="留空使用内置模型"><button type="button" data-model-browse="${locale}" class="subtle">浏览…</button></div>`).join("")}
      </div>
      <div id="speech-api-fields">
        <p class="note">在线模式会将待识别音频发送给你配置的服务商，可能产生费用。支持兼容 multipart 音频转写接口；不会在离线失败时自动切换在线。</p>
        <label for="speech-url">完整转写地址</label>
        <input id="speech-url" value="${esc(sc.api_url || "")}" placeholder="https://服务商域名/v1/audio/transcriptions" spellcheck="false">
        <label for="speech-model">API 模型名称</label>
        <input id="speech-model" value="${esc(sc.api_model || "")}" placeholder="填写服务商提供的语音模型 ID" spellcheck="false">
        <label for="speech-key">API 密钥</label>
        <div class="path-row"><input id="speech-key" type="password" autocomplete="off" value="${esc(s.speech_api_key || "")}" placeholder="填写服务商提供的 API 密钥"><button id="show-speech-key" type="button" class="subtle">显示</button></div>
        <p>密钥随本地设置保存，迁移 workspace 后可继续使用；留空并保存即可清除。也可使用 PENGPENG_SPEECH_API_KEY 环境变量。</p>
        <p>点击状态检查会向该地址发送一秒静音以验证连接、认证与模型，也可能计费。</p>
      </div>
      <div class="path-row"><button id="save-speech-config" class="primary">保存语音配置</button><button id="check-speech-status" class="subtle">语音模型状态检查</button></div>
      <p id="speech-status-result" role="status" aria-live="polite">保存配置后可检查模型是否能够实际加载。</p>
    </div>`);
  $("#show-speech-key").onclick = () => {
    const input = $("#speech-key");
    input.type = input.type === "password" ? "text" : "password";
    $("#show-speech-key").textContent = input.type === "password" ? "显示" : "隐藏";
  };
  $("#speech-provider").value = sc.provider;
  const showSpeechFields = () => {
    $("#speech-local-fields").hidden = $("#speech-provider").value !== "local";
    $("#speech-api-fields").hidden = $("#speech-provider").value !== "api";
  };
  showSpeechFields();
  let speechDirty = false;
  document.querySelectorAll(".speech-config input, .speech-config select").forEach(input => {
    input.oninput = () => { speechDirty = true; showSpeechFields(); };
  });
  document.querySelectorAll("[data-model-browse]").forEach(button => {
    button.onclick = () => host.chooseDirectory("speech", path => {
      if (path) { $("#speech-" + button.dataset.modelBrowse).value = path; speechDirty = true; }
    });
  });
  $("#save-speech-config").onclick = guarded(async () => {
    const button = $("#save-speech-config");
    button.disabled = true;
    try {
      const config = {provider: $("#speech-provider").value, zhcn_path: $("#speech-zhcn").value,
        enus_path: $("#speech-enus").value, api_url: $("#speech-url").value, api_model: $("#speech-model").value};
      const params = {speech_config: config, speech_api_key: $("#speech-key").value};
      cancelSpeech();
      state.status.settings = await api("save_settings", params);
      renderSettings();
      $("#speech-status-result").textContent = "配置已保存。点击状态检查验证当前模型。";
    } finally { button.disabled = false; }
  });
  $("#check-speech-status").onclick = async () => {
    const button = $("#check-speech-status"), result = $("#speech-status-result");
    if (speechDirty) { result.textContent = "配置尚未保存，请先保存后再检查。"; return; }
    button.disabled = true;
    result.textContent = "正在检查，请稍候…";
    try {
      const checked = await api("speech_status");
      result.textContent = (checked.ok ? "✓ " : "检查未通过：\n") + checked.message;
    } catch (error) { result.textContent = "检查失败：" + error.message; }
    finally { button.disabled = false; }
  };
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
        ${["ifindhs","hsdata","wikigg","huiji","baidu"].includes(source.id) ? "" : `<button class="subtle" data-source-edit="${index}">编辑</button><button class="subtle" data-source-delete="${index}">删除</button>`}
      </div>`).join("")}</div>
    <p>ifindhs 与 hsdata 按完整音频键匹配；其他网站按唯一基础事件补充。网站要求访问验证时，可在应用内浏览后读取台词。</p>
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
  for (const view of ["cards", "heroes", "battlegrounds"]) {
    const order = saved?.[view];
    if (["default", "name", "id", "release"].includes(order?.sort) && typeof order.descending === "boolean")
      state.catalogOrder[view] = {sort: order.sort, descending: order.descending};
  }
} catch (_) { /* 旧值损坏时只回退排序，不影响收藏和其他工作区设置。 */ }
renderOrder();
$("#locale").onchange = guarded(async () => {
  state.locale = $("#locale").value;
  if (state.status.settings.sync_voice_locale !== false) state.voiceLocale = state.locale;
  state.offset = 0;
  state.selected.clear();
  languageWarning();
  state.status.settings = await api("save_settings", { locale: state.locale });
  const language = state.status.locales.find((l) => l.code === state.locale);
  if (!language?.audioInstalled)
    toast("本地未安装此语言语音。文本仍可读取；不会用其他语言冒充；通用音效仍可使用。");
  await refresh();
  if (state.card) await showCard(state.card.id);
});
$("#category").onchange = guarded(() => {
  state.category = $("#category").value;
  state.subgroup = '';
  state.offset = 0;
  return refresh();
});
$('#audio-subgroup').onchange = guarded(() => {
  state.subgroup = $('#audio-subgroup').value;
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
async function startScan(scope = 'all') {
  await api("scan", {scope});
  state.scanning = true;
  $("#scan-button").disabled = true;
  $("#jobbar").hidden = false;
}
$("#scan-button").onclick = guarded(() => startScan());
$("#cancel-scan").onclick = guarded(() => api("cancel_scan"));
async function dismissIndexGuide(start) {
  if (start) await startScan();
  const settings = await api("save_settings", {index_guide_seen: true});
  state.status.settings = settings;
  $("#index-guide").close();
}
$("#index-later").onclick = guarded(() => dismissIndexGuide(false));
$("#index-start").onclick = guarded(() => dismissIndexGuide(true));
$("#index-guide").addEventListener("cancel", e => {
  e.preventDefault();
  guarded(() => dismissIndexGuide(false))();
});
const detailResize = DetailResize.install($('#detail'), $('#detail-resize'), width => {
  api('save_settings', {detail_width:width}).then(settings => {
    if (state.status) state.status.settings.detail_width = settings.detail_width;
  }).catch(e => toast('详情宽度保存失败：' + e.message));
}, logicalViewport);
$("#close-detail").onclick = closeDetail;
$("#logs-toggle").onclick = () => {
  $("#log-panel").hidden = !$("#log-panel").hidden;
  renderLogs();
};
$("#close-logs").onclick = () => ($("#log-panel").hidden = true);
$("#open-logs").onclick = () => host.openLogs();
$("#play-pause").onclick = () => {
  if (audio.ended && currentAudio) guarded(() => playAsset(currentAudio.assetid, true, currentAudio.locale))();
  else if (audio.paused) audio.play().catch(e => toast(e.message));
  else audio.pause();
};
function syncPlaybackButton() {
  const playing = !audio.paused;
  document.querySelectorAll('[data-play]').forEach(button => {
    const locale = button.closest("[data-voice-locale]")?.dataset.voiceLocale;
    const active = currentAudio?.assetid === button.dataset.play && (!locale || currentAudio.locale === locale) && playing;
    const preparing = state.preparingAudio === button.dataset.play;
    button.textContent = preparing ? '…' : active ? 'Ⅱ' : '▷';
    button.setAttribute('aria-label', preparing ? '正在准备试听' : active ? '暂停' : '试听');
    button.setAttribute('aria-pressed', String(active));
  });
  $("#play-pause").textContent = !audio.paused ? "Ⅱ" : "▶";
}
$("#volume").oninput = () => { audio.volume = Number($("#volume").value); };
$("#stop-player").onclick = stopPlayback;
$("#replay-track").onclick = guarded(() => currentAudio && playAsset(currentAudio.assetid, true, currentAudio.locale));
function stopPlayback() {
  playGeneration++;
  state.preparingAudio = null;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  currentAudio = null;
  waveform.reset();
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
    if (document.querySelector('dialog[open]')) return;
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
let playGeneration = 0;
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
function renderVoiceGroups(counts) {
  let root = $("#voice-groups");
  if (!root) {
    root = document.createElement("div"); root.id = "voice-groups";
    root.setAttribute("role", "group"); root.setAttribute("aria-label", "角色语音分类");
    // 分组按钮只创建一次；后台字幕到达后更新数量，不替换用户的焦点节点。
    root.innerHTML = Object.entries(VoiceGroups.labels).map(([key, label]) =>
      `<button data-voice-group="${key}" aria-pressed="false">${label}<span></span></button>`).join("");
    $("#voice-search").before(root);
    root.addEventListener("click", e => {
      const button = e.target.closest("[data-voice-group]");
      if (!button) return;
      state.voiceGroup = button.dataset.voiceGroup; state.voicePage = 1;
      renderVoiceList(); queueVisibleSpeech();
    });
  }
  root.hidden = state.voiceKind !== "voice";
  root.querySelectorAll("button").forEach(button => {
    const key = button.dataset.voiceGroup;
    button.setAttribute("aria-pressed", String(key === state.voiceGroup));
    button.querySelector("span").textContent = counts[key];
    // 保留空分组与选中状态，搜索无匹配时用户仍能清楚看见当前筛选条件。
    button.title = `${VoiceGroups.labels[key]} · 当前搜索匹配 ${counts[key]} 条`;
  });
}
// 后台字幕/识别可以改变行高。滚动期间暂存界面更新，在最后一次滚动
// 结束 160ms 后统一提交，避免重新布局打断手势；用户主动翻页/搜索仍即时响应。
let lastDetailScroll = -Infinity, voiceRefresh = null, voiceRefreshTimer = 0;
$('#detail').addEventListener('scroll', () => { lastDetailScroll = performance.now(); }, {passive:true});
function queueVoiceRefresh(id = null) {
  if (!voiceRefresh || voiceRefresh.generation !== state.detailGeneration)
    voiceRefresh = {generation:state.detailGeneration, all:false, ids:new Set()};
  if (id === null) voiceRefresh.all = true;
  else voiceRefresh.ids.add(id);
  clearTimeout(voiceRefreshTimer);
  const flush = () => {
    if (!voiceRefresh || voiceRefresh.generation !== state.detailGeneration) { voiceRefresh = null; return; }
    const remaining = 160 - (performance.now() - lastDetailScroll);
    if (remaining > 0) { voiceRefreshTimer = setTimeout(flush, remaining); return; }
    const pending = voiceRefresh; voiceRefresh = null;
    if (pending.all) renderVoiceList();
    else pending.ids.forEach(updateRecognizedRows);
  };
  voiceRefreshTimer = setTimeout(flush, Math.max(0, 160 - (performance.now() - lastDetailScroll)));
}
function renderVoiceList() {
  if (!$("#voice-list")) return;
  const scroll = $("#detail").scrollTop;
  const query = ($("#voice-search")?.value || "").trim().toLowerCase();
  const {rows: unique, counts} = VoiceGroups.select(state.voiceItems, state.voiceKind, state.voiceGroup, query);
  renderVoiceGroups(counts);
  document.querySelectorAll("[data-voice-kind]").forEach(b => {
    b.classList.toggle("active", b.dataset.voiceKind === state.voiceKind);
    // 页签和分类使用同一事件口径：同一音频的不同触发条件分别计数。
    const count = VoiceGroups.select(state.voiceItems, b.dataset.voiceKind, 'all', '').counts.all;
    b.textContent = (b.dataset.voiceKind === "voice" ? "角色语音" : "音效与音乐") + ` (${count})`;
  });
  if (state.voiceLoading === state.detailGeneration) {
    $('#voice-list').innerHTML = '<div class="empty"><span class="spinner"></span>正在读取声音，可继续切换分类…</div>';
    return;
  }
  $("#voice-list").className = "";
  // 台词优先露出，避免首屏被无字幕的攻击喘息、死亡和播报占满。
  // 保持资源顺序稳定；后台补齐台词时不把正在看的语音挪到其他页。
  const transcribed = unique.filter(x => x.text).length;
  const recognized = unique.filter(x => !x.text && x.speech_text).length;
  const coverage = state.voiceKind === "voice" ? `<p class="transcript-summary">${transcribed} / ${unique.length} 条语音已有来源台词${recognized ? ` · ${recognized} 条语音识别（仅供参考）` : ""} · 台词在后台补齐</p>` : "";
  const size = state.voicePageSize || 24, pages = Math.max(1, Math.ceil(unique.length / size));
  state.voicePage = Math.min(pages, Math.max(1, state.voicePage || 1));
  const offset = (state.voicePage - 1) * size;
  const pageItems = unique.slice(offset, offset + size);
  state.voiceVisibleIds = new Set(pageItems.map(item => item.id));
  const pager = `<nav class="voice-pagination" aria-label="语音分页"><button id="voice-prev" ${state.voicePage <= 1 ? 'disabled' : ''}>上一页</button><label>第 <input id="voice-page" aria-label="语音页码" type="number" min="1" max="${pages}" value="${state.voicePage}"> / ${pages} 页</label><button id="voice-next" ${state.voicePage >= pages ? 'disabled' : ''}>下一页</button><label>每页 <select id="voice-page-size" aria-label="每页语音数量">${[12,24,48,96].map(n => `<option ${n===size?'selected':''}>${n}</option>`).join('')}</select> 条</label><small>共 ${unique.length} 条</small></nav>`;
  $("#voice-list").innerHTML = (state.voiceItems.length ? `<p class="voice-ready">✓ 音频已就绪，可立即试听与导出${state.transcriptPending === state.detailGeneration ? ' · 台词正在后台补充' : ''}</p>` : '') + coverage +
    `<p id="speech-inline-status" class="note" role="status"></p>` +
    (state.transcriptNote ? `<details class="transcript-diagnostics"><summary>台词来源状态</summary><p class="note">${esc(state.transcriptNote)}</p></details>` : '') +
    voiceRows(pageItems) + (!unique.length ? '<p class="empty">没有匹配的语音，请调整筛选。</p>' : '') + pager + errorBox(state.voiceErrors);
  const go = page => { state.voicePage = Math.min(pages, Math.max(1, Number(page) || 1)); renderVoiceList(); $("#voice-search").scrollIntoView({block:'start'}); queueVisibleSpeech(); };
  $("#voice-prev").onclick = () => go(state.voicePage - 1);
  $("#voice-next").onclick = () => go(state.voicePage + 1);
  $("#voice-page").onchange = e => go(e.target.value);
  $("#voice-page-size").onchange = guarded(async e => {
    state.voicePageSize = Number(e.target.value); state.voicePage = 1;
    state.status.settings.voice_page_size = state.voicePageSize;
    renderVoiceList();
    await api('save_settings', {voice_page_size: state.voicePageSize});
    queueVisibleSpeech();
  });
  renderOtherVoiceLists();
  bindVoices($("#voice-list"));
  syncPlaybackButton();
  if (state.voiceKind === "voice") {
    $("#speech-inline-status").textContent = state.speechNote || "";
  }
  // 重试只补缺失台词；请求期间禁用，切卡/切语言后旧结果由 generation 丢弃。
  if (state.voiceKind === "voice" && state.voiceLocale === "zhcn" &&
      state.status.settings.online_transcripts !== false && state.card?.record.dbfid &&
      state.voiceItems.some(item => item.kind === "voice" && !item.text)) {
    const retry = document.createElement("button");
    retry.id = "retry-transcripts";
    retry.className = "subtle";
    retry.disabled = state.transcriptPending === state.detailGeneration;
    retry.textContent = retry.disabled ? "后台补充台词中 · 不影响试听" : "重试获取缺失台词";
    retry.onclick = guarded(() => supplementTranscripts(state.card, state.detailGeneration, true));
    $("#voice-list").prepend(retry);
  }

  if (state.voiceKind === "voice" && state.transcriptSources?.length) {
    const controls = document.createElement("div");
    controls.className = "transcript-actions";
    controls.innerHTML = state.transcriptSources.map(id => `<button class="subtle" data-transcript-source="${esc(id)}">${String(id).startsWith("ifindhs:") ? "ifindhs · 浏览读取" : "灰机 · 浏览读取"}</button>`).join("");
    $("#voice-list").prepend(controls);
    controls.querySelectorAll("button").forEach(button => button.onclick = guarded(async () => {
      const card = state.card, generation = state.detailGeneration;
      const source = button.dataset.transcriptSource;
      const parts = source.split(":");
      const result = await api("transcript_browser", parts[0] === "ifindhs"
        ? {dbfid: card.record.dbfid, source: "ifindhs", cardid: parts[1], name: parts.slice(2).join(":")}
        : {dbfid: Number(source)});
      if (result.saved && generation === state.detailGeneration) await supplementTranscripts(card, generation);
    }));
  }
  attachAudioExports($("#voice-list"));
  $("#detail").scrollTop = scroll;
}
// 只改变布局样式，保留卡片节点、已解码图片和进行中的缩略图队列。
// input 负责即时预览，change 才保存，拖拽过程中不反复写入工作区设置。
function applyCatalogDisplay() {
  const active = ["cards", "heroes", "battlegrounds"].includes(state.view);
  const {mode, size} = state.catalogDisplay;
  $("#results").classList.toggle("catalog-list", active && mode === "list");
  $("#results").style.setProperty("--tile-width", `${size}px`);
  $("#results").style.setProperty("--tile-space", `${Math.round(7 + (size - 180) / 20)}px`);
}
function renderCatalogDisplay() {
  const node = $("#catalog-display");
  node.hidden = !["cards", "heroes", "battlegrounds"].includes(state.view);
  if (node.hidden) return;
  $("#npc-shortcut").hidden = state.view !== 'heroes';
  $("#npc-shortcut").onclick = guarded(async () => {
    state.filters = {hero_group:'npc'}; state.query = ''; $("#search").value = '';
    state.offset = 0; state.favorites = false; renderFilters(); rememberView(); await refresh();
  });
  const sync = () => {
    const {mode, size} = state.catalogDisplay;
    node.querySelectorAll("[data-layout]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.layout === mode)));
    $("#catalog-size").value = size;
    $("#catalog-size").disabled = mode === "list";
    $("#catalog-size-value").textContent = mode === "list" ? "列表" : `${Math.round(size / 180 * 100)}%`;
    applyCatalogDisplay();
  };
  node.querySelectorAll("[data-layout]").forEach(b => b.onclick = () => {
    state.catalogDisplay.mode = b.dataset.layout; sync(); rememberView();
  });
  $("#catalog-size").oninput = e => { state.catalogDisplay.size = Number(e.target.value); sync(); };
  $("#catalog-size").onchange = rememberView;
  $("#reset-layout").onclick = () => {
    state.catalogDisplay = {mode: "grid", size: 180}; sync(); rememberView();
  };
  sync();
}
function renderFilters() {
  const node = $("#card-filters");
  node.hidden = !["cards", "heroes", "battlegrounds"].includes(state.view);
  if (node.hidden || !state.status?.filters) return;
  const f = state.status.filters;
  const make = (key, label, values = []) => `<label>${label}<select data-filter="${key}" aria-label="${label}"><option value="">全部${label}</option>${values.map(x => `<option data-kind="${esc(x.kind || "大系列")}" value="${esc(x.value)}" ${String(state.filters[key]) === String(x.value) ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>`;
  const cost = Array.from({length: 11}, (_, i) => ({value: i === 10 ? "10+" : String(i), label: i === 10 ? "10 费及以上" : `${i} 费`}));
  node.innerHTML = state.view === "heroes"
    ? make("hero_group", "英雄职业", f.hero_groups) + make("battlegrounds", "酒馆皮肤", [{value: "exclude", label: "不显示酒馆皮肤"}, {value: "only", label: "只显示酒馆皮肤"}])
    : state.view === "battlegrounds" ? make("tier", "酒馆等级", Array.from({length:7},(_,i)=>({value:String(i+1),label:`${i+1} 星`}))) + make("bg_pool", "随从池", [{value:"1",label:"客户端标记可入池"},{value:"0",label:"衍生 / 金色 / 非入池"}])
    : make("set", "系列", f.sets) + make("format", "赛制", f.formats) + make("class", "职业", f.classes) + make("rarity", "稀有度", f.rarities) + make("cost", "法力消耗", cost) + make("type", "类别", f.types) + make("collectible", "收集状态", [{value: "1", label: "可收集"}, {value: "0", label: "衍生 / 非收集"}]);
  if (state.view !== "heroes") node.innerHTML += make("race", "种族", f.races) + make("keyword", "词条", f.keywords);
  if (state.view === "heroes") node.querySelector('[data-filter="battlegrounds"] option').textContent = "显示酒馆皮肤";

  node.querySelectorAll("select").forEach(select => select.onchange = guarded(() => {
    state.filters[select.dataset.filter] = select.value;
    state.offset = 0;
    return refresh();
  }));

  SelectUI.refresh();
}
async function startup() {
  const status = await api("status");
  updateStatus(status);
  if (status.settings.auto_check_updates !== false) checkUpdates(false);
  state.viewStates = status.settings.view_state || {};
  state.preferencesReady = true;
  applyDisplay(status.settings);
  detailResize.set(status.settings.detail_width);
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
connectHost((connectedHost) => {
  host = connectedHost;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  host.setSmoothScrolling(!motion.matches);
  motion.addEventListener('change', () => host.setSmoothScrolling(!motion.matches));
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
new IntersectionObserver(entries => {
  if (entries.some(e => e.isIntersecting) && state.infiniteScroll) guarded(loadMore)();
}, {rootMargin: '180px'}).observe($('#load-more'));
function attachAudioExports(root) {
  root.querySelectorAll("[data-export]").forEach(source => {
    const locale = source.closest("[data-voice-locale]")?.dataset.voiceLocale || state.voiceLocale;
    const folder = state.audioExports.get(`${locale}:${source.dataset.export}`);
    if (!folder) return;
    const row = source.closest(".voice-translation, .voice-row");
    let button = row.querySelector(":scope > .open-audio-export");
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
  queueVoiceRefresh();
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
    queueVoiceRefresh();
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
    queueVoiceRefresh();
    return;
  }
  const items = [...new Map(state.voiceItems.filter(a => a.kind === "voice" && !a.text &&
    (retryId ? a.id === retryId : state.voiceVisibleIds?.has(a.id) && !a.speech_done && !a.speech_error)).map(a => [a.id, a])).values()];
  if (!items.length) return;
  const run = {generation}, locale = state.voiceLocale;
  state.speechRun = run;
  const active = () => state.speechRun === run && generation === state.detailGeneration;
  let completed = 0;
  try {
    for (const item of items) {
      if (!active()) return;
      // 网络来源重试可能在等待期间补齐文字；识别永远不盖过已有台词。
      if ((!retryId && !state.voiceVisibleIds?.has(item.id)) || state.voiceItems.some(a => a.id === item.id && a.text)) continue;
      state.speechCount = `${++completed} / ${items.length}`;
      state.speechNote = `后台语音识别 ${state.speechCount}；试听与导出可继续使用…`;
      if ($('#speech-inline-status')) $('#speech-inline-status').textContent = state.speechNote;
      try {
        const audioData = await api("audio", {assetid: item.id, locale});
        if (!active()) return;
        // 切分组/翻页后不继续识别旧页；已经完成的解码缓存仍可供下次试听复用。
        if (!retryId && !state.voiceVisibleIds?.has(item.id)) continue;
        const result = await api("speech", {paths: audioData.samples.map(s => s.path), locale, force: !!retryId});
        if (!active()) return;
        state.voiceItems.forEach(a => { if (a.id === item.id && !a.text) Object.assign(a, {
          speech_text: result.text, speech_cached: result.cached, speech_done: true, speech_error: ""
        }); });
        queueVoiceRefresh(item.id);
      } catch (error) {
        if (!active()) return;
        state.voiceItems.forEach(a => { if (a.id === item.id) a.speech_error = error.message; });
        // 模型/网络故障不应对同一页数十条语音反复重试；保留逐条主动重试入口。
        state.speechNote = "自动识别已暂停：" + error.message + "。可点击语音识别按钮重试。";
        return;
      }
    }
    if (active()) state.speechNote = "语音识别完成；语音识别文字仅供参考，不保证准确性。";
  } finally {
    if (active()) { state.speechRun = null; queueVoiceRefresh(); }
  }
  if (generation === state.detailGeneration) queueVisibleSpeech();
}

function queueVisibleSpeech() {
  // 只自动识别当前页；旅店老板等上千条语音不再一打开就全部解码。
  // 已在运行的批次完成后会自然处理用户切换到的新页。
  if (state.tab === 'voices' && state.voiceKind === 'voice' && state.transcriptPending !== state.detailGeneration)
    setTimeout(() => recognizeMissing(state.detailGeneration), 0);
}

// 词条气泡独立于详情的重绘；通过 textContent 放入解释，避免客户端富文本注入。
document.addEventListener('click', event => {
  const relation = event.target.closest('[data-text-relation]');
  if (relation) {
    const data = state.card?.text_links?.[Number(relation.dataset.textRelation)];
    if (!data) return;
    if (data.cards.length === 1) { showCard(data.cards[0].id, 'related'); return; }
    const dialog = $('#relation-dialog');
    $('#relation-title').textContent = data.label + ' · ' + data.cards.length + ' 张';
    $('#relation-choices').innerHTML = data.cards.map(r=>`<button class="related-card" data-choice="${esc(r.id)}"><img class="relation-thumb" data-relation-image="${esc(r.id)}" alt=""><b>${esc(r.name)}</b><small>${esc(r.variant || r.id)} ↗</small></button>`).join('');
    dialog.querySelectorAll('[data-choice]').forEach(b=>b.onclick=()=>{dialog.close();showCard(b.dataset.choice,'related');});
    dialog.showModal(); loadRelationImages(dialog); return;
  }
  const keyword = event.target.closest('[data-keyword]');
  document.querySelector('.keyword-popover')?.remove();
  if (!keyword) return;
  const data = state.card?.keywords.find(k => k.name === keyword.dataset.keyword);
  if (!data) return;
  const bubble = document.createElement('div');
  bubble.className = 'keyword-popover'; bubble.setAttribute('role', 'status');
  const title = document.createElement('strong'), text = document.createElement('p');
  title.textContent = plain(data.name); text.textContent = plain(data.text);
  bubble.append(title, text); document.body.append(bubble);
  const rect = logicalViewport(keyword.getBoundingClientRect());
  bubble.style.left = `${Math.max(12, Math.min(rect.left, rect.viewportWidth - bubble.offsetWidth - 12))}px`;
  bubble.style.top = `${Math.max(12, Math.min(rect.bottom + 8, rect.viewportHeight - bubble.offsetHeight - 12))}px`;
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') document.querySelector('.keyword-popover')?.remove();
});

// 更新查询在独立网络线程执行；自动失败不弹窗，手动检查明确显示错误。
let updateChecking = false;
async function checkUpdates(manual) {
  if (updateChecking) return;
  updateChecking = true;
  const button = $("#check-updates"), status = $("#update-status");
  if (button) button.disabled = true;
  if (status) status.textContent = '正在查询 GitHub 稳定版本…';
  try {
    const result = await api('check_updates');
    if (status) status.textContent = result.message;
    if (result.available) {
      const dialog = $("#update-dialog");
      $("#update-description").textContent = `当前 v${result.current}，发现 ${result.latest}。下载并解压新版后即可使用。`;
      $("#update-download").href = result.url;
      if (!dialog.open) dialog.showModal();
    } else if (manual) toast(result.message);
  } catch (error) {
    if (status) status.textContent = error.message;
    if (manual) toast(error.message);
  } finally { updateChecking = false; if (button) button.disabled = false; }
}
$("#update-close").onclick = () => $("#update-dialog").close();

// 对不可细分的原生解码/网络等待，显示真实阶段与等待时间，不伪造百分比。
// 初始化和台词有自己的进度区域；这里覆盖检索、媒体、特效、导出和诊断。
function updateActivity() {
  const node = $('#activity-status');
  if (!node) return;
  const labels = {list_cards:'检索卡牌', list_assets:'检索资源', card:'读取卡牌详情',
    thumbnail:'解码缩略图', portrait:'读取原画', card_render:'读取完整卡面',
    card_audio:'解析语音引用', audio:'解码音频', related_audio:'解析关联声音',
    general_audio:'解析配套音效', effect:'解析特效', export:'准备导出', diagnostics:'检查资源'};
  const tasks = [...pending.values()].filter(p => labels[p.method]);
  const task = tasks.find(p => p.method !== 'thumbnail') || tasks[0];
  node.hidden = !task;
  if (task) node.textContent = `${task.stage || labels[task.method]} · ${Math.floor((performance.now()-task.started)/1000)} 秒${tasks.length > 1 ? ` · ${tasks.length} 项处理中` : ''}`;
}
setInterval(updateActivity, 1000);

// 一条识别完成只替换它的可见行，不销毁分页、筛选框和其余正在交互的按钮。
function updateRecognizedRows(id) {
  document.querySelectorAll('#voice-list [data-voice-row]').forEach(row => {
    if (row.dataset.voiceRow !== id) return;
    // 同一文件可能用于多个事件；识别结果更新不能把其他触发上下文搬到这一行。
    const match = row.querySelector('[data-voice-match]')?.dataset.voiceMatch;
    const item = state.voiceItems.find(item => item.id === id && (!match || VoiceLocales.key(item) === match));
    if (!item) return;
    const template = document.createElement('template');
    template.innerHTML = voiceRows([item]);
    const next = template.content.firstElementChild;
    const focused = row.contains(document.activeElement) ? document.activeElement.dataset : null;
    row.replaceWith(next);
    renderOtherVoiceLists(null, next);
    bindVoices(next); attachAudioExports(next);
    if (focused?.play) next.querySelector('[data-play]')?.focus({preventScroll:true});
    if (focused?.speech) next.querySelector('[data-speech]')?.focus({preventScroll:true});
  });
  syncPlaybackButton();
}

$("#only-new").onchange = () => {state.filters.new = $("#only-new").checked ? "1" : ""; state.offset=0; refresh();};
