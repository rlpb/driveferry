/* DriveFerry front-end.
   Talks to the local API on the same origin. Every dynamic visual value is a
   class or an SVG attribute: the page runs under a CSP that forbids style
   attributes, including the ones JavaScript writes. */

"use strict";

const TOKEN = document.body.dataset.token;
const PLATFORM = document.body.dataset.platform || "";
const POLL_MS = 700;
const STORE = { theme: "driveferry.theme", locale: "driveferry.locale" };

const state = {
  mode: "copy",
  active: "src",
  remotes: [],
  info: {},
  panes: {
    src: { remote: null, path: "", entries: [], selected: new Set(), cursor: -1, loading: false },
    dst: { remote: null, path: "", entries: [], selected: new Set(), cursor: -1, loading: false },
  },
};

/* ---------- preferences (they must survive a private-mode profile) ---------- */

function readPref(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch (error) {
    return fallback;
  }
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (error) {
    /* storage disabled: the choice simply does not outlive this window */
  }
}

/* ---------- i18n ---------- */

const prefs = { theme: readPref(STORE.theme, "system"), locale: readPref(STORE.locale, "system") };

function activeLocale() {
  if (prefs.locale !== "system" && window.DF_LOCALES[prefs.locale]) return prefs.locale;
  const tag = (navigator.language || "en").slice(0, 2).toLowerCase();
  return window.DF_LOCALES[tag] ? tag : "en";
}

function plural(count, kind) {
  const table = window.DF_PLURALS[activeLocale()] || window.DF_PLURALS.en;
  const forms = table[kind] || ["", ""];
  return forms[count === 1 ? 0 : 1];
}

function t(key, params) {
  const locale = activeLocale();
  const source = window.DF_LOCALES[locale][key] || window.DF_LOCALES.en[key] || key;
  const values = params || {};
  return source.replace(/\{(\w+)\}/g, (match, name) => {
    if (name === "p" || name === "sp") {
      const count = values.n !== undefined ? values.n : values.count;
      return plural(Number(count), name);
    }
    if (name === "fp") return plural(Number(values.folders), "fp");
    if (name === "ip") return plural(Number(values.files), "ip");
    return values[name] !== undefined ? String(values[name]) : match;
  });
}

function applyTranslations() {
  document.documentElement.lang = activeLocale();
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    const label = t(node.dataset.i18nAria);
    node.setAttribute("aria-label", label);
    node.title = label;
  });
}

/* ---------- theme ---------- */

function applyTheme() {
  if (prefs.theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = prefs.theme;
}

/* ---------- element handles ---------- */

const el = {
  panes: {},
  go: document.getElementById("go"),
  goCount: document.getElementById("go-count"),
  hint: document.getElementById("transfer-hint"),
  swap: document.getElementById("swap"),
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  scrim: document.getElementById("scrim"),
  sheet: document.getElementById("sheet"),
  sheetTitle: document.getElementById("sheet-title"),
  sheetBody: document.getElementById("sheet-body"),
  sheetFoot: document.getElementById("sheet-foot"),
  sheetClose: document.getElementById("sheet-close"),
  toasts: document.getElementById("toasts"),
  dry: document.getElementById("opt-dry"),
  serverSide: document.getElementById("opt-serverside"),
  settings: document.getElementById("settings-btn"),
};

document.querySelectorAll(".pane").forEach((node) => {
  const side = node.dataset.side;
  el.panes[side] = {
    root: node,
    account: node.querySelector('[data-role="account"]'),
    crumbs: node.querySelector('[data-role="crumbs"]'),
    list: node.querySelector('[data-role="list"]'),
    empty: node.querySelector('[data-role="empty"]'),
    foot: node.querySelector('[data-role="foot"]'),
    drop: node.querySelector('[data-role="drop"]'),
    storage: node.querySelector('[data-role="storage"]'),
    storageFill: node.querySelector(".storage-fill"),
    storageText: node.querySelector('[data-role="storage-text"]'),
  };
});

/* ---------- window chrome ---------- */

function markPlatform() {
  const classes = document.body.classList;
  classes.add(PLATFORM === "darwin" ? "is-mac" : PLATFORM === "win32" ? "is-win" : "is-linux");
  // Until pywebview announces itself the page might be a plain browser tab,
  // where painted window buttons would do nothing.
  if (window.pywebview) classes.remove("is-web");
  else classes.add("is-web");
}

window.addEventListener("pywebviewready", () => document.body.classList.remove("is-web"));

document.querySelectorAll("[data-window]").forEach((button) => {
  button.addEventListener("click", () => {
    const api = window.pywebview && window.pywebview.api;
    if (!api) return;
    if (button.dataset.window === "minimize") api.minimize();
    if (button.dataset.window === "maximize") api.toggle_maximize();
    if (button.dataset.window === "close") api.close();
  });
});

/* ---------- helpers ---------- */

async function api(operation, payload) {
  const response = await fetch("/api/" + operation, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-DriveFerry-Token": TOKEN },
    body: JSON.stringify(payload || {}),
  });
  let data;
  try {
    data = await response.json();
  } catch (error) {
    throw new Error("The local service returned an unreadable answer.");
  }
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "-";
  if (bytes < 1000) return bytes + " B";
  const units = ["kB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = -1;
  do {
    value /= 1000;
    unit += 1;
  } while (value >= 1000 && unit < units.length - 1);
  return value.toFixed(value < 10 ? 1 : 0) + " " + units[unit];
}

function formatSpeed(bytesPerSecond) {
  if (!bytesPerSecond) return "-";
  return formatBytes(bytesPerSecond) + "/s";
}

function formatEta(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const total = Math.round(seconds);
  if (total < 60) return total + "s";
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return minutes + "m " + (total % 60) + "s";
  return Math.floor(minutes / 60) + "h " + (minutes % 60) + "m";
}

function formatDate(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(activeLocale(), { year: "numeric", month: "short", day: "numeric" });
}

function icon(name, className) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  if (className) svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#" + name);
  svg.appendChild(use);
  return svg;
}

