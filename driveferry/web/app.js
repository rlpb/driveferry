/* DriveFerry front-end.
   Talks to the local API on the same origin. Every dynamic visual value is a
   class or an SVG attribute: the page runs under a CSP that forbids style
   attributes, including the ones JavaScript writes. */

"use strict";

const TOKEN = document.body.dataset.token;
const PLATFORM = document.body.dataset.platform || "";
const POLL_MS = 700;

const state = {
  mode: "copy",
  active: "src",
  remotes: [],
  info: {},
  status: null,
  panes: {
    src: { remote: null, path: "", entries: [], selected: new Set(), cursor: -1, loading: false, about: null },
    dst: { remote: null, path: "", entries: [], selected: new Set(), cursor: -1, loading: false, about: null },
  },
};

/* ---------- preferences ---------- */

/* They arrive with the page and are saved by the local service, not by the
   browser: the server listens on a fresh port every run, and browser storage
   is per origin, so anything kept there would be gone at the next launch. */
const prefs = {
  theme: document.body.dataset.prefTheme || "system",
  locale: document.body.dataset.prefLocale || "system",
};

function savePrefs() {
  api("prefs_set", prefs).catch((error) => toast(error.message, true));
}

/* ---------- i18n ---------- */

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
  compare: document.getElementById("compare"),
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
  statusAction: document.getElementById("status-action"),
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

/** Status set from a translation key, so it can be redrawn in a new language. */
function setStatusKey(key, params, kind) {
  state.status = { key: key, params: params, kind: kind };
  setStatus(t(key, params), kind);
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
  const label = document.createElement("p");
  label.className = "skel-label";
  label.textContent = t("list.loading");
  wrap.appendChild(label);
  ["w-70", "w-45", "w-85", "w-70", "w-45"].forEach((width) => {
    const bar = document.createElement("div");
    bar.className = "skel-row " + width;
    wrap.appendChild(bar);
  });
  list.appendChild(wrap);
}

function renderEmpty(side, title, detail, iconName, action) {
  const holder = el.panes[side].empty;
  holder.textContent = "";
  holder.appendChild(icon(iconName || "i-folder"));
  const heading = document.createElement("strong");
  heading.textContent = title;
  const paragraph = document.createElement("p");
  paragraph.textContent = detail;
  holder.appendChild(heading);
  holder.appendChild(paragraph);
  if (action) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "btn btn-primary empty-action";
    node.textContent = action.label;
    node.addEventListener("click", action.onClick);
    holder.appendChild(node);
  }
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
    total: formatBytes(about.total),
    free: formatBytes(about.free),
  });
}

function renderGo() {
  const pane = state.panes[state.active];
  const count = pane.selected.size;
  el.go.disabled = count === 0 || !state.panes[other(state.active)].remote;
  el.compare.disabled = el.go.disabled;
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
  const opts = options || {};
  const pane = state.panes[side];
  if (!pane.remote) {
    el.panes[side].list.textContent = "";
    el.panes[side].storage.hidden = true;
    renderEmpty(side, t("empty.noAccount.title"), t("empty.noAccount.detail"), "i-cloud");
    return;
  }
  pane.loading = true;
  // A folder already visited comes back from the cache in a few milliseconds.
  // A skeleton drawn for that long is a flash, not feedback, so it waits until
  // the wait is long enough to be worth explaining.
  const skeleton = setTimeout(() => renderSkeleton(side), 150);
  const wanted = { remote: pane.remote, path: pane.path };
  const started = performance.now();
  try {
    const data = await api("list", { remote: wanted.remote, path: wanted.path, refresh: !!opts.refresh });
    clearTimeout(skeleton);
    reportSlowListing(performance.now() - started);
    // Two folders opened quickly race each other, and a cached answer can now
    // overtake a slow one. Whichever the user asked for last is the one drawn.
    if (pane.remote !== wanted.remote || pane.path !== wanted.path) return;
    pane.entries = data.entries;
    if (!opts.keepSelection) {
      pane.selected.clear();
      pane.cursor = -1;
    }
    renderList(side);
    renderCrumbs(side);
  } catch (error) {
    if (pane.remote !== wanted.remote || pane.path !== wanted.path) return;
    el.panes[side].list.textContent = "";
    renderEmpty(side, t("empty.error.title"), error.message, "i-alert");
    renderCrumbs(side);
  } finally {
    clearTimeout(skeleton);
    pane.loading = false;
    renderGo();
  }

  // Free space only moves when something is written, so asking on every folder
  // opened is a second round trip that always answers the same thing.
  if (!opts.refresh && pane.aboutFor === pane.remote) return;
  pane.aboutFor = pane.remote;
  api("about", { remote: pane.remote })
    .then((about) => {
      pane.about = about;
      renderStorage(side, about);
    })
    .catch(() => {
      pane.about = null;
      renderStorage(side, null);
    });
}

