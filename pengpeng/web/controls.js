/* Qt WebEngine 在部分 Windows 缩放比例下会把原生 select 弹窗放错位置。
 * 保留 select 作为值模型，使用同一网页内的 listbox 作为可视交互层。
 * 支持方向键、首字搜索、Enter / Escape，并在滚动、失焦时关闭弹层。
 */
"use strict";
const SelectUI = (() => {
  let active = null;
  const widgets = new Map();
  function close() {
    if (!active) return;
    active.popup.remove();
    active.button.setAttribute("aria-expanded", "false");
    active = null;
  }
  function sync(select, button) {
    button.hidden = select.hidden;
    button.disabled = select.disabled;
    button.textContent = select.selectedOptions[0]?.textContent || "请选择";
    button.setAttribute("aria-label", select.getAttribute("aria-label") || button.textContent);
  }
  function open(select, button) {
    close();
    const popup = document.createElement("div");
    popup.className = "select-popup";
    popup.setAttribute("role", "listbox");
    popup.setAttribute("aria-label", button.getAttribute("aria-label"));
    let index = Math.max(0, select.selectedIndex);
    const options = Array.from(select.options);
    const choose = (i) => {
      if (options[i].disabled) return;
      select.value = options[i].value;
      sync(select, button);
      close();
      button.focus();
      select.dispatchEvent(new Event("change", {bubbles: true}));
    };
    options.forEach((option, i) => {
      const row = document.createElement("button");
      row.type = "button";
      row.role = "option";
      row.textContent = option.textContent;
      row.disabled = option.disabled;
      row.setAttribute("aria-selected", String(i === index));
      row.onclick = () => choose(i);
      popup.append(row);
    });
    document.body.append(popup);
    const r = button.getBoundingClientRect();
    popup.style.width = `${Math.min(Math.max(r.width, 210), innerWidth - 24)}px`;
    popup.style.left = `${Math.max(12, Math.min(r.left, innerWidth - popup.offsetWidth - 12))}px`;
    const below = innerHeight - r.bottom - 12;
    const height = Math.min(320, Math.max(below, r.top - 12));
    popup.style.maxHeight = `${height}px`;
    popup.style.top = `${below >= Math.min(popup.scrollHeight, 240) ? r.bottom + 6 : Math.max(12, r.top - Math.min(popup.scrollHeight, height) - 6)}px`;
    active = {popup, button};
    button.setAttribute("aria-expanded", "true");
    function focus() { popup.children[index]?.focus(); }
    popup.onkeydown = (e) => {
      if (e.key === "Escape" || e.key === "Tab") {
        close(); button.focus();
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); }
      } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        index = e.key === "Home" ? 0 : e.key === "End" ? options.length - 1 :
          (index + (e.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
        focus();
      } else if (e.key.length === 1 && e.key !== " ") {
        const found = options.findIndex(o => o.textContent.toLowerCase().startsWith(e.key.toLowerCase()));
        if (found >= 0) { index = found; focus(); }
      }
    };
    focus();
  }
  function refresh() {
    for (const [select, button] of widgets) {
      if (!select.isConnected) { widgets.delete(select); if (active?.button === button) close(); }
      else sync(select, button);
    }
    document.querySelectorAll("select:not([data-enhanced])").forEach(select => {
      select.dataset.enhanced = "true";
      select.classList.add("select-model");
      select.tabIndex = -1;
      select.setAttribute("aria-hidden", "true");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "select-control";
      button.setAttribute("aria-haspopup", "listbox");
      button.setAttribute("aria-expanded", "false");
      button.onclick = () => active?.button === button ? close() : open(select, button);
      button.onkeydown = e => {
        if (["ArrowUp", "ArrowDown"].includes(e.key)) { e.preventDefault(); open(select, button); }
      };
      select.after(button);
      widgets.set(select, button);
      select.addEventListener("change", () => sync(select, button));
      sync(select, button);
    });
  }
  document.addEventListener("pointerdown", e => {
    if (active && !active.popup.contains(e.target) && !active.button.contains(e.target)) close();
  });
  document.addEventListener("scroll", e => {
    if (active && !active.popup.contains(e.target)) close();
  }, true);
  window.addEventListener("resize", close);
  window.addEventListener("blur", close);
  // 只观察 select 本身的变化，避免刷新按钮的文字引发观察器循环。
  new MutationObserver(records => {
    if (records.some(r => r.target.tagName === "SELECT" || [...r.addedNodes].some(n => n.nodeType === 1 && (n.tagName === "SELECT" || n.querySelector?.("select"))))) refresh();
  }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "disabled"]});
  return {refresh, close};
})();