function toast(message, isError) {
  const node = document.createElement("div");
  node.className = "toast" + (isError ? " is-error" : "");
  node.textContent = message;
  el.toasts.appendChild(node);
  setTimeout(() => node.remove(), isError ? 6000 : 3200);
}

function setStatus(text, kind) {
  el.statusText.textContent = text;
  el.statusDot.className = "status-dot" + (kind ? " is-" + kind : "");
}

function other(side) {
  return side === "src" ? "dst" : "src";
}

function joinPath(path, name) {
  return path ? path + "/" + name : name;
}

/* ---------- rendering ---------- */

function renderAccounts() {
  Object.keys(el.panes).forEach((side) => {
    const select = el.panes[side].account;
    select.textContent = "";
    if (!state.remotes.length) {
      const option = document.createElement("option");
      option.textContent = t("empty.noAccounts.title");
      option.value = "";
      select.appendChild(option);
      select.disabled = true;
      return;
    }
    select.disabled = false;
    state.remotes.forEach((remote) => {
      const option = document.createElement("option");
      option.value = remote.name;
      option.textContent = remote.name + "  ·  " + remote.type;
      select.appendChild(option);
    });
    select.value = state.panes[side].remote || "";
  });
}

function renderCrumbs(side) {
  const pane = state.panes[side];
  const container = el.panes[side].crumbs;
  container.textContent = "";
  const segments = pane.path ? pane.path.split("/") : [];

  const root = document.createElement("button");
  root.type = "button";
  root.className = "crumb" + (segments.length ? "" : " is-current");
  root.textContent = pane.remote || "-";
  root.addEventListener("click", () => navigate(side, ""));
  container.appendChild(root);

  segments.forEach((segment, index) => {
    const separator = document.createElement("span");
    separator.className = "crumb-sep";
    separator.textContent = "›";
    container.appendChild(separator);

    const crumb = document.createElement("button");
    crumb.type = "button";
    const isLast = index === segments.length - 1;
    crumb.className = "crumb" + (isLast ? " is-current" : "");
    crumb.textContent = segment;
    if (!isLast) {
      const target = segments.slice(0, index + 1).join("/");
      crumb.addEventListener("click", () => navigate(side, target));
    }
    container.appendChild(crumb);
  });
  container.scrollLeft = container.scrollWidth;
}

function renderSkeleton(side) {
  const list = el.panes[side].list;
  list.textContent = "";
  el.panes[side].empty.hidden = true;
  const wrap = document.createElement("li");
  wrap.className = "skeleton";
  ["w-70", "w-45", "w-85", "w-70", "w-45"].forEach((width) => {
    const bar = document.createElement("div");
    bar.className = "skel-row " + width;
    wrap.appendChild(bar);
  });
  list.appendChild(wrap);
}

function renderEmpty(side, title, detail, iconName) {
  const holder = el.panes[side].empty;
  holder.textContent = "";
  holder.appendChild(icon(iconName || "i-folder"));
  const heading = document.createElement("strong");
  heading.textContent = title;
  const paragraph = document.createElement("p");
  paragraph.textContent = detail;
  holder.appendChild(heading);
  holder.appendChild(paragraph);
  holder.hidden = false;
}