/* Google rate limits rclone's shared Google client across every rclone user in
   the world, and rclone answers a 403 by sleeping: one folder can take half a
   minute. Nothing in the app can make that faster, so when it happens the app
   says so and offers the one thing that does fix it. */
const SLOW_LISTING_MS = 4000;
let slowListingReported = false;

function reportSlowListing(elapsed) {
  if (slowListingReported || elapsed < SLOW_LISTING_MS) return;
  if (!state.info.shared_client) return;
  slowListingReported = true;
  setStatusKey("status.throttled", {}, "warn");
  el.statusAction.hidden = false;
}

/** Redraw every string the current language touches, without refetching. */
function renderAll() {
  applyTranslations();
  renderAccounts();
  Object.keys(el.panes).forEach((side) => {
    renderCrumbs(side);
    renderList(side);
    renderStorage(side, state.panes[side].about);
  });
  renderGo();
  if (state.status) setStatus(t(state.status.key, state.status.params), state.status.kind);
}

function navigate(side, path) {
  state.panes[side].path = path;
  refreshPane(side);
}

async function loadState() {
  setStatusKey("status.connecting", {}, "busy");
  try {
    const data = await api("state", {});
    state.info = data;
    state.remotes = data.remotes;
    if (!state.panes.src.remote) state.panes.src.remote = (state.remotes[0] || {}).name || null;
    if (!state.panes.dst.remote) {
      state.panes.dst.remote = (state.remotes[1] || state.remotes[0] || {}).name || null;
    }
    renderAccounts();
    setStatusKey(
      "status.ready",
      { version: data.rclone_version, count: state.remotes.length, n: state.remotes.length },
      "ok"
    );
    if (!state.remotes.length) {
      renderEmpty("src", t("empty.noAccounts.title"), t("empty.noAccounts.detail"), "i-cloud", {
        label: t("account.connect"),
        onClick: openConnectSheet,
      });
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
      if (button.dataset.action === "refresh") refreshPane(side, { refresh: true });
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
  wrap.className = "progress is-preparing";
  wrap.innerHTML =
    '<div class="bar-clip progress-clip">' +
    '<svg class="progress-bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">' +
    '<rect class="progress-track" x="0" y="0" width="100" height="8"/>' +
    '<rect class="progress-fill" x="0" y="0" width="0" height="8"/></svg>' +
    '<div class="progress-sweep" aria-hidden="true"></div></div>' +
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
        savePrefs();
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
        savePrefs();
        renderAll();
        openSettings();
      }
    )
  );

  el.sheetBody.appendChild(group("settings.general", [themeRow, localeRow], "settings.generalHint"));

  const accountRows = state.remotes.map((remote) => {
    const row = document.createElement("div");
    row.className = "setting-row";
    const label = document.createElement("span");
    label.className = "setting-label";
    label.textContent = remote.name;
    const right = document.createElement("span");
    right.className = "row-actions";
    const type = document.createElement("span");
    type.className = "setting-value";
    type.textContent = remote.type;
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "linklike linklike-danger";
    forget.textContent = t("account.forget");
    forget.addEventListener("click", () => forgetAccount(remote.name));
    right.appendChild(type);
    right.appendChild(forget);
    row.appendChild(label);
    row.appendChild(right);
    return row;
  });
  if (!accountRows.length) {
    const empty = document.createElement("div");
    empty.className = "setting-row";
    const label = document.createElement("span");
    label.className = "setting-value";
    label.textContent = t("account.none");
    empty.appendChild(label);
    accountRows.push(empty);
  }
  const accounts = group("settings.accounts", accountRows, "settings.accountsHint");
  const connect = document.createElement("button");
  connect.type = "button";
  connect.className = "btn btn-primary group-action";
  connect.textContent = t("account.connect");
  connect.addEventListener("click", openConnectSheet);
  accounts.appendChild(connect);
  el.sheetBody.appendChild(accounts);

  const clientRow = document.createElement("div");
  clientRow.className = "setting-row";
  const clientLabel = document.createElement("span");
  clientLabel.className = "setting-label";
  clientLabel.textContent = t("client.title");
  const clientRight = document.createElement("span");
  clientRight.className = "row-actions";
  const clientValue = document.createElement("span");
  clientValue.className = "setting-value";
  clientValue.textContent = state.info.google_client_id ? t("client.setUp") : t("client.notSet");
  const clientButton = document.createElement("button");
  clientButton.type = "button";
  clientButton.className = "linklike";
  clientButton.textContent = t(state.info.google_client_id ? "client.change" : "client.configure");
  clientButton.addEventListener("click", openClientSheet);
  clientRight.appendChild(clientValue);
  clientRight.appendChild(clientButton);
  clientRow.appendChild(clientLabel);
  clientRow.appendChild(clientRight);

  el.sheetBody.appendChild(
    group("settings.about", [
      clientRow,
      valueRow("settings.rcloneVersion", state.info.rclone_version || "-"),
      valueRow("settings.rclonePath", state.info.rclone_binary || "-"),
    ])
  );
  el.sheetFoot.appendChild(button(t("action.done"), "btn-primary", closeSheet));
}

el.settings.addEventListener("click", openSettings);

/* ---------- connecting an account ---------- */

function textField(id, labelText, placeholder, type) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("label");
  label.setAttribute("for", id);
  label.textContent = labelText;
  const input = document.createElement("input");
  input.id = id;
  input.type = type || "text";
  // autocomplete="off" alone is not enough: the web view matches saved values
  // by field name, so a name it has never seen is what actually keeps stale
  // suggestions out of a field like the account name.
  input.name = id + "-" + Math.random().toString(36).slice(2, 10);
  input.autocomplete = "off";
  input.setAttribute("autocapitalize", "off");
  input.setAttribute("autocorrect", "off");
  input.spellcheck = false;
  input.placeholder = placeholder || "";
  wrap.appendChild(label);
  wrap.appendChild(input);
  return { wrap: wrap, input: input };
}

/* Mirrors SETUP_URLS in app.py. The window opens them by position through the
   bridge; in a plain browser tab there is no bridge, so the same list is used
   directly. */
const SETUP_URLS = [
  "https://console.cloud.google.com/projectcreate",
  "https://console.cloud.google.com/apis/library/drive.googleapis.com",
  "https://console.cloud.google.com/auth/overview",
  "https://console.cloud.google.com/auth/clients/create",
  "https://rclone.org/drive/#making-your-own-client-id",
];

function openSetupPage(index) {
  const bridge = window.pywebview && window.pywebview.api;
  if (bridge && bridge.open_setup_page) bridge.open_setup_page(index);
  else window.open(SETUP_URLS[index], "_blank", "noopener");
}

const connectDraft = { name: "" };