function renderList(side) {
  const pane = state.panes[side];
  const nodes = el.panes[side];
  nodes.list.textContent = "";

  if (!pane.entries.length) {
    renderEmpty(side, t("empty.folder.title"), t("empty.folder.detail"), "i-folder");
  } else {
    nodes.empty.hidden = true;
  }

  pane.entries.forEach((entry, index) => {
    const row = document.createElement("li");
    row.className = "row " + (entry.is_dir ? "is-dir" : "is-file");
    if (pane.selected.has(entry.name)) row.classList.add("is-selected");
    if (pane.cursor === index) row.classList.add("is-cursor");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", pane.selected.has(entry.name) ? "true" : "false");
    row.draggable = true;
    row.dataset.name = entry.name;

    row.appendChild(icon(entry.is_dir ? "i-folder" : "i-file", "row-icon"));

    const name = document.createElement("span");
    name.className = "row-name";
    name.textContent = entry.name;
    name.title = entry.name;
    row.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "row-meta";
    meta.textContent = entry.is_dir
      ? formatDate(entry.mtime)
      : formatBytes(entry.size) + " · " + formatDate(entry.mtime);
    row.appendChild(meta);

    const open = document.createElement("button");
    open.type = "button";
    open.className = "row-open";
    open.setAttribute("aria-label", t("action.refresh"));
    open.appendChild(icon("i-chevron"));
    open.addEventListener("click", (event) => {
      event.stopPropagation();
      navigate(side, joinPath(pane.path, entry.name));
    });
    row.appendChild(open);

    row.addEventListener("click", (event) => onRowClick(side, index, event));
    row.addEventListener("dblclick", () => {
      if (entry.is_dir) navigate(side, joinPath(pane.path, entry.name));
    });
    row.addEventListener("dragstart", (event) => onDragStart(side, index, event, row));
    row.addEventListener("dragend", () => row.classList.remove("is-dragging"));

    nodes.list.appendChild(row);
  });

  renderFoot(side);
}

function renderFoot(side) {
  const pane = state.panes[side];
  const folders = pane.entries.filter((entry) => entry.is_dir).length;
  const files = pane.entries.length - folders;
  const left = document.createElement("span");
  left.textContent = t("foot.counts", { folders: folders, files: files });
  const right = document.createElement("span");
  if (pane.selected.size) {
    right.className = "selected-count";
    right.textContent = t("foot.selected", { n: pane.selected.size });
  }
  const foot = el.panes[side].foot;
  foot.textContent = "";
  foot.appendChild(left);
  foot.appendChild(right);
}

function renderStorage(side, about) {
  const nodes = el.panes[side];
  if (!about || !about.supported || !about.total) {
    nodes.storage.hidden = true;
    return;
  }
  const ratio = Math.min(1, (about.used || 0) / about.total);
  nodes.storage.hidden = false;
  nodes.storageFill.setAttribute("width", String((ratio * 100).toFixed(2)));
  nodes.storageFill.classList.toggle("is-tight", ratio >= 0.8 && ratio < 0.95);
  nodes.storageFill.classList.toggle("is-full", ratio >= 0.95);
  nodes.storageText.textContent = t("storage.text", {
    used: formatBytes(about.used),
    total: formatBytes(about.total),
    free: formatBytes(about.free),
  });
}

function renderGo() {
  const pane = state.panes[state.active];
  const count = pane.selected.size;
  el.go.disabled = count === 0 || !state.panes[other(state.active)].remote;
  el.goCount.textContent = String(count);
  el.go.classList.toggle("is-move", state.mode === "move");
  el.go.classList.toggle("is-reversed", state.active === "dst");
  el.go.setAttribute("aria-label", t(state.mode === "move" ? "sheet.moveN" : "sheet.copyN", { n: count }));
  if (!state.remotes.length) {
    el.hint.textContent = t("hint.addAccount");
  } else if (!count) {
    el.hint.textContent = t(state.mode === "move" ? "hint.selectMove" : "hint.selectCopy");
  } else {
    el.hint.textContent = state.panes[state.active].remote + " → " + state.panes[other(state.active)].remote;
  }
}

/* ---------- data ---------- */