function openConnectSheet() {
  openSheet(t("account.connectTitle"));

  const name = textField("account-name", t("account.name"), t("account.namePlaceholder"));
  name.input.value = connectDraft.name;
  name.input.addEventListener("input", () => {
    connectDraft.name = name.input.value;
  });
  el.sheetBody.appendChild(name.wrap);

  const hint = document.createElement("p");
  hint.className = "group-hint";
  hint.textContent = t("account.nameHint");
  el.sheetBody.appendChild(hint);

  const signInHint = document.createElement("p");
  signInHint.className = "group-hint";
  signInHint.textContent = t("account.signInHint");
  el.sheetBody.appendChild(signInHint);

  /* One button is the whole flow. The client ID is a one-off setup that lives
     in Settings, so it is offered here as a line, not as a form to fill in
     before every account. */
  if (state.info.google_client_id) {
    el.sheetBody.appendChild(notice("ok", "i-check", t("client.usingOwn")));
  } else {
    const configure = document.createElement("button");
    configure.type = "button";
    configure.className = "linklike";
    configure.textContent = t("client.configure");
    configure.addEventListener("click", openClientSheet);
    el.sheetBody.appendChild(configure);
  }

  el.sheetFoot.appendChild(button(t("action.cancel"), "", closeSheet));
  el.sheetFoot.appendChild(
    button(t("account.signIn"), "btn-primary", async () => {
      const wanted = connectDraft.name.trim();
      try {
        await api("account_connect", { name: wanted, allow_shared_client: true });
      } catch (error) {
        toast(error.message, true);
        return;
      }
      connectDraft.name = "";
      waitForConnection(wanted);
    })
  );
  name.input.focus();
}

/* ---------- the one-off Google client setup ---------- */

/* One screen per step, with the button that opens exactly the page that step
   talks about. Someone who has never seen the Google Cloud console should be
   able to finish without reading anything else. */
const CLIENT_STEPS = [
  { key: "client.step1", url: 0 },
  { key: "client.step2", url: 1 },
  { key: "client.step3", url: 2 },
  { key: "client.step4", url: 3 },
  { key: "client.step5", url: null },
];

const clientDraft = { step: 0, id: "", secret: "" };

function openClientSheet(restart) {
  if (restart !== false) clientDraft.step = 0;
  openSheet(t("client.title"));

  if (clientDraft.step === 0) {
    el.sheetBody.appendChild(notice("info", "i-alert", t("client.why")));
  }

  const step = CLIENT_STEPS[clientDraft.step];
  const counter = document.createElement("p");
  counter.className = "group-title";
  counter.textContent = t("client.step", { n: clientDraft.step + 1, total: CLIENT_STEPS.length });
  el.sheetBody.appendChild(counter);

  const title = document.createElement("h3");
  title.className = "step-title";
  title.textContent = t(step.key + ".title");
  el.sheetBody.appendChild(title);

  const body = document.createElement("p");
  body.className = "step-body";
  body.textContent = t(step.key + ".body");
  el.sheetBody.appendChild(body);

  if (step.url !== null) {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "btn btn-primary group-action";
    open.textContent = t("client.openPage");
    open.addEventListener("click", () => openSetupPage(step.url));
    el.sheetBody.appendChild(open);
  } else {
    const clientId = textField("client-id", t("account.clientId"), "");
    const clientSecret = textField("client-secret", t("account.clientSecret"), "", "password");
    clientId.input.value = clientDraft.id || state.info.google_client_id || "";
    clientSecret.input.value = clientDraft.secret;
    clientId.input.addEventListener("input", () => {
      clientDraft.id = clientId.input.value;
    });
    clientSecret.input.addEventListener("input", () => {
      clientDraft.secret = clientSecret.input.value;
    });
    el.sheetBody.appendChild(clientId.wrap);
    el.sheetBody.appendChild(clientSecret.wrap);
  }

  const stuck = document.createElement("p");
  stuck.className = "group-hint";
  stuck.textContent = t("client.stuck");
  el.sheetBody.appendChild(stuck);
  const guide = document.createElement("button");
  guide.type = "button";
  guide.className = "linklike";
  guide.textContent = t("account.clientGuide");
  guide.addEventListener("click", () => openSetupPage(4));
  el.sheetBody.appendChild(guide);

  el.sheetFoot.appendChild(
    button(clientDraft.step === 0 ? t("action.cancel") : t("client.back"), "", () => {
      if (clientDraft.step === 0) {
        closeSheet();
        return;
      }
      clientDraft.step -= 1;
      openClientSheet(false);
    })
  );

  if (clientDraft.step < CLIENT_STEPS.length - 1) {
    el.sheetFoot.appendChild(
      button(t("client.next"), "btn-primary", () => {
        clientDraft.step += 1;
        openClientSheet(false);
      })
    );
  } else {
    if (state.info.google_client_id) {
      el.sheetFoot.appendChild(
        button(t("client.clear"), "btn-danger", async () => {
          try {
            await api("prefs_set", { google_client_id: "", google_client_secret: "" });
            state.info.google_client_id = "";
            clientDraft.id = "";
            clientDraft.secret = "";
            toast(t("client.cleared"));
            closeSheet();
          } catch (error) {
            toast(error.message, true);
          }
        })
      );
    }
    el.sheetFoot.appendChild(
      button(t("action.done"), "btn-primary", async () => {
        try {
          await api("prefs_set", {
            google_client_id: clientDraft.id.trim(),
            google_client_secret: clientDraft.secret.trim(),
          });
          state.info.google_client_id = clientDraft.id.trim();
          clientDraft.secret = "";
          toast(t("client.saved"));
          closeSheet();
        } catch (error) {
          toast(error.message, true);
        }
      })
    );
  }
}