async function refreshPane(side, options) {
  const pane = state.panes[side];
  if (!pane.remote) {
    el.panes[side].list.textContent = "";
    el.panes[side].storage.hidden = true;
    renderEmpty(side, t("empty.noAccount.title"), t("empty.noAccount.detail"), "i-cloud");
    return;
  }
  pane.loading = true;
  renderSkeleton(side);
  try {
    const data = await api("list", { remote: pane.remote, path: pane.path });
    pane.entries = data.entries;
    if (!options || !options.keepSelection) {
      pane.selected.clear();
      pane.cursor = -1;
    }
    renderList(side);
    renderCrumbs(side);
  } catch (error) {
    el.panes[side].list.textContent = "";
    renderEmpty(side, t("empty.error.title"), error.message, "i-alert");
    renderCrumbs(side);
  } finally {
    pane.loading = false;
    renderGo();
  }

  api("about", { remote: pane.remote })
    .then((about) => renderStorage(side, about))
    .catch(() => renderStorage(side, null));
}

function navigate(side, path) {
  state.panes[side].path = path;
  refreshPane(side);
}

async function loadState() {
  setStatus(t("status.connecting"), "busy");
  try {
    const data = await api("state", {});
    state.info = data;
    state.remotes = data.remotes;
    if (!state.panes.src.remote) state.panes.src.remote = (state.remotes[0] || {}).name || null;
    if (!state.panes.dst.remote) {
      state.panes.dst.remote = (state.remotes[1] || state.remotes[0] || {}).name || null;
    }
    renderAccounts();
    setStatus(
      t("status.ready", {
        version: data.rclone_version,
        count: state.remotes.length,
        plural: state.remotes.length === 1 ? "" : "s",
      }),
      "ok"
    );
    if (!state.remotes.length) {
      renderEmpty("src", t("empty.noAccounts.title"), t("empty.noAccounts.detail"), "i-cloud");
      renderEmpty("dst", t("empty.noAccounts.title"), t("empty.noAccounts.detail2"), "i-cloud");
      renderGo();
      return;
    }
    await Promise.all([refreshPane("src"), refreshPane("dst")]);
  } catch (error) {
    setStatus(error.message, "error");
    toast(error.message, true);
  }
}

/* ---------- selection ---------- */

function onRowClick(side, index, event) {
  const pane = state.panes[side];
  const entry = pane.entries[index];
  state.active = side;

  if (event.shiftKey && pane.cursor >= 0) {
    const [from, to] = pane.cursor < index ? [pane.cursor, index] : [index, pane.cursor];
    for (let i = from; i <= to; i += 1) pane.selected.add(pane.entries[i].name);
  } else if (event.ctrlKey || event.metaKey) {
    if (pane.selected.has(entry.name)) pane.selected.delete(entry.name);
    else pane.selected.add(entry.name);
  } else if (pane.selected.size === 1 && pane.selected.has(entry.name)) {
    pane.selected.clear();
  } else {
    pane.selected.clear();
    pane.selected.add(entry.name);
  }
  pane.cursor = index;
  renderList(side);
  renderGo();
}

function moveCursor(side, delta) {
  const pane = state.panes[side];
  if (!pane.entries.length) return;
  pane.cursor = Math.max(0, Math.min(pane.entries.length - 1, pane.cursor + delta));
  renderList(side);
  const row = el.panes[side].list.children[pane.cursor];
  if (row && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
}

function onListKeydown(side, event) {
  const pane = state.panes[side];
  state.active = side;
  switch (event.key) {
    case "ArrowDown":
      event.preventDefault();
      moveCursor(side, pane.cursor < 0 ? 0 : 1);
      break;
    case "ArrowUp":
      event.preventDefault();
      moveCursor(side, -1);
      break;
    case " ":
      event.preventDefault();
      if (pane.cursor >= 0) {
        const name = pane.entries[pane.cursor].name;
        if (pane.selected.has(name)) pane.selected.delete(name);
        else pane.selected.add(name);
        renderList(side);
        renderGo();
      }
      break;
    case "Enter":
      event.preventDefault();
      if (pane.cursor >= 0 && pane.entries[pane.cursor].is_dir) {
        navigate(side, joinPath(pane.path, pane.entries[pane.cursor].name));
      }
      break;
    case "Backspace":
      event.preventDefault();
      if (pane.path) navigate(side, pane.path.split("/").slice(0, -1).join("/"));
      break;
    case "a":
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        pane.entries.forEach((entry) => pane.selected.add(entry.name));
        renderList(side);
        renderGo();
      }
      break;
    default:
      break;
  }
}

/* ---------- drag and drop ---------- */

function onDragStart(side, index, event, row) {
  const pane = state.panes[side];
  const entry = pane.entries[index];
  if (!pane.selected.has(entry.name)) {
    pane.selected.clear();
    pane.selected.add(entry.name);
    pane.cursor = index;
    renderList(side);
  }
  state.active = side;
  renderGo();
  row.classList.add("is-dragging");
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData("text/plain", side);
}

Object.keys(el.panes).forEach((side) => {
  const nodes = el.panes[side];
  nodes.drop.addEventListener("dragover", (event) => {
    if (state.active === side) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    nodes.root.classList.add("is-droptarget");
  });
  nodes.drop.addEventListener("dragleave", () => nodes.root.classList.remove("is-droptarget"));
  nodes.drop.addEventListener("drop", (event) => {
    event.preventDefault();
    nodes.root.classList.remove("is-droptarget");
    if (state.active === side) return;
    openTransferSheet();
  });
  nodes.list.addEventListener("keydown", (event) => onListKeydown(side, event));
  nodes.list.addEventListener("focus", () => {
    state.active = side;
    renderGo();
  });
  nodes.account.addEventListener("change", () => {
    state.panes[side].remote = nodes.account.value || null;
    state.panes[side].path = "";
    refreshPane(side);
  });
  nodes.root.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.action === "refresh") refreshPane(side);
      if (button.dataset.action === "newfolder") openNewFolderSheet(side);
    });
  });
});

/* ---------- sheet plumbing ---------- */

let sheetEscapeHandler = null;

function openSheet(title) {
  el.sheetTitle.textContent = title;
  el.sheetBody.textContent = "";
  el.sheetFoot.textContent = "";
  el.sheet.hidden = false;
  el.scrim.hidden = false;
  sheetEscapeHandler = (event) => {
    if (event.key === "Escape" && !el.sheetClose.disabled) closeSheet();
  };
  document.addEventListener("keydown", sheetEscapeHandler);
}

function closeSheet() {
  el.sheet.hidden = true;
  el.scrim.hidden = true;
  el.sheetClose.disabled = false;
  if (sheetEscapeHandler) document.removeEventListener("keydown", sheetEscapeHandler);
  sheetEscapeHandler = null;
}

el.sheetClose.addEventListener("click", closeSheet);
el.scrim.addEventListener("click", () => {
  if (!el.sheetClose.disabled) closeSheet();
});

function button(label, className, onClick) {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "btn " + (className || "");
  node.textContent = label;
  node.addEventListener("click", onClick);
  return node;
}

function notice(kind, iconName, text) {
  const box = document.createElement("div");
  box.className = "notice notice-" + kind;
  box.appendChild(icon(iconName));
  const body = document.createElement("div");
  body.textContent = text;
  box.appendChild(body);
  return box;
}

function routeSummary(fromSide, toSide) {
  const wrap = document.createElement("div");
  wrap.className = "route";
  [state.panes[fromSide], state.panes[toSide]].forEach((pane, index) => {
    if (index === 1) wrap.appendChild(icon("i-arrow", "route-arrow"));
    const end = document.createElement("div");
    end.className = "route-end";
    const label = document.createElement("div");
    label.className = "route-label";
    label.textContent = t(index === 0 ? "pane.from" : "pane.to");
    const value = document.createElement("div");
    value.className = "route-value";
    value.textContent = pane.remote;
    const path = document.createElement("div");
    path.className = "route-path";
    path.textContent = "/" + (pane.path || "");
    end.appendChild(label);
    end.appendChild(value);
    end.appendChild(path);
    wrap.appendChild(end);
  });
  return wrap;
}

function itemList(names, dirs) {
  const list = document.createElement("ul");
  list.className = "itemlist";
  names.forEach((name) => {
    const item = document.createElement("li");
    item.className = "item";
    item.dataset.name = name;
    item.appendChild(icon(dirs.has(name) ? "i-folder" : "i-file"));
    const label = document.createElement("span");
    label.className = "item-name";
    label.textContent = name;
    const stateLabel = document.createElement("span");
    stateLabel.className = "item-state";
    stateLabel.textContent = t("item.waiting");
    item.appendChild(label);
    item.appendChild(stateLabel);
    list.appendChild(item);
  });
  return list;
}

function progressBlock() {
  const wrap = document.createElement("div");
  wrap.className = "progress";
  wrap.innerHTML =
    '<svg class="progress-bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">' +
    '<rect class="progress-track" x="0" y="0" width="100" height="8" rx="4"/>' +
    '<rect class="progress-fill" x="0" y="0" width="0" height="8" rx="4"/></svg>' +
    '<p class="progress-meta"><span class="progress-done"></span>' +
    '<span class="progress-speed">-</span><span class="progress-eta">-</span></p>';
  return wrap;
}