function waitForConnection(accountName) {
  el.sheetBody.textContent = "";
  el.sheetFoot.textContent = "";
  el.sheetClose.disabled = true;

  const waiting = notice("info", "i-cloud", t("account.waitingDetail"));
  const heading = document.createElement("h3");
  heading.className = "group-title";
  heading.textContent = t("account.waiting");
  el.sheetBody.appendChild(heading);
  el.sheetBody.appendChild(waiting);

  const cancel = button(t("action.cancel"), "", async () => {
    cancel.disabled = true;
    await api("account_cancel", {}).catch(() => {});
  });
  el.sheetFoot.appendChild(cancel);

  const poll = async () => {
    let status;
    try {
      status = await api("account_status", {});
    } catch (error) {
      status = { stage: "error", error: error.message };
    }
    if (status.stage === "starting" || status.stage === "browser") {
      setTimeout(poll, 1200);
      return;
    }
    el.sheetClose.disabled = false;
    cancel.remove();
    waiting.remove();
    heading.remove();
    if (status.stage === "done") {
      el.sheetBody.appendChild(notice("ok", "i-check", t("account.done", { name: accountName })));
      el.sheetFoot.appendChild(button(t("action.done"), "btn-primary", closeSheet));
      loadState();
    } else if (status.stage === "cancelled") {
      closeSheet();
    } else {
      el.sheetBody.appendChild(
        notice("danger", "i-alert", t("account.failed", { error: status.error || "" }))
      );
      el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
    }
  };
  poll();
}

async function forgetAccount(accountName) {
  openSheet(t("account.forget"));
  el.sheetBody.appendChild(notice("danger", "i-alert", t("account.forgetConfirm", { name: accountName })));
  el.sheetFoot.appendChild(button(t("action.cancel"), "", closeSheet));
  el.sheetFoot.appendChild(
    button(t("account.forget"), "btn-danger", async () => {
      try {
        await api("account_forget", { name: accountName, confirm: "FORGET" });
        toast(t("account.forgotten", { name: accountName }));
        closeSheet();
        Object.keys(el.panes).forEach((side) => {
          if (state.panes[side].remote === accountName) {
            state.panes[side].remote = null;
            state.panes[side].path = "";
          }
        });
        loadState();
      } catch (error) {
        toast(error.message, true);
      }
    })
  );
}

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

function buildTransferBody(context, isMove, dryRun, serverSide) {
  openSheet(t(isMove ? "sheet.moveN" : "sheet.copyN", { n: context.names.length }));
  el.sheetBody.appendChild(routeSummary(context.fromSide, context.toSide));
  if (dryRun) el.sheetBody.appendChild(notice("info", "i-check", t("notice.dryRun")));
  if (isMove && !dryRun) el.sheetBody.appendChild(notice("warn", "i-alert", t("notice.moveSteps")));
  if (serverSide) el.sheetBody.appendChild(notice("info", "i-cloud", t("notice.serverSide")));
  // The destination listing is already on screen, so saying what is about to be
  // replaced costs nothing and answers the question before it is asked.
  const clashes = context.names.filter((name) =>
    (context.to.entries || []).some((entry) => entry.name === name)
  );
  if (clashes.length && !dryRun) {
    el.sheetBody.appendChild(
      notice("info", "i-refresh", t("notice.overwrite", { n: clashes.length, total: context.names.length }))
    );
  }
  el.sheetBody.appendChild(itemList(context.names, context.dirs));
}