/* ---------- settings ---------- */

function settingRow(labelKey, control) {
  const row = document.createElement("div");
  row.className = "setting-row";
  const label = document.createElement("span");
  label.className = "setting-label";
  label.textContent = t(labelKey);
  row.appendChild(label);
  row.appendChild(control);
  return row;
}

function miniSegmented(options, current, onPick) {
  const group = document.createElement("div");
  group.className = "seg-mini";
  group.setAttribute("role", "radiogroup");
  options.forEach((option) => {
    const node = document.createElement("button");
    node.type = "button";
    node.setAttribute("role", "radio");
    node.setAttribute("aria-checked", option.value === current ? "true" : "false");
    node.className = option.value === current ? "is-active" : "";
    node.textContent = option.label;
    node.addEventListener("click", () => onPick(option.value));
    group.appendChild(node);
  });
  return group;
}

function group(titleKey, rows, hintKey) {
  const wrap = document.createElement("section");
  wrap.className = "group";
  const title = document.createElement("h3");
  title.className = "group-title";
  title.textContent = t(titleKey);
  const body = document.createElement("div");
  body.className = "group-body";
  rows.forEach((row) => body.appendChild(row));
  wrap.appendChild(title);
  wrap.appendChild(body);
  if (hintKey) {
    const hint = document.createElement("p");
    hint.className = "group-hint";
    hint.textContent = t(hintKey);
    wrap.appendChild(hint);
  }
  return wrap;
}

function valueRow(labelKey, value) {
  const row = document.createElement("div");
  row.className = "setting-row";
  const label = document.createElement("span");
  label.className = "setting-label";
  label.textContent = t(labelKey);
  const text = document.createElement("span");
  text.className = "setting-value";
  text.textContent = value;
  row.appendChild(label);
  row.appendChild(text);
  return row;
}

function openSettings() {
  openSheet(t("settings.title"));

  const themeRow = settingRow(
    "settings.appearance",
    miniSegmented(
      [
        { value: "system", label: t("theme.system") },
        { value: "light", label: t("theme.light") },
        { value: "dark", label: t("theme.dark") },
      ],
      prefs.theme,
      (value) => {
        prefs.theme = value;
        writePref(STORE.theme, value);
        applyTheme();
        openSettings();
      }
    )
  );

  const localeRow = settingRow(
    "settings.language",
    miniSegmented(
      [{ value: "system", label: t("theme.system") }].concat(
        Object.keys(window.DF_LOCALES).map((code) => ({
          value: code,
          label: window.DF_LOCALES[code]["lang.name"],
        }))
      ),
      prefs.locale,
      (value) => {
        prefs.locale = value;
        writePref(STORE.locale, value);
        applyTranslations();
        renderAccounts();
        renderGo();
        Object.keys(el.panes).forEach((side) => {
          renderList(side);
          renderCrumbs(side);
        });
        openSettings();
      }
    )
  );

  el.sheetBody.appendChild(group("settings.appearance", [themeRow], "settings.appearanceHint"));
  el.sheetBody.appendChild(group("settings.language", [localeRow], "settings.languageHint"));
  el.sheetBody.appendChild(
    group(
      "settings.about",
      [
        valueRow("settings.accounts", String(state.remotes.length)),
        valueRow("settings.about", "rclone " + (state.info.rclone_version || "-")),
        valueRow("action.refresh", state.info.rclone_binary || "-"),
      ],
      "settings.accountsHint"
    )
  );
  el.sheetFoot.appendChild(button(t("action.done"), "btn-primary", closeSheet));
}

el.settings.addEventListener("click", openSettings);

/* ---------- new folder ---------- */

function openNewFolderSheet(side) {
  const pane = state.panes[side];
  if (!pane.remote) {
    toast(t("toast.selectAccount"), true);
    return;
  }
  openSheet(t("sheet.newFolder"));
  const field = document.createElement("div");
  field.className = "field";
  const label = document.createElement("label");
  label.setAttribute("for", "folder-name");
  label.textContent = t("field.createIn", { location: pane.remote + ":/" + (pane.path || "") });
  const input = document.createElement("input");
  input.id = "folder-name";
  input.type = "text";
  input.autocomplete = "off";
  input.placeholder = t("field.folderName");
  field.appendChild(label);
  field.appendChild(input);
  el.sheetBody.appendChild(field);

  const create = async () => {
    const name = input.value.trim();
    if (!name) return;
    try {
      await api("mkdir", { remote: pane.remote, path: pane.path, name: name });
      closeSheet();
      toast(t("toast.created", { name: name }));
      refreshPane(side);
    } catch (error) {
      toast(error.message, true);
    }
  };

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") create();
  });
  el.sheetFoot.appendChild(button(t("action.cancel"), "", closeSheet));
  el.sheetFoot.appendChild(button(t("action.create"), "btn-primary", create));
  input.focus();
}

/* ---------- transfer ---------- */

function selectionContext() {
  const fromSide = state.active;
  const toSide = other(fromSide);
  const from = state.panes[fromSide];
  const names = Array.from(from.selected);
  const dirs = new Set(
    from.entries.filter((entry) => entry.is_dir && from.selected.has(entry.name)).map((entry) => entry.name)
  );
  return { fromSide, toSide, from, to: state.panes[toSide], names, dirs };
}

function openTransferSheet() {
  const context = selectionContext();
  if (!context.names.length || !context.to.remote) return;

  const isMove = state.mode === "move";
  const dryRun = el.dry.checked;
  openSheet(t(isMove ? "sheet.moveN" : "sheet.copyN", { n: context.names.length }));

  el.sheetBody.appendChild(routeSummary(context.fromSide, context.toSide));
  if (dryRun) el.sheetBody.appendChild(notice("info", "i-check", t("notice.dryRun")));
  if (isMove && !dryRun) el.sheetBody.appendChild(notice("warn", "i-alert", t("notice.moveSteps")));
  if (el.serverSide.checked) el.sheetBody.appendChild(notice("info", "i-cloud", t("notice.serverSide")));
  el.sheetBody.appendChild(itemList(context.names, context.dirs));

  const label = dryRun
    ? t("btn.runDryRun")
    : isMove
      ? t("btn.startCopy")
      : t("btn.copyN", { n: context.names.length });

  el.sheetFoot.appendChild(button(t("action.cancel"), "", closeSheet));
  el.sheetFoot.appendChild(
    button(label, isMove && !dryRun ? "btn-warn" : "btn-primary", () => startTransfer(context, isMove, dryRun))
  );
}

async function startTransfer(context, isMove, dryRun) {
  el.sheetFoot.textContent = "";
  el.sheetClose.disabled = true;
  const progress = progressBlock();
  el.sheetBody.insertBefore(progress, el.sheetBody.querySelector(".itemlist"));
  const fill = progress.querySelector(".progress-fill");
  const doneLabel = progress.querySelector(".progress-done");
  const speedLabel = progress.querySelector(".progress-speed");
  const etaLabel = progress.querySelector(".progress-eta");

  let started;
  try {
    started = await api("transfer", {
      src_remote: context.from.remote,
      src_path: context.from.path,
      dst_remote: context.to.remote,
      dst_path: context.to.path,
      names: context.names,
      dirs: Array.from(context.dirs),
      dry_run: dryRun,
      server_side: el.serverSide.checked,
    });
  } catch (error) {
    el.sheetClose.disabled = false;
    el.sheetBody.appendChild(notice("danger", "i-alert", error.message));
    el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
    return;
  }

  const jobIds = started.jobs.map((job) => job.jobid);
  const byJob = new Map(started.jobs.map((job) => [job.jobid, job.name]));
  setStatus(t("status.transferring"), "busy");

  const cancel = button(t("btn.cancelTransfer"), "btn-danger", async () => {
    cancel.disabled = true;
    await api("transfer_cancel", { jobs: jobIds }).catch(() => {});
    toast(t("toast.cancelling"));
  });
  el.sheetFoot.appendChild(cancel);

  const poll = async () => {
    let status;
    try {
      status = await api("transfer_status", { group: started.group, jobs: jobIds });
    } catch (error) {
      el.sheetBody.appendChild(notice("danger", "i-alert", error.message));
      el.sheetClose.disabled = false;
      return;
    }

    const stats = status.stats;
    const ratio = stats.total_bytes ? Math.min(1, stats.bytes / stats.total_bytes) : status.finished ? 1 : 0;
    fill.setAttribute("width", String((ratio * 100).toFixed(2)));
    doneLabel.textContent = t("progress.of", {
      done: formatBytes(stats.bytes),
      total: formatBytes(stats.total_bytes),
    });
    speedLabel.textContent = formatSpeed(stats.speed);
    etaLabel.textContent = t("progress.eta", { eta: formatEta(stats.eta) });

    status.jobs.forEach((job) => {
      const item = el.sheetBody.querySelector('.item[data-name="' + CSS.escape(byJob.get(job.jobid)) + '"]');
      if (!item) return;
      const label = item.querySelector(".item-state");
      if (!job.finished) {
        label.textContent = t("item.transferring");
      } else if (job.success) {
        item.classList.add("is-ok");
        label.textContent = t("item.done");
      } else {
        item.classList.add("is-failed");
        label.textContent = t("item.failed");
        item.title = job.error;
      }
    });

    if (!status.finished) {
      setTimeout(poll, POLL_MS);
      return;
    }

    cancel.remove();
    el.sheetClose.disabled = false;
    fill.classList.toggle("is-done", status.failed.length === 0);
    fill.classList.toggle("is-failed", status.failed.length > 0);
    setStatus(t("status.idle"), "ok");
    refreshPane(context.toSide);

    if (status.failed.length) {
      el.sheetBody.appendChild(
        notice("danger", "i-alert", t("notice.failed", { n: status.failed.length, error: status.failed[0].error }))
      );
      el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
      return;
    }
    if (dryRun) {
      el.sheetBody.appendChild(notice("ok", "i-check", t("notice.dryRunDone")));
      el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
      return;
    }
    await runVerification(context, isMove);
  };

  poll();
}