function openTransferSheet() {
  const context = selectionContext();
  if (!context.names.length || !context.to.remote) return;

  const isMove = state.mode === "move";
  const dryRun = el.dry.checked;
  const serverSide = el.serverSide.checked;
  buildTransferBody(context, isMove, dryRun, serverSide);

  const label = dryRun
    ? t("btn.runDryRun")
    : isMove
      ? t("btn.startCopy")
      : t("btn.copyN", { n: context.names.length });

  el.sheetFoot.appendChild(button(t("action.cancel"), "", closeSheet));
  el.sheetFoot.appendChild(
    button(label, isMove && !dryRun ? "btn-warn" : "btn-primary", () =>
      startTransfer(context, isMove, dryRun, serverSide)
    )
  );
}

async function startTransfer(context, isMove, dryRun, serverSide) {
  el.sheetFoot.textContent = "";
  el.sheetClose.disabled = true;
  const progress = progressBlock();
  el.sheetBody.insertBefore(progress, el.sheetBody.querySelector(".itemlist"));
  const fill = progress.querySelector(".progress-fill");
  const doneLabel = progress.querySelector(".progress-done");
  const speedLabel = progress.querySelector(".progress-speed");
  const etaLabel = progress.querySelector(".progress-eta");

  try {
    await api("transfer", {
      src_remote: context.from.remote,
      src_path: context.from.path,
      dst_remote: context.to.remote,
      dst_path: context.to.path,
      names: context.names,
      dirs: Array.from(context.dirs),
      dry_run: dryRun,
      server_side: serverSide,
    });
  } catch (error) {
    el.sheetClose.disabled = false;
    el.sheetBody.appendChild(notice("danger", "i-alert", error.message));
    el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
    return;
  }

  setStatusKey("status.transferring", {}, "busy");

  const cancel = button(t("btn.cancelTransfer"), "btn-quiet-danger", async () => {
    cancel.disabled = true;
    cancel.textContent = t("toast.cancelling");
    await api("transfer_cancel", {}).catch(() => {});
  });
  el.sheetFoot.appendChild(cancel);

  const ITEM_LABELS = {
    waiting: "item.waiting",
    running: "item.transferring",
    done: "item.done",
    failed: "item.failed",
    cancelled: "item.cancelledItem",
  };

  const poll = async () => {
    let status;
    try {
      status = await api("transfer_status", {});
    } catch (error) {
      el.sheetBody.appendChild(notice("danger", "i-alert", error.message));
      el.sheetClose.disabled = false;
      return;
    }

    const stats = status.stats || {};
    /* The whole job is sized before anything moves, so once measuring is over
       the denominator never changes and the bar cannot lurch. */
    const measuring = status.stage === "measuring" || !status.total_bytes;
    progress.classList.toggle("is-preparing", measuring);
    if (measuring) {
      doneLabel.textContent = t("progress.preparing", { size: formatBytes(status.measured || 0) });
      speedLabel.textContent = "";
      etaLabel.textContent = "";
    } else {
      const moved = stats.bytes || 0;
      const ratio = status.total_bytes ? Math.min(1, moved / status.total_bytes) : 0;
      fill.setAttribute("width", String((ratio * 100).toFixed(2)));
      doneLabel.textContent = t("progress.of", {
        done: formatBytes(moved),
        total: formatBytes(status.total_bytes),
      });
      speedLabel.textContent = formatSpeed(stats.speed);
      const remaining = Math.max(0, (status.total_bytes || 0) - moved);
      etaLabel.textContent = t("progress.eta", {
        eta: stats.speed > 0 ? formatEta(remaining / stats.speed) : "-",
      });
    }

    (status.items || []).forEach((entry) => {
      const item = el.sheetBody.querySelector('.item[data-name="' + CSS.escape(entry.name) + '"]');
      if (!item) return;
      const label = item.querySelector(".item-state");
      label.textContent = t(ITEM_LABELS[entry.state] || "item.waiting");
      item.classList.toggle("is-ok", entry.state === "done");
      item.classList.toggle("is-failed", entry.state === "failed");
      if (entry.error) item.title = entry.error;
    });

    const settled = ["done", "failed", "cancelled"].includes(status.stage);
    if (!settled) {
      setTimeout(poll, POLL_MS);
      return;
    }

    cancel.remove();
    el.sheetClose.disabled = false;
    const failures = (status.items || []).filter((entry) => entry.state === "failed");
    fill.classList.toggle("is-done", status.stage === "done");
    fill.classList.toggle("is-failed", failures.length > 0);
    setStatusKey("status.idle", {}, "ok");
    refreshPane(context.toSide, { refresh: true });

    if (status.stage === "cancelled") {
      el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
      return;
    }

    if (failures.length || status.error) {
      el.sheetBody.appendChild(
        notice("danger", "i-alert", t("notice.failed", {
          n: failures.length || 1,
          error: (failures[0] && failures[0].error) || status.error,
        }))
      );
      /* Google answers a cross-account server-side copy with a 404 on the
         source file, because the request carries the destination account's
         credentials and that account cannot see it. Offer the way out. */
      const firstError = (failures[0] && failures[0].error) || status.error || "";
      const refused = serverSide && /notFound|404/i.test(firstError);
      if (refused) {
        el.sheetBody.appendChild(notice("warn", "i-cloud", t("notice.serverSideFailed")));
        el.sheetFoot.appendChild(
          button(t("btn.retryDirect"), "btn-primary", () => {
            el.serverSide.checked = false;
            buildTransferBody(context, isMove, dryRun, false);
            startTransfer(context, isMove, dryRun, false);
          })
        );
      }
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
        refreshPane(context.fromSide, { refresh: true });
      } catch (error) {
        toast(error.message, true);
      }
    })
  );
}