async function runVerification(context, isMove) {
  const pending = notice("info", "i-refresh", t("notice.checking"));
  el.sheetBody.appendChild(pending);
  let result;
  try {
    result = await api("verify", {
      src_remote: context.from.remote,
      src_path: context.from.path,
      dst_remote: context.to.remote,
      dst_path: context.to.path,
      names: context.names,
      dirs: Array.from(context.dirs),
    });
  } catch (error) {
    pending.remove();
    el.sheetBody.appendChild(notice("danger", "i-alert", t("notice.verifyFailed", { error: error.message })));
    el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
    return;
  }
  pending.remove();

  result.results.forEach((entry) => {
    const item = el.sheetBody.querySelector('.item[data-name="' + CSS.escape(entry.name) + '"]');
    if (!item) return;
    const label = item.querySelector(".item-state");
    if (entry.ok) {
      label.textContent = t("item.verified", { count: entry.dst.count, size: formatBytes(entry.dst.bytes) });
    } else {
      item.classList.remove("is-ok");
      item.classList.add("is-failed");
      label.textContent = entry.error === "missing at destination" ? t("item.missing") : t("item.mismatch");
    }
  });

  if (!result.ok) {
    el.sheetBody.appendChild(notice("danger", "i-alert", t("notice.mismatch")));
    el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
    return;
  }

  el.sheetBody.appendChild(notice("ok", "i-check", t("notice.verified")));

  if (!isMove) {
    toast(t("toast.copied", { n: context.names.length }));
    el.sheetFoot.appendChild(button(t("action.done"), "btn-primary", closeSheet));
    return;
  }

  el.sheetBody.appendChild(
    notice("danger", "i-alert", t("notice.deleteWarning", { account: context.from.remote }))
  );
  el.sheetFoot.appendChild(button(t("btn.keepOriginals"), "", closeSheet));
  el.sheetFoot.appendChild(
    button(t("btn.deleteN", { n: context.names.length }), "btn-danger", async () => {
      try {
        const deleted = await api("delete", {
          remote: context.from.remote,
          path: context.from.path,
          names: context.names,
          dirs: Array.from(context.dirs),
          confirm: "DELETE",
        });
        if (deleted.failed.length) toast(t("toast.deleteFailed", { n: deleted.failed.length }), true);
        else toast(t("toast.moved", { n: deleted.deleted.length }));
        closeSheet();
        refreshPane(context.fromSide);
      } catch (error) {
        toast(error.message, true);
      }
    })
  );
}

/* ---------- top bar ---------- */

document.querySelectorAll(".seg").forEach((segment) => {
  segment.addEventListener("click", () => {
    state.mode = segment.dataset.mode;
    document.querySelectorAll(".seg").forEach((node) => {
      const active = node === segment;
      node.classList.toggle("is-active", active);
      node.setAttribute("aria-checked", active ? "true" : "false");
    });
    renderGo();
  });
});

el.swap.addEventListener("click", () => {
  const src = state.panes.src;
  const dst = state.panes.dst;
  [src.remote, dst.remote] = [dst.remote, src.remote];
  [src.path, dst.path] = [dst.path, src.path];
  src.selected.clear();
  dst.selected.clear();
  renderAccounts();
  refreshPane("src");
  refreshPane("dst");
});

el.go.addEventListener("click", openTransferSheet);

/* ---------- boot ---------- */

applyTheme();
applyTranslations();
markPlatform();
loadState();