/* ---------- comparing without transferring ---------- */

async function openCompareSheet() {
  const context = selectionContext();
  if (!context.names.length || !context.to.remote) return;

  openSheet(t("compare.title", { n: context.names.length }));
  el.sheetBody.appendChild(routeSummary(context.fromSide, context.toSide));
  const hint = document.createElement("p");
  hint.className = "group-hint";
  hint.textContent = t("compare.hint");
  el.sheetBody.appendChild(hint);
  const pending = notice("info", "i-refresh", t("compare.checking"));
  el.sheetBody.appendChild(pending);
  el.sheetClose.disabled = true;

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
    el.sheetClose.disabled = false;
    pending.remove();
    el.sheetBody.appendChild(notice("danger", "i-alert", error.message));
    el.sheetFoot.appendChild(button(t("action.close"), "", closeSheet));
    return;
  }

  el.sheetClose.disabled = false;
  pending.remove();

  const list = document.createElement("ul");
  list.className = "itemlist";
  result.results.forEach((entry) => {
    const item = document.createElement("li");
    item.className = "item " + (entry.ok ? "is-ok" : "is-failed");
    item.appendChild(icon(context.dirs.has(entry.name) ? "i-folder" : "i-file"));
    const label = document.createElement("span");
    label.className = "item-name";
    label.textContent = entry.name;
    const detail = document.createElement("span");
    detail.className = "item-state";
    if (!entry.src || !entry.dst) {
      detail.textContent = t("compare.absent");
    } else {
      detail.textContent = t("compare.sideBySide", {
        srcCount: entry.src.count,
        srcSize: formatBytes(entry.src.bytes),
        dstCount: entry.dst.count,
        dstSize: formatBytes(entry.dst.bytes),
        n: entry.src.count,
        count: entry.dst.count,
      });
    }
    item.appendChild(label);
    item.appendChild(detail);
    list.appendChild(item);
  });
  el.sheetBody.appendChild(list);
  el.sheetBody.appendChild(
    result.ok
      ? notice("ok", "i-check", t("compare.same"))
      : notice("danger", "i-alert", t("compare.differs"))
  );
  el.sheetFoot.appendChild(button(t("action.done"), "btn-primary", closeSheet));
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

el.statusAction.addEventListener("click", () => openClientSheet());
el.go.addEventListener("click", openTransferSheet);
el.compare.addEventListener("click", openCompareSheet);

/* ---------- boot ---------- */

applyTheme();
applyTranslations();
markPlatform();
loadState();
