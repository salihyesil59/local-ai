// Local AI UI — vanilla JS, no external dependencies (works offline).
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") { node.innerHTML = v; node.classList.add("md"); enhance(node); } // only for server-rendered (escaped) Markdown
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c.nodeType ? c : document.createTextNode(String(c)));
  return node;
};
const api = async (path, opts = {}) => {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};

// runs: id -> summary of every chat; currentRun: the chat that is open (null = a new chat is being written)
const state = { status: {}, view: "new", runs: {}, runList: [], currentRun: null, running: false, source: null, project: null, mode: "research", folders: [], targetFolder: null, images: [] };

// Remembered per browser; storage may be unavailable, the page works without it.
const remember = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
const recall = (key) => { try { return localStorage.getItem(key) || ""; } catch { return ""; } };

// -- icons: one small set of line icons, drawn inline -------------------------------------------
const ICONS = {
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.8 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.8-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.8H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 2.8-1.2V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.8 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.8h.1a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  dots: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 21V5M19 19v2H6"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  pin: '<path d="M12 17v5M8 3h8l-1 6 3 4H6l3-4z"/>',
  right: '<path d="m9 6 6 6-6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  contrast: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18a9 9 0 0 0 0-18z" fill="currentColor"/>',
};

function icon(name) {
  const node = el("span", { class: "ic", "aria-hidden": "true" });
  node.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[name]}</svg>`; // constant markup from the table above
  return node;
}

// -- theme and language: chosen in the top bar, remembered in this browser ----------------------
const THEMES = ["auto", "light", "dark"];
const THEME_ICON = { auto: "contrast", light: "sun", dark: "moon" };

function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme; // follows the system
  else document.documentElement.dataset.theme = theme;
  $("#theme").replaceChildren(icon(THEME_ICON[theme]));
  $("#theme").title = `${t("Theme")}: ${t(theme)}`;
}

const currentTheme = () => (THEMES.includes(recall("theme")) ? recall("theme") : "auto");

function nextTheme() {
  const theme = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
  remember("theme", theme);
  applyTheme(theme);
}

function switchLanguage() {
  remember("lang", LANG === "tr" ? "en" : "tr");
  location.reload(); // every text of the page is written again
}

for (const tab of document.querySelectorAll(".tab[data-icon]")) tab.prepend(icon(tab.dataset.icon));
$("#cancel").append(icon("stop"));
$("#theme").addEventListener("click", nextTheme);
$("#lang").addEventListener("click", switchLanguage);
$("#lang").textContent = LANG.toUpperCase();
$("#lang").title = t("Language");

// -- the widths of the sidebar and the progress panel: dragged at their edge, remembered in this browser ----
const SIDEBAR = { min: 200, max: 640, initial: 280 };
const PANEL = { min: 260, max: 900, initial: 320 };

function setSidebar(width, save = true) {
  width = Math.round(Math.max(SIDEBAR.min, Math.min(width, SIDEBAR.max, window.innerWidth - 360)));
  $(".layout").style.setProperty("--sidebar", `${width}px`);
  $("#resizer").setAttribute("aria-valuenow", width);
  if (save) remember("sidebar", String(width));
}

function setPanel(width, save = true) {
  const sidebar = parseInt($(".layout").style.getPropertyValue("--sidebar"), 10) || SIDEBAR.initial;
  width = Math.round(Math.max(PANEL.min, Math.min(width, PANEL.max, window.innerWidth - sidebar - 420)));
  $("#view-new").style.setProperty("--panel", `${width}px`);
  $("#panel-resizer").setAttribute("aria-valuenow", width);
  if (save) remember("panel", String(width));
}

// handle: the drag handle; set(width, save); width(pointer x) while dragging; current(); grow: +1 when the
// right arrow key widens, -1 when it narrows (the panel's edge is on its left).
function resizable(handle, { set, width, current, initial, grow }) {
  handle.addEventListener("pointerdown", (ev) => {
    handle.setPointerCapture(ev.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
    const move = (e) => set(width(e.clientX), false);
    const stop = () => {
      handle.removeEventListener("pointermove", move);
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing");
      set(current());
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop, { once: true });
    handle.addEventListener("pointercancel", stop, { once: true });
  });
  handle.addEventListener("dblclick", () => set(initial));
  handle.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    set(current() + (ev.key === "ArrowRight" ? 16 : -16) * grow);
  });
}

resizable($("#resizer"), {
  set: setSidebar,
  width: (x) => x - $(".layout").getBoundingClientRect().left,
  current: () => parseInt($(".layout").style.getPropertyValue("--sidebar"), 10) || SIDEBAR.initial,
  initial: SIDEBAR.initial,
  grow: 1,
});
resizable($("#panel-resizer"), {
  set: setPanel,
  width: (x) => $("#view-new").getBoundingClientRect().right - x,
  current: () => parseInt($("#view-new").style.getPropertyValue("--panel"), 10) || PANEL.initial,
  initial: PANEL.initial,
  grow: -1,
});
if (+recall("sidebar")) setSidebar(+recall("sidebar"), false);
if (+recall("panel")) setPanel(+recall("panel"), false);

// -- status -----------------------------------------------------------------------
async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const s = state.status;
    const box = $("#status");
    box.classList.toggle("err", !!s.error);
    box.textContent = s.error ? s.error : `${s.model || t("model: auto")} · ${s.servers.join(", ") || t("no servers")} · ${s.tools} ${t("tools")}`;
    box.title = `Workspace: ${s.workspace}\nLM Studio: ${s.base_url}`;
    renderMcpSelectors();
  } catch (e) {
    $("#status").textContent = t("server not reachable");
    $("#status").classList.add("err");
  }
}

// -- MCP servers for a message: a menu at the message box (the servers this message may use) --------
// Remembered per mode. The notebook belongs to the research agent and the coder to the coding agent: not offered.
function offeredServers() {
  return (state.status.servers || []).filter((s) => s !== "notebook" && s !== "coder");
}

function chosenServers() {
  let saved = null;
  try { saved = JSON.parse(recall("mcp_servers_" + state.mode) || "null"); } catch {}
  const offered = offeredServers();
  if (Array.isArray(saved)) return offered.filter((s) => saved.includes(s));
  return state.mode === "chat" ? [] : offered; // until the user chooses: a chat has no tools, the agents all
}

function getSelectedMcpServers() {
  return chosenServers();
}

function switchRow(label, on, change, detail) {
  const box = el("input", { type: "checkbox", role: "switch", checked: on, onchange: (ev) => change(ev.target.checked) });
  return el("label", { class: "switch-row" }, el("span", { class: "switch-text" }, label, detail ? el("small", { class: "muted" }, detail) : null),
    el("span", { class: "switch" }, box, el("span", { class: "knob" })));
}

function renderMcpSelectors() {
  const offered = offeredServers(), chosen = chosenServers();
  $("#tools-toggle").replaceChildren(icon("plug"), ` ${t("Tools")}`,
    offered.length ? el("span", { class: "count" }, `${chosen.length}/${offered.length}`) : null);
  const counts = state.toolCounts || {};
  $("#tools-menu").replaceChildren(
    el("div", { class: "menu-head" }, t("Servers this message may use")),
    ...(offered.length ? offered.map((s) => switchRow(s, chosen.includes(s), (on) => {
      const next = on ? [...chosenServers(), s] : chosenServers().filter((x) => x !== s);
      remember("mcp_servers_" + state.mode, JSON.stringify(next));
      renderMcpSelectors();
    }, counts[s] != null ? `${counts[s]} ${t("tools")}` : "")) : [el("p", { class: "muted" }, t("No servers are running."))]),
    el("button", { class: "ghost small", type: "button", onclick: () => { toggleToolsMenu(false); goTo("tools"); } }, t("Manage servers…")));
}

function toggleToolsMenu(open) {
  const menu = $("#tools-menu");
  menu.hidden = open === undefined ? !menu.hidden : !open;
  $("#tools-toggle").setAttribute("aria-expanded", String(!menu.hidden));
  if (!menu.hidden) loadToolCounts();
}

async function loadToolCounts() {
  const data = await api("/api/mcp").catch(() => null);
  if (!data) return;
  state.toolCounts = Object.fromEntries(data.servers.map((s) => [s.name, s.tools.length]));
  renderMcpSelectors();
}

$("#tools-toggle").addEventListener("click", () => toggleToolsMenu());
document.addEventListener("click", (ev) => { if (!ev.target.closest("#tools-menu, #tools-toggle")) toggleToolsMenu(false); });

// -- the Tools view: the MCP servers, with their tools; add, switch off, restart, remove ----------
async function loadTools() {
  const data = await api("/api/mcp").catch(() => null);
  if (!data) { $("#mcp-list").replaceChildren(el("p", { class: "muted" }, t("server not reachable"))); return; }
  $("#mcp-add").hidden = !data.editable;
  $("#mcp-config").textContent = data.editable ? `${t("Saved in")} ${data.config}` : t("This UI was started without a server list to change.");
  const change = async (name, options) => {
    try {
      await api(`/api/mcp/${encodeURIComponent(name)}`, options);
    } catch (e) { fail(e); }
    await Promise.all([loadTools(), loadStatus()]);
  };
  $("#mcp-list").replaceChildren(...(data.servers.length ? data.servers.map((s) => {
    const state_ = s.disabled ? "off" : s.connected ? "done" : "failed";
    const label = s.disabled ? t("switched off") : s.connected ? `${s.tools.length} ${t("tools")}` : t("not running");
    return el("div", { class: "mcp-card" + (s.connected ? "" : " idle") },
      el("div", { class: "mcp-head" },
        el("span", { class: `dot ${state_}` }), el("strong", {}, s.name), el("span", { class: "muted" }, label), el("span", { class: "spacer" }),
        data.editable && !s.required ? el("label", { class: "switch", title: t("On or off") },
          el("input", { type: "checkbox", role: "switch", checked: !s.disabled, "aria-label": `${s.name}: ${t("On or off")}`,
            onchange: (ev) => change(s.name, { method: "PATCH", body: JSON.stringify({ disabled: !ev.target.checked }) }) }), el("span", { class: "knob" }))
          : s.required ? el("span", { class: "badge", title: t("The research agent keeps its plans, findings, memory and reports in this server, so it cannot be switched off or removed.") }, t("always on")) : null,
        data.editable && !s.disabled ? el("button", { class: "ghost small", type: "button", onclick: () => change(s.name, { method: "PATCH", body: "{}" }) }, t("Restart")) : null,
        data.editable && !s.required ? el("button", { class: "ghost small danger", type: "button", onclick: async () => {
          if (await confirmDialog(t('Remove the server "{name}" from the list?', { name: s.name }), t("Remove"))) change(s.name, { method: "DELETE" });
        } }, t("Remove")) : null),
      el("code", { class: "mcp-command" }, s.command || s.url, s.env.length ? `   (${s.env.join(", ")})` : ""),
      s.error ? el("div", { class: "log warning" }, s.error) : null,
      s.tools.length ? el("details", {}, el("summary", {}, `${t("Tools")} (${s.tools.length})`),
        el("ul", { class: "tool-list" }, s.tools.map((tool) => el("li", {}, el("code", {}, tool.name), tool.description ? ` — ${tool.description}` : "")))) : null);
  }) : [el("p", { class: "muted" }, t("No servers yet."))]));
}

document.querySelectorAll("#mcp-add .mode").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#mcp-add .mode").forEach((x) => x.classList.toggle("active", x === b));
  $("#mcp-add .add-form").hidden = b.dataset.add !== "form";
  $("#mcp-add .add-json").hidden = b.dataset.add !== "json";
}));

$("#mcp-add").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const pasted = !$("#mcp-add .add-json").hidden;
  const target = $("#mcp-command").value.trim();
  const env = Object.fromEntries($("#mcp-env").value.split("\n").map((line) => line.trim()).filter((line) => line.includes("="))
    .map((line) => [line.slice(0, line.indexOf("=")).trim(), line.slice(line.indexOf("=") + 1).trim()]));
  const body = pasted ? { name: $("#mcp-name").value.trim(), json: $("#mcp-json").value }
    : { name: $("#mcp-name").value.trim(), env, ...(/^https?:\/\//.test(target) ? { url: target } : { command: target }) };
  const button = ev.submitter || $("#mcp-add .primary");
  button.disabled = true;
  try {
    const added = await api("/api/mcp", { method: "POST", body: JSON.stringify(body) });
    if (added.connected) toast(t('"{name}" is running with {n} tools.', { name: added.name, n: added.tools.length }));
    else toast(t('"{name}" was saved but did not start: {error}', { name: added.name, error: added.error }), "error");
    for (const id of ["#mcp-name", "#mcp-command", "#mcp-env", "#mcp-json"]) $(id).value = "";
  } catch (e) {
    fail(e);
  } finally {
    button.disabled = false;
  }
  await Promise.all([loadTools(), loadStatus()]);
});

function fileUrl(path) {
  const ws = state.status.workspace || "";
  return path && path.startsWith(ws) ? "/files/" + path.slice(ws.length + 1).split("/").map(encodeURIComponent).join("/") : null;
}

// -- navigation ---------------------------------------------------------------------
function show(view) {
  state.view = view;
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== `view-${view}`;
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.view === view || (view === "project" && t.dataset.view === "projects"));
}

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    const v = tab.dataset.view;
    if (v === "new") { show("new"); renderChats(); renderRunsSidebar(); }
    if (v === "projects") {
      renderProjectsSidebar();
      if (state.project) openProject(state.project);
      else { show("project"); $("#view-project").replaceChildren(el("p", { class: "muted" }, t("Pick a project on the left."))); }
    }
    if (v === "memory") { show("memory"); $("#side-list").replaceChildren(); loadMemory(); }
    if (v === "skills") { show("skills"); $("#side-list").replaceChildren(); loadSkills(); }
    if (v === "tools") { show("tools"); $("#side-list").replaceChildren(); loadTools(); }
    if (v === "offline") { show("offline"); $("#side-list").replaceChildren(); loadOffline(); }
  })
);

// -- Offline: notebooks, library files and ZIM archives --------------------------------------------
const megabytes = (n) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : n >= 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(1)} MB`
  : `${Math.max(1, Math.round(n / 1024))} KB`);
state.notebooks = [];

// The notebook chooser of the message box: the notebooks of the library, the chosen one kept while it exists.
function renderNotebookChooser() {
  const box = $("#notebook"), chosen = box.value;
  box.replaceChildren(el("option", { value: "" }, t("No notebook")),
    ...state.notebooks.map((n) => el("option", { value: n.name }, `${t("Notebook")}: ${n.name} (${n.files.length})`)));
  box.value = state.notebooks.some((n) => n.name === chosen) ? chosen : "";
  box.classList.toggle("chosen", !!box.value);
}
function setNotebook(name) {
  if (name && !state.notebooks.some((n) => n.name === name)) state.notebooks.push({ name, files: [] }); // until the list loads
  renderNotebookChooser();
  $("#notebook").value = name;
  $("#notebook").classList.toggle("chosen", !!name);
}
$("#notebook").addEventListener("change", () => $("#notebook").classList.toggle("chosen", !!$("#notebook").value));

async function upload(files, folder) {
  for (const file of files) {
    try {
      const res = await fetch(`/api/offline/files?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(file.name)}`,
        { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      toast(t("Added: {name}", { name: data.path }));
    } catch (e) { fail(e); }
  }
  loadOffline();
}

// A place files can be dropped onto (and chosen for) that uploads them into folder.
function dropTarget(zone, folder, input) {
  zone.addEventListener("dragover", (ev) => { if ([...ev.dataTransfer.types].includes("Files")) { ev.preventDefault(); zone.classList.add("dropping"); } });
  zone.addEventListener("dragleave", () => zone.classList.remove("dropping"));
  zone.addEventListener("drop", (ev) => { ev.preventDefault(); zone.classList.remove("dropping"); upload([...ev.dataTransfer.files], folder()); });
  if (input) input.addEventListener("change", () => { upload([...input.files], folder()); input.value = ""; });
}

function fileRow(f, label = f.path) {
  const remove = el("button", { class: "ghost small danger", type: "button", title: t("Delete"), "aria-label": t("Delete") }, icon("x"));
  remove.addEventListener("click", async () => {
    if (!(await confirmDialog(t('Delete "{name}" from the library?', { name: f.path })))) return;
    try { await api(`/api/offline/files?path=${encodeURIComponent(f.path)}`, { method: "DELETE" }); } catch (e) { fail(e); }
    loadOffline();
  });
  return el("div", { class: "file-row" }, icon("file"), el("span", { class: "name", title: f.path }, label),
    el("span", { class: "muted" }, megabytes(f.size)),
    el("span", { class: `badge ${f.indexed ? "fact" : ""}`, title: f.indexed ? t("In the search index") : t("Indexed on the next search") }, f.indexed ? t("indexed") : t("new")),
    remove);
}

function renderNotebooks(notebooks) {
  $("#notebook-list").replaceChildren(...(notebooks.length ? notebooks.map((n) => {
    const input = el("input", { type: "file", accept: ".pdf,.md,.txt", multiple: true, hidden: true });
    const add = el("button", { class: "ghost small", type: "button" }, icon("plus"), ` ${t("Add files")}`);
    add.addEventListener("click", () => input.click());
    const remove = el("button", { class: "ghost small danger", type: "button" }, t("Delete"));
    remove.addEventListener("click", async () => {
      if (!(await confirmDialog(t('Delete the notebook "{name}" and its {n} files?', { name: n.name, n: n.files.length })))) return;
      try { await api(`/api/offline/notebooks/${encodeURIComponent(n.name)}`, { method: "DELETE" }); } catch (e) { fail(e); }
      loadOffline();
    });
    const card = el("div", { class: "notebook drop" },
      el("div", { class: "mcp-head" }, icon("book"), el("strong", {}, n.name), el("span", { class: "muted" }, `${n.files.length} ${t("files")}`),
        el("span", { class: "spacer" }), add, remove, input),
      n.files.length ? el("div", { class: "file-rows" }, n.files.map((f) => fileRow(f, f.path.split("/").slice(2).join("/"))))
        : el("p", { class: "muted note" }, t("Drop files here or use Add files.")));
    dropTarget(card, () => `notebooks/${n.name}`, input);
    return card;
  }) : [el("p", { class: "muted" }, t("No notebooks yet."))]));
}

function renderZims(zim, job) {
  $("#zim-dirs").textContent = `${t("Folders")}: ${zim.dirs.join(" · ")}`;
  $("#zim-list").replaceChildren(...(zim.archives.length ? zim.archives.map((a) =>
    el("div", { class: "file-row" }, icon("layers"), el("span", { class: "name", title: a.file }, a.name), el("span", { class: "muted" }, a.date || ""),
      el("span", { class: "muted" }, megabytes(a.size)))) : [el("p", { class: "muted" }, t("No ZIM archives in these folders."))]));
  const box = $("#zim-job");
  if (job.running) {
    const pct = job.total ? Math.round((100 * job.done) / job.total) : 0;
    const cancel = el("button", { class: "ghost small", type: "button" }, t("Cancel"));
    cancel.addEventListener("click", () => api("/api/offline/zim/cancel", { method: "POST" }).catch(fail));
    box.replaceChildren(el("div", { class: "row" }, el("span", {}, `${t("Downloading")} ${job.file}: ${megabytes(job.done)} / ${megabytes(job.total)}`), cancel),
      el("div", { class: "bar" }, el("span", { style: `width:${pct}%` })));
    clearTimeout(state.zimPoll);
    state.zimPoll = setTimeout(() => state.view === "offline" && loadOffline(), 2000);
  } else {
    box.replaceChildren(...(job.log || []).map((line) => el("div", { class: "log" }, line)));
  }
}

$("#zim-check").addEventListener("click", async () => {
  const button = $("#zim-check"), box = $("#zim-updates");
  button.disabled = true;
  box.replaceChildren(el("p", { class: "muted" }, t("Asking the Kiwix catalog…")));
  try {
    const updates = await api("/api/offline/zim/updates");
    if (!updates.length) { box.replaceChildren(el("p", { class: "log success" }, t("All archives are up to date."))); return; }
    const boxes = updates.map((u) => el("input", { type: "checkbox", checked: true, value: u.file }));
    const keep = el("input", { type: "checkbox" });
    const go = el("button", { class: "primary", type: "button" }, t("Download the updates"));
    go.addEventListener("click", async () => {
      try {
        await api("/api/offline/zim/update", { method: "POST", body: JSON.stringify({ files: boxes.filter((b) => b.checked).map((b) => b.value), keep_old: keep.checked }) });
        box.replaceChildren();
        loadOffline();
      } catch (e) { fail(e); }
    });
    box.replaceChildren(...updates.map((u, i) => el("label", { class: "file-row" }, boxes[i], el("span", { class: "name" }, `${u.file} → ${u.new_file}`), el("span", { class: "muted" }, megabytes(u.size)))),
      el("div", { class: "row" }, go, el("label", { class: "opt" }, keep, ` ${t("Keep the old files")}`)));
  } catch (e) {
    box.replaceChildren(el("p", { class: "log warning" }, e.message));
  } finally { button.disabled = false; }
});

$("#notebook-add").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const { name } = await api("/api/offline/notebooks", { method: "POST", body: JSON.stringify({ name: $("#notebook-name").value }) });
    $("#notebook-name").value = "";
    toast(t("Notebook created: {name}", { name }));
    loadOffline();
  } catch (e) { fail(e); }
});

$("#library-pick").addEventListener("click", () => $("#library-input").click());
dropTarget($("#library-drop"), () => "", $("#library-input"));

async function loadOffline() {
  try {
    const data = await api("/api/offline");
    state.notebooks = data.library.notebooks;
    renderNotebookChooser();
    if (state.view !== "offline") return;
    const lib = data.library;
    $("#library-dir").textContent = `${t("Folder")}: ${lib.dir}${lib.exists ? "" : ` (${t("created with the first file")})`}`;
    renderNotebooks(lib.notebooks);
    $("#library-files").replaceChildren(...(lib.files.length ? lib.files.map((f) => fileRow(f)) : [el("p", { class: "muted" }, t("No files yet."))]),
      ...(lib.more ? [el("p", { class: "muted note" }, t("{n} more files not shown", { n: lib.more }))] : []));
    renderZims(data.zim, data.job);
  } catch (e) { fail(e); }
}

// -- small dialogs: a toast, a confirmation, a text edited in place ----------------------------
function toast(message, kind = "info") {
  const node = el("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, message);
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 8000 : 3500);
}
const fail = (e) => toast(e.message, "error");

// Ask before something that cannot be undone; resolves to true when confirmed.
function confirmDialog(text, okLabel = t("Delete")) {
  return new Promise((resolve) => {
    let answered = false;
    const answer = (ok) => {
      if (answered) return;
      answered = true;
      dialog.close();
      dialog.remove();
      resolve(ok);
    };
    const dialog = el("dialog", { class: "dialog" }, el("p", {}, text),
      el("div", { class: "row end" },
        el("button", { class: "ghost", type: "button", onclick: () => answer(false) }, t("Cancel")),
        el("button", { class: "primary danger-bg", type: "button", onclick: () => answer(true) }, okLabel)));
    dialog.addEventListener("close", () => answer(false)); // closed with Escape
    document.body.append(dialog);
    dialog.showModal();
  });
}

// Edit a text where it stands: the node becomes a field; Enter or leaving it saves, Escape cancels.
function editInPlace(node, value, save, placeholder = "") {
  const input = el("input", { class: "inline-edit", value, placeholder, "aria-label": placeholder || t("Name") });
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    const text = input.value.trim();
    input.replaceWith(node);
    if (commit && text !== value) save(text);
    else renderChats();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  // the text may stand inside a button: typing and clicking in the field must not press it
  input.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); });
  input.addEventListener("keyup", (ev) => { if (ev.key === " ") ev.preventDefault(); });
  node.replaceWith(input);
  input.focus();
  input.select();
}

// -- chats in the sidebar -------------------------------------------------------------
// Chats are sorted into the user's folders, like in LM Studio or Goose; they can be renamed, pinned, searched
// and dragged into a folder.
const chatTitle = (r) => r.title || shortTitle(r.question);

function collapsedFolders() {
  try { return new Set(JSON.parse(recall("collapsed") || "[]")); } catch { return new Set(); }
}

function toggleFolder(id) {
  const set = collapsedFolders();
  set.has(id) ? set.delete(id) : set.add(id);
  remember("collapsed", JSON.stringify([...set]));
  renderChats();
}

function closeMenu() { document.querySelectorAll(".menu").forEach((m) => m.remove()); }
document.addEventListener("click", (ev) => { if (!ev.target.closest(".menu, .icon")) closeMenu(); });

function showMenu(anchor, entries) {
  closeMenu();
  const rect = anchor.getBoundingClientRect();
  const menu = el("div", { class: "menu", style: `top:${rect.bottom + 4}px;left:${Math.max(8, Math.min(rect.left, window.innerWidth - 200))}px` },
    entries.map((e) => el("button", { class: e.danger ? "danger" : "", onclick: () => { closeMenu(); e.action(); } }, e.label)));
  document.body.append(menu);
}

async function guarded(fn) {
  try { await fn(); } catch (e) { fail(e); }
  renderRunsSidebar();
}

const patchRun = (id, fields) => guarded(() => api(`/api/runs/${id}`, { method: "PATCH", body: JSON.stringify(fields) }));

function renameChat(r) {
  const title = document.querySelector(`.side-row[data-run="${r.id}"] .t`);
  if (title) editInPlace(title, chatTitle(r), (text) => patchRun(r.id, { title: text }), t("Chat title (empty = automatic)"));
}

function chatMenu(anchor, r) {
  showMenu(anchor, [
    { label: t("Rename"), action: () => renameChat(r) },
    { label: r.pinned ? t("Unpin") : t("Pin to the top"), action: () => patchRun(r.id, { pinned: !r.pinned }) },
    { label: t("Move to folder…"), action: () => showMenu(anchor, [
      ...state.folders.filter((f) => f.id !== r.folder).map((f) => ({ label: f.name, action: () => patchRun(r.id, { folder: f.id }) })),
      ...(r.folder ? [{ label: t("No folder"), action: () => patchRun(r.id, { folder: null }) }] : []),
      { label: t("New folder…"), action: () => newFolder(async (f) => api(`/api/runs/${r.id}`, { method: "PATCH", body: JSON.stringify({ folder: f.id }) })) },
    ]) },
    { label: t("Delete"), danger: true, action: async () => {
      if (await confirmDialog(`${t("Delete this chat?")}\n${chatTitle(r)}`)) guarded(async () => {
        await api(`/api/runs/${r.id}`, { method: "DELETE" });
        if (state.currentRun === r.id) newChat();
        toast("Chat deleted.");
      });
    } },
  ]);
}

function folderMenu(anchor, f) {
  showMenu(anchor, [
    { label: t("Rename"), action: () => {
      const name = document.querySelector(`.folder[data-folder="${f.id}"] .folder-label`);
      if (name) editInPlace(name, f.name, (text) => (text ? guarded(() => api(`/api/folders/${f.id}`, { method: "PATCH", body: JSON.stringify({ name: text }) })) : renderChats()), t("Folder name"));
    } },
    { label: t("Delete folder (chats stay)"), danger: true, action: async () => {
      if (await confirmDialog(t('Delete the folder "{name}"? Its chats move out of it.', { name: f.name }))) guarded(async () => {
        await api(`/api/folders/${f.id}`, { method: "DELETE" });
        if (state.targetFolder === f.id) setTarget(null);
      });
    } },
  ]);
}

// A new folder is named where it will stand, at the top of the list.
function newFolder(then) {
  if (state.view !== "new") show("new");
  renderChats();
  const slot = el("span");
  $("#chat-rows").prepend(slot);
  editInPlace(slot, "", (name) => (name ? guarded(async () => {
    const folder = await api("/api/folders", { method: "POST", body: JSON.stringify({ name }) });
    if (then) await then(folder);
  }) : renderChats()), t("Folder name"));
}

// New chats go into the target folder (set with the + of a folder).
function setTarget(id) {
  state.targetFolder = id;
  remember("target", id || "");
  renderTarget();
  renderChats();
}

function renderTarget() {
  const box = $("#target");
  const folder = state.folders.find((f) => f.id === state.targetFolder);
  if (!folder) { state.targetFolder = null; box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren(t("New chat goes into folder: "), el("strong", {}, folder.name), " ",
    el("button", { class: "icon", type: "button", title: t("No folder"), onclick: () => setTarget(null) }, icon("x")));
}

function chatRow(r) {
  return el("div", { class: "side-row" + (state.currentRun === r.id ? " active" : ""), "data-run": r.id, draggable: "true",
    ondragstart: (ev) => { ev.dataTransfer.setData("text/plain", r.id); ev.dataTransfer.effectAllowed = "move"; state.dragging = r.id; },
    ondragend: () => { state.dragging = null; } },
    el("button", { class: "side-item", onclick: () => attach(r.id), title: r.question },
      el("span", { class: "t" }, r.pinned ? icon("pin") : null, chatTitle(r)),
      el("small", {}, el("span", { class: `dot ${r.status}` }), `${t(knownMode(r.mode))} · ${t(r.status)}`)),
    el("button", { class: "icon", title: t("Rename, pin, move, delete"), onclick: (ev) => chatMenu(ev.currentTarget, r) }, icon("dots")));
}

// A place a dragged chat can be dropped on: a folder, or the head of the list for "no folder".
function dropTarget(node, folderId) {
  node.addEventListener("dragover", (ev) => { if (state.dragging) { ev.preventDefault(); node.classList.add("drop"); } });
  node.addEventListener("dragleave", () => node.classList.remove("drop"));
  node.addEventListener("drop", (ev) => {
    ev.preventDefault();
    node.classList.remove("drop");
    const run = state.runs[state.dragging];
    if (run && (run.folder || null) !== folderId) patchRun(run.id, { folder: folderId });
  });
  return node;
}

async function renderRunsSidebar() {
  const [runs, folders] = await Promise.all([api("/api/runs").catch(() => []), api("/api/folders").catch(() => [])]);
  state.folders = folders;
  state.runList = runs;
  state.runs = Object.fromEntries(runs.map((r) => [r.id, r]));
  renderTarget();
  renderChats();
}

function renderChats() {
  if (state.view !== "new" || $("#side-list .inline-edit")) return; // another tab owns the sidebar, or a name is being typed
  if (!$("#chat-rows")) {
    $("#side-list").replaceChildren(
      el("button", { class: "ghost new-chat", type: "button", onclick: newChat, title: "Alt+N" }, t("+ New chat")),
      el("input", { id: "chat-search", type: "search", placeholder: t("Search chats  ( / )"), "aria-label": t("Search chats"), oninput: renderChats }),
      el("div", { id: "chat-rows", class: "side-list" }));
  }
  const { runList: runs, folders } = state;
  const query = $("#chat-search").value.trim().toLowerCase();
  const items = [dropTarget(el("div", { class: "side-head", title: t("Drop a chat here to take it out of its folder") }, el("span", { class: "muted" }, query ? t("Found") : t("Chats")),
    el("button", { class: "ghost small", type: "button", onclick: () => newFolder() }, t("+ Folder"))), null)];
  if (query) {
    const found = runs.filter((r) => `${r.title} ${r.question} ${r.project}`.toLowerCase().includes(query));
    items.push(...found.map(chatRow));
    if (!found.length) items.push(el("p", { class: "muted" }, t("No chat matches.")));
  } else {
    const pinned = runs.filter((r) => r.pinned), rest = runs.filter((r) => !r.pinned);
    if (pinned.length) items.push(el("div", { class: "group-label muted" }, t("Pinned")), ...pinned.map(chatRow));
    const collapsed = collapsedFolders();
    for (const f of folders) {
      const inside = rest.filter((r) => r.folder === f.id);
      items.push(dropTarget(el("div", { class: "folder" + (state.targetFolder === f.id ? " target" : ""), "data-folder": f.id },
        el("button", { class: "folder-name", onclick: () => toggleFolder(f.id) }, icon(collapsed.has(f.id) ? "right" : "down"), el("span", { class: "folder-label" }, f.name), el("small", {}, ` ${inside.length}`)),
        el("button", { class: "icon", title: t("New chats go into this folder"), onclick: () => setTarget(state.targetFolder === f.id ? null : f.id) }, icon("plus")),
        el("button", { class: "icon", title: t("Folder menu"), onclick: (ev) => folderMenu(ev.currentTarget, f) }, icon("dots"))), f.id));
      if (!collapsed.has(f.id)) items.push(...inside.map((r) => el("div", { class: "indent" }, chatRow(r))));
    }
    const loose = rest.filter((r) => !folders.some((f) => f.id === r.folder));
    if ((folders.length || pinned.length) && loose.length) items.push(el("div", { class: "group-label muted" }, folders.length ? t("No folder") : t("Chats")));
    items.push(...loose.map(chatRow));
    if (!runs.length && !folders.length) items.push(el("p", { class: "muted" }, t("Your chats appear here.")));
  }
  $("#chat-rows").replaceChildren(...items);
}

// A short sidebar title: the first line of the prompt, cut at a word, like a chat title.
function shortTitle(text, max = 40) {
  const line = (text || "").split("\n").map((l) => l.trim()).find(Boolean) || "";
  if (line.length <= max) return line;
  const cut = line.slice(0, max);
  return cut.slice(0, Math.max(cut.lastIndexOf(" "), max / 2)).replace(/[\s.,;:]+$/, "") + "…";
}

// -- the composer: starts a chat, or sends a follow-up message in the open one ----------------
const MODES = ["chat", "research", "code"];
const knownMode = (mode) => (MODES.includes(mode) ? mode : "research");

function setMode(mode) {
  state.mode = knownMode(mode);
  document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  renderMcpSelectors();
  updateComposer();
}
document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => { remember("mode", b.dataset.mode); setMode(b.dataset.mode); }));

function updateComposer() {
  const code = state.mode === "code", chat = state.mode === "chat";
  const run = state.runs[state.currentRun];
  for (const n of document.querySelectorAll(".code-only, .research-only, .agent-only, .not-code, .new-only")) {
    n.hidden = (n.classList.contains("code-only") && !code) || (n.classList.contains("research-only") && state.mode !== "research")
      || (n.classList.contains("agent-only") && chat) || (n.classList.contains("not-code") && code) || (n.classList.contains("new-only") && !!run);
  }
  const info = $("#chat-info");
  info.hidden = !run;
  if (run) info.textContent = code ? `${t("Code")} · ${run.project}` : chat ? t("Chat") : t("Research · follow-up in this chat");
  const steer = state.running && !!run; // a running chat takes a message: code reads it, chat and research queue it
  const action = steer && code ? t("Steer") : run || chat ? t("Send") : code ? t("Code") : t("Research");
  $("#run").replaceChildren(icon("up"));
  $("#run").title = `${action} (Ctrl+Enter)`;
  $("#run").setAttribute("aria-label", action);
  $("#chat-title").textContent = run ? chatTitle(run) : "";
  $("#fast-opt").title = code ? t("Plan and verify, but no review at the end and no project map; shorter thinking")
    : t("Skip review, gap check and learning");
  $("#quick-opt").title = code ? t("A small mechanical change (a rename): no plan, the tests run once at the end")
    : t("Quick answer: one tool loop, no plan or report");
  $("#run").disabled = state.running && !steer;
  $("#cancel").hidden = !state.running;
  if (!state.running) $("#compact").hidden = true; // shown by the first "turn" event, which only code runs send
  const q = $("#question");
  const resume = !!run && state.mode === "research" && !state.running && run.resumable; // a stopped research can go on
  // code mode may continue an existing plan without a new task; a message of images alone needs no text
  q.required = !state.images.length && ((!code && !resume) || steer);
  q.placeholder = chat && steer ? t("Write your next message: it is answered when the current reply is done.")
    : steer && !code ? t("Write your next question: it is researched when this research is done.")
    : chat ? t("Write a message — any language. Images can be pasted or dropped here.")
    : steer ? t("Tell the agent what to change. It reads the message before its next reply.") : run
    ? (code ? t("Next instruction for this project. Leave empty to continue the plan.")
      : resume ? t("Ask something with what was found so far, or leave empty to go on with the research.") : t("Ask a follow-up question."))
    : (code ? t("What to build, change or fix. Leave empty to continue the plan in the folder.") : t("Ask a research question — any language. e.g. “Hidrojen atomunun taban durum enerjisini türet.”"));
}

function setRunning(on) {
  state.running = on;
  updateComposer();
}

$("#browse").addEventListener("click", async () => {
  const button = $("#browse");
  button.disabled = true;
  button.textContent = t("Waiting for the dialog…");
  try {
    const { path } = await api("/api/pick-folder", { method: "POST", body: JSON.stringify({ initial: $("#folder").value.trim() }) });
    if (path) { $("#folder").value = path; remember("folder", path); }
  } catch (e) {
    fail(e);
  } finally {
    button.disabled = false;
    button.textContent = t("Browse…");
  }
});

// -- images: attached with the button, pasted or dropped; made smaller here and sent with the message ------
const IMAGES = { max: 6, side: 1600, bytes: 3 * 1024 * 1024 };
const IMAGE_TYPES = /^image\/(png|jpeg|webp|gif)$/;

function renderAttachments() {
  const box = $("#attachments");
  box.hidden = !state.images.length;
  box.replaceChildren(...state.images.map((url, i) => {
    const remove = el("button", { type: "button", class: "remove", title: t("Remove"), "aria-label": t("Remove") }, icon("x"));
    remove.addEventListener("click", () => { state.images.splice(i, 1); renderAttachments(); });
    return el("div", { class: "thumb" }, el("img", { src: url, alt: t("Attached image") }), remove);
  }));
  updateComposer();
}

const readAsDataURL = (blob) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result);
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(blob);
});

// A large picture is scaled down (a screenshot keeps its text readable at 1600 px); PNG stays PNG for sharp text.
async function shrink(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, IMAGES.side / Math.max(bitmap.width, bitmap.height));
  if (scale === 1 && file.size <= IMAGES.bytes) { bitmap.close(); return readAsDataURL(file); }
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return file.type === "image/png" ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", 0.92);
}

async function addImages(files) {
  for (const file of files) {
    if (!IMAGE_TYPES.test(file.type)) { toast(t("Only PNG, JPEG, WebP or GIF images can be attached."), "error"); continue; }
    if (state.images.length >= IMAGES.max) { toast(t("At most 6 images per message."), "error"); break; }
    try { state.images.push(await shrink(file)); } catch (e) { fail(e); }
  }
  renderAttachments();
}

$("#attach").replaceChildren(icon("image"));
$("#attach").addEventListener("click", () => $("#attach-input").click());
$("#attach-input").addEventListener("change", (ev) => { addImages([...ev.target.files]); ev.target.value = ""; });
$("#question").addEventListener("paste", (ev) => {
  const files = [...(ev.clipboardData?.items || [])].filter((i) => i.kind === "file" && IMAGE_TYPES.test(i.type)).map((i) => i.getAsFile());
  if (files.length) { ev.preventDefault(); addImages(files); }
});
$("#ask").addEventListener("dragover", (ev) => {
  if (![...ev.dataTransfer.types].includes("Files")) return;
  ev.preventDefault();
  $("#ask").classList.add("dropping");
});
$("#ask").addEventListener("dragleave", (ev) => { if (!$("#ask").contains(ev.relatedTarget)) $("#ask").classList.remove("dropping"); });
$("#ask").addEventListener("drop", (ev) => {
  $("#ask").classList.remove("dropping");
  if (!ev.dataTransfer.files.length) return;
  ev.preventDefault();
  addImages([...ev.dataTransfer.files]);
});

function sent() { // the message was taken: the box is empty for the next one
  $("#question").value = "";
  resizeQuestion();
  state.images = [];
  renderAttachments();
}

$("#ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const question = $("#question").value.trim();
  const images = [...state.images];
  const empty = !question && !images.length;
  const code = state.mode === "code", chat = state.mode === "chat";
  const steer = state.running && state.currentRun;
  const resume = state.mode === "research" && state.currentRun && state.runs[state.currentRun]?.resumable;
  if ((empty && ((!code && !resume) || steer)) || (state.running && !steer)) return;
  if (steer) { // the live stream shows the message when the server has taken it
    try {
      await api(`/api/runs/${state.currentRun}/messages`, { method: "POST", body: JSON.stringify({ question, images }) });
      sent();
    } catch (e) { fail(e); }
    return;
  }
  const options = { question, images, servers: getSelectedMcpServers(), settings: getSettings() };
  if (!code && $("#notebook").value) options.notebook = $("#notebook").value;
  if (!chat) Object.assign(options, { fast: $("#fast").checked, quick: $("#quick").checked });
  if (state.mode === "research") options.parallel = +$("#parallel").value;
  try {
    let run;
    if (state.currentRun) {
      run = await api(`/api/runs/${state.currentRun}/messages`, { method: "POST", body: JSON.stringify(options) });
    } else {
      const folder = $("#folder").value.trim();
      if (code) remember("folder", folder);
      const body = code ? { ...options, mode: "code", folder } : chat ? { ...options, mode: "chat" }
        : { ...options, project: $("#project").value.trim() };
      run = await api("/api/runs", { method: "POST", body: JSON.stringify({ ...body, chat_folder: state.targetFolder }) });
    }
    sent();
    state.runs[run.id] = run;
    attach(run.id);
    renderRunsSidebar();
  } catch (e) {
    fail(e);
  }
});

$("#cancel").addEventListener("click", () => state.currentRun && api(`/api/runs/${state.currentRun}/cancel`, { method: "POST" }));

$("#compact").addEventListener("click", async () => {
  if (!state.currentRun) return;
  try {
    await api(`/api/runs/${state.currentRun}/compact`, { method: "POST" });
    append(el("div", { class: "log" }, t("Compaction requested: it happens before the next model reply.")));
  } catch (e) { fail(e); }
});

function resizeQuestion() {
  const q = $("#question");
  q.style.height = "auto";
  q.style.height = q.scrollHeight + "px";
}
$("#question").addEventListener("input", resizeQuestion);
$("#question").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) $("#ask").requestSubmit(); });

// -- settings of a chat: model, temperature and limits; empty fields use the server's defaults ----
const SETTINGS = ["model", "temperature", "max_tokens", "max_turns", "context_chars", "max_thinking_chars", "max_steps"];
const CODE_SETTINGS = ["max_turns", "context_chars", "max_thinking_chars"], RESEARCH_SETTINGS = ["max_steps"];

function getSettings() {
  const code = state.mode === "code", chat = state.mode === "chat";
  const settings = {};
  for (const key of SETTINGS) {
    // the other agent's limits; a chat has none of them (its context limit is the code chat's field, kept)
    if ((code ? RESEARCH_SETTINGS : chat ? [...RESEARCH_SETTINGS, "max_turns", "max_thinking_chars"] : CODE_SETTINGS).includes(key)) continue;
    const value = $(`#set-${key}`).value.trim();
    if (value) settings[key] = key === "model" ? value : +value;
  }
  return settings;
}

function setSettings(settings) {
  const model = $("#set-model");
  if (settings.model && ![...model.options].some((o) => o.value === settings.model)) model.append(el("option", { value: settings.model }, settings.model));
  for (const key of SETTINGS) $(`#set-${key}`).value = settings[key] ?? "";
  renderSettingsButton();
}

function recalledSettings() {
  try { return JSON.parse(recall("settings") || "{}"); } catch { return {}; }
}

function renderSettingsButton() {
  const settings = getSettings();
  const changed = Object.keys(settings).filter((k) => k !== "model").length;
  $("#settings-toggle").replaceChildren(icon("gear"), ` ${settings.model || state.defaultModel || t("model: auto")}${changed ? ` · ${changed} ${t("set")}` : ""}`);
}

async function loadModels() {
  const { models = [], default: preset } = await api("/api/models").catch(() => ({}));
  state.defaultModel = preset || "";
  const select = $("#set-model"), chosen = select.value;
  select.replaceChildren(el("option", { value: "" }, preset ? `${t("Default")} (${preset})` : t("Default (first loaded model)")),
    ...models.map((id) => el("option", { value: id }, id)));
  if (chosen && !models.includes(chosen)) select.append(el("option", { value: chosen }, chosen));
  select.value = chosen;
  renderSettingsButton();
}

$("#settings-toggle").addEventListener("click", () => {
  const box = $("#settings");
  box.hidden = !box.hidden;
  $("#settings-toggle").setAttribute("aria-expanded", String(!box.hidden));
  if (!box.hidden) loadModels();
});
$("#settings").addEventListener("change", () => { remember("settings", JSON.stringify(getSettings())); renderSettingsButton(); });
$("#set-reset").addEventListener("click", () => { setSettings({}); remember("settings", "{}"); });

// -- the meter of a code run: turns, speed, and how full the context is (it is compacted at 100%) ----
function renderMeter(e) {
  const meter = $("#meter");
  const speed = e.tokens && e.seconds > 0.05 ? `${Math.round(e.tokens / e.seconds)} tok/s` : null;
  const parts = [`${t("Turn")} ${e.turn}${e.max_turns ? `/${e.max_turns}` : ""}`, speed].filter(Boolean).join(" · ");
  if (!e.context_limit) { meter.replaceChildren(parts); meter.hidden = false; return; }
  const full = Math.min(100, Math.round((100 * e.context) / e.context_limit));
  meter.replaceChildren(parts, ` · ${t("context")} `,
    el("span", { class: "gauge" + (full >= 80 ? " high" : "") }, el("span", { style: `width:${full}%` })), ` ${full}%`);
  meter.title = t("Context: {used}k of {limit}k characters", { used: Math.round(e.context / 1000), limit: Math.round(e.context_limit / 1000) }) +
    (e.prompt_tokens ? ` (${e.prompt_tokens} ${t("tokens")})` : "") + t(". At 100% it is compacted: old messages are replaced by notes.");
  meter.hidden = false;
}

// -- a chat: its timeline and its progress panel --------------------------------------------------
function newChat() {
  show("new");
  if (state.source) state.source.close();
  state.currentRun = null;
  $("#timeline").replaceChildren(el("div", { class: "welcome" }, el("h2", {}, t("What are we working on?")),
    el("div", {}, t("Research answers a question from your library, Wikipedia and arXiv. Code writes and tests a project.")),
    el("div", {}, t("Ctrl+Enter sends."))));
  $("#side-panel").hidden = true;
  $("#meter").hidden = true;
  setSettings(recalledSettings());
  setMode(recall("mode"));
  setRunning(false);
  renderRunsSidebar();
  $("#question").focus();
}

function newContext(title) {
  // cards, calls, live: what the current message has put on the timeline; panel: what the progress panel shows;
  // contents: the files the agent has written in this chat, to show what a later write changes
  return { cards: {}, calls: {}, live: {}, current: null, contents: new Map(),
    panel: { rounds: [newRound(title)], openRounds: {}, tree: null, dirs: {}, treeOpen: false } };
}

// A new message of the user: its turns start counting again, and the panel opens a new entry for it.
function nextMessage(ctx, title) {
  Object.assign(ctx, { cards: {}, calls: {}, live: {}, current: null });
  ctx.panel.rounds.push(newRound(title));
}

function attach(runId) {
  show("new");
  if (state.source) state.source.close();
  state.currentRun = runId;
  const run = state.runs[runId];
  $("#timeline").replaceChildren();
  $("#side-panel").hidden = true;
  $("#meter").hidden = true;
  if (run) {
    setSettings(run.settings || {});
    setMode(run.mode);
    setNotebook(run.notebook || "");
    userBubble(run.question, run.images);
  }
  const ctx = newContext(run ? run.question : "");
  ctx.mode = run ? knownMode(run.mode) : state.mode;
  setRunning(true);
  const source = new EventSource(`/api/runs/${runId}/events`);
  state.source = source;
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    const stick = atBottom();
    renderEvent(event, ctx);
    if (stick) scrollDown();
    if (event.kind === "end") { source.close(); renderRunsSidebar().then(() => setRunning(false)); }
  };
  source.onerror = () => { source.close(); setRunning(false); };
}

// The timeline follows new output only while the user is at its end.
function atBottom() {
  const tl = $("#timeline");
  return tl.scrollHeight - tl.scrollTop - tl.clientHeight < 80;
}
function scrollDown() {
  const tl = $("#timeline");
  tl.scrollTop = tl.scrollHeight;
}
function append(...nodes) { $("#timeline").append(...nodes); }

// "Send now" on a message the chat queued while it works: stop what it does and answer the message at once.
function addSendNow() {
  const turn = $("#timeline").lastElementChild;
  const button = el("button", { class: "ghost small send-now", type: "button", title: t("Stop the current work and answer this message now") }, t("Send now"));
  button.addEventListener("click", async () => {
    button.disabled = true;
    const options = { servers: getSelectedMcpServers(), settings: getSettings() };
    if (state.mode !== "code" && $("#notebook").value) options.notebook = $("#notebook").value;
    try {
      await api(`/api/runs/${state.currentRun}/send-now`, { method: "POST", body: JSON.stringify(options) });
      attach(state.currentRun); // the stopped part has ended: follow the new one
    } catch (e) { fail(e); button.remove(); }
  });
  turn.append(button);
}
function clearSendNow() { document.querySelectorAll(".send-now").forEach((b) => b.remove()); }

function userBubble(text, images = []) {
  const auto = /^\((continue|resume)\b/.test(text); // sent by the page, not typed
  const pictures = images.length ? el("div", { class: "bubble-images" }, images.map((name) => {
    const url = `/api/runs/${state.currentRun}/images/${encodeURIComponent(name)}`;
    return el("a", { href: url, target: "_blank", rel: "noopener" }, el("img", { src: url, alt: t("Attached image"), loading: "lazy" }));
  })) : null;
  append(el("div", { class: "turn" }, el("div", { class: "bubble" + (auto ? " auto" : "") }, pictures, auto ? t(text.slice(1, -1)) : text)));
}

function card(ctx, key, title, badge) {
  if (!ctx.cards[key]) {
    const c = el("div", { class: "card" }, el("h4", {}, title, badge ? el("span", { class: "badge" }, badge) : null), el("div", { class: "calls" }));
    ctx.cards[key] = c;
    append(c);
  }
  return ctx.cards[key];
}

function liveBox(ctx, key) {
  if (!ctx.live[key]) {
    const box = el("div", { class: "live" }, el("div", { class: "live-thinking" }), el("div", { class: "live-text md" }));
    box.text = ""; // the model's text so far, as Markdown
    (ctx.cards[key] || $("#timeline")).append(box);
    ctx.live[key] = box;
  }
  return ctx.live[key];
}

function closeLive(ctx, key) {
  const box = ctx.live[key];
  if (!box) return;
  delete ctx.live[key];
  const parts = [];
  const thinking = box.querySelector(".live-thinking").textContent;
  if (thinking.trim()) parts.push(el("details", { class: "call thinking" }, el("summary", {}, `${t("Thinking")} (${thinking.length} ${t("chars")})`), el("pre", {}, thinking)));
  // in a chat the answer follows as the message itself
  if (box.text.trim() && ctx.mode !== "chat") parts.push(el("details", { class: "call" }, el("summary", {}, `${t("Model output")} (${box.text.length} ${t("chars")})`), box.querySelector(".live-text")));
  box.replaceWith(...parts);
}

// -- tool calls: file edits as a diff, verify as a test result, everything else as text ------------
const toolName = (name) => name.split("__").pop(); // "coder__verify" -> "verify"
const parseArgs = (args) => { try { const a = JSON.parse(args); return a && typeof a === "object" ? a : null; } catch { return null; } };
const prettyArgs = (args) => { try { return JSON.stringify(JSON.parse(args), null, 2); } catch { return args; } };
const DIFF_LINES = 400; // of one edit shown on the page
const DIFF_OPEN = 40; // an edit with at most this many lines is shown open

// The lines of b against a: [kind, text] with kind "ctx", "del" or "add" (longest common subsequence).
function lineDiff(a, b) {
  const n = a.length, m = b.length;
  if (n * m > 250000) return [...a.map((t) => ["del", t]), ...b.map((t) => ["add", t])];
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push(["ctx", a[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push(["del", a[i++]]);
    else out.push(["add", b[j++]]);
  }
  while (i < n) out.push(["del", a[i++]]);
  while (j < m) out.push(["add", b[j++]]);
  return out;
}

const SIGN = { add: "+", del: "−", ctx: " " };
const counts = (added, removed) => el("span", { class: "counts" }, el("span", { class: "plus" }, `+${added}`), " ", el("span", { class: "minus" }, `−${removed}`));

const DIFF_CONTEXT = 3; // unchanged lines shown around a change

// Fold the unchanged lines that are far from a change into one row.
function foldContext(rows) {
  const near = rows.map(() => false);
  rows.forEach((r, i) => {
    if (r[0] !== "ctx") for (let k = Math.max(0, i - DIFF_CONTEXT); k <= Math.min(rows.length - 1, i + DIFF_CONTEXT); k++) near[k] = true;
  });
  const out = [];
  for (let i = 0; i < rows.length; i++) {
    if (near[i]) { out.push(rows[i]); continue; }
    let j = i;
    while (j < rows.length && !near[j]) j++;
    out.push(["gap", `⋯ ${j - i} ${t("unchanged lines")}`]);
    i = j - 1;
  }
  return out;
}

function diffCall(e, a, ctx) {
  const lines = (s) => String(s ?? "").replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n");
  const edit = toolName(e.name) === "edit_file";
  const path = String(a.path ?? "");
  const known = ctx.contents.get(path); // what this chat wrote to the file before, if it did
  let rows, after;
  if (edit) {
    rows = lineDiff(lines(a.old), lines(a.new));
    if (known != null && typeof a.old === "string" && known.includes(a.old)) after = known.replace(a.old, () => String(a.new ?? ""));
  } else {
    after = String(a.content ?? "");
    rows = known != null ? lineDiff(lines(known), lines(after)) : lines(after).map((line) => ["add", line]);
  }
  const added = rows.filter((r) => r[0] === "add").length, removed = rows.filter((r) => r[0] === "del").length;
  const folded = foldContext(rows);
  const body = el("div", { class: "diff-body" }, folded.slice(0, DIFF_LINES).map(([kind, text]) =>
    kind === "gap" ? el("div", { class: "dl more" }, text) : el("div", { class: `dl ${kind}` }, el("span", { class: "sign" }, SIGN[kind]), el("span", {}, text))));
  if (folded.length > DIFF_LINES) body.append(el("div", { class: "dl more" }, `… ${folded.length - DIFF_LINES} ${t("more lines")}`));
  const d = el("details", { class: "call diff pending", open: folded.length <= DIFF_OPEN },
    el("summary", {}, el("span", {}, edit ? "edit" : "write"), el("span", { class: "path" }, path), counts(added, removed)), body);
  const files = round(ctx).files;
  d.commit = () => { // the tool has done it: count the change for this message and remember the file
    const file = files.get(path) || { added: 0, removed: 0 };
    files.set(path, { added: file.added + added, removed: file.removed + removed });
    if (after != null) ctx.contents.set(path, after);
    else ctx.contents.delete(path); // edited a file this chat has not seen whole
    renderPanel(ctx);
  };
  return d;
}

function testCall(title) {
  return el("details", { class: "call test pending" },
    el("summary", {}, el("span", { class: "verdict" }, "RUNNING"), el("span", { class: "headline" }, title)));
}

// What a verify result says: [class, label].
function verdict(output) {
  if (/^PASSED/.test(output)) return /ALL DONE/.test(output) ? ["pass", "ALL DONE"] : /but (the review is still open|no file has changed)/.test(output) ? ["warn", "PASSED · OPEN"] : ["pass", "PASSED"];
  if (/^FAILED/.test(output)) return ["fail", "FAILED"];
  return ["warn", /^CANNOT VERIFY/.test(output) ? "NO TESTS" : "NOT RUN"];
}

function testResult(d, output) {
  const [cls, label] = verdict(output);
  const badge = d.querySelector(".verdict");
  badge.className = `verdict ${cls}`;
  badge.textContent = label;
  d.querySelector(".headline").textContent = output.split("\n")[0];
  d.append(el("pre", {}, output));
  d.open = cls === "fail";
}

function renderToolCall(e, ctx) {
  const key = e.index ?? ctx.current ?? "misc";
  const c = ctx.cards[key] || card(ctx, key, t("Tool calls"));
  const tool = toolName(e.name);
  const a = parseArgs(e.arguments);
  let d;
  if (a && (tool === "write_file" || tool === "edit_file")) d = diffCall(e, a, ctx);
  else if (tool === "verify") d = testCall(a && a.note ? a.note : "verify");
  else d = el("details", { class: "call pending" }, el("summary", {}, `${e.name} ${e.arguments}`), el("pre", {}, prettyArgs(e.arguments)));
  if (a && tool === "add_source") d.source = a; // numbered by its result, for the progress panel
  ctx.calls[e.id + key] = d;
  c.querySelector(".calls").append(d);
}

function renderToolResult(e, ctx) {
  const d = ctx.calls[e.id + (e.index ?? ctx.current ?? "misc")];
  if (!d) return;
  d.classList.remove("pending");
  if (e.error) d.classList.add("error");
  if (d.classList.contains("test")) { testResult(d, e.output); round(ctx).verifies.push(verdict(e.output)); renderPanel(ctx); }
  else if (d.classList.contains("diff")) {
    d.append(el("div", { class: "call-note" }, e.output));
    if (e.error) d.open = true;
    else d.commit();
    refreshFiles(ctx);
  }
  else d.append(el("pre", {}, e.output));
  const n = d.source && !e.error && /\[(\d+)\]/.exec(e.output);
  if (n) { round(ctx).sources.set(+n[1], d.source); renderPanel(ctx); }
}

// -- the files of a code project: a tree in the progress panel; a click shows the file ----------
const FILE_LANGUAGE = { py: "python", js: "js", mjs: "js", ts: "ts", tsx: "tsx", jsx: "jsx", json: "json", sh: "bash", ps1: "powershell",
  yml: "yaml", yaml: "yaml", toml: "toml", c: "c", h: "c", cpp: "cpp", hpp: "cpp", java: "java", go: "go", rs: "rust", cs: "csharp" };
const TREE_OPEN = 40; // a project with at most this many files shows its folders open

// Read the file list again; calls that arrive while one is under way are folded into one more.
function refreshFiles(ctx) {
  const run = state.runs[state.currentRun];
  if (!run || run.mode !== "code") return;
  if (ctx.filesBusy) { ctx.filesAgain = true; return; }
  ctx.filesBusy = true;
  api(`/api/runs/${run.id}/files`).catch(() => null).then((tree) => {
    ctx.filesBusy = false;
    if (tree && state.currentRun === run.id) { ctx.panel.tree = tree; renderPanel(ctx); }
    if (ctx.filesAgain) { ctx.filesAgain = false; refreshFiles(ctx); }
  });
}

function fileTree(ctx) {
  const { tree, dirs } = ctx.panel;
  const files = allFiles(ctx);
  const root = { dirs: {}, files: [] };
  for (const path of tree.files) {
    let node = root;
    for (const name of path.split("/").slice(0, -1)) node = node.dirs[name] ||= { dirs: {}, files: [] };
    node.files.push(path);
  }
  const level = (node, prefix) => [
    ...Object.entries(node.dirs).map(([name, child]) => {
      const path = `${prefix}${name}/`;
      return el("details", { class: "dir", open: dirs[path] ?? tree.files.length <= TREE_OPEN, ontoggle: (ev) => { dirs[path] = ev.currentTarget.open; } },
        el("summary", {}, icon("folder"), name), level(child, path));
    }),
    ...node.files.map((path) => {
      const changed = files.get(path);
      return el("button", { class: "file", type: "button", title: path, onclick: () => openFile(path) },
        icon("file"), el("span", { class: "path" }, path.split("/").pop()), changed ? counts(changed.added, changed.removed) : null);
    }),
  ];
  return el("details", { class: "tree", open: ctx.panel.treeOpen, ontoggle: (ev) => { ctx.panel.treeOpen = ev.currentTarget.open; } },
    el("summary", {}, `${t("Project files")} (${tree.files.length}${tree.more ? "+" : ""})`), level(root, ""));
}

async function openFile(path) {
  const run = state.runs[state.currentRun];
  if (!run) return;
  let file;
  try {
    file = await api(`/api/runs/${run.id}/file?path=${encodeURIComponent(path)}`);
  } catch (e) {
    toast(e.message === "not a text file" ? t("This file cannot be shown as text.") : e.message, "error");
    return;
  }
  const code = el("code");
  code.innerHTML = highlight(file.content, FILE_LANGUAGE[path.split(".").pop().toLowerCase()]); // escaped by highlight
  const close = () => { dialog.close(); dialog.remove(); };
  const dialog = el("dialog", { class: "dialog file-view" },
    el("div", { class: "file-head" }, icon("file"), el("strong", {}, path), el("button", { class: "icon", type: "button", title: t("Close"), onclick: close }, icon("x"))),
    el("pre", {}, code));
  dialog.addEventListener("click", (ev) => { if (ev.target === dialog) close(); }); // a click on the backdrop
  dialog.addEventListener("close", () => dialog.remove()); // closed with Escape
  document.body.append(dialog);
  dialog.showModal();
}

// -- the progress panel: the plan of a code chat, the sub-questions and sources of a research chat --
function section(title, extra, ...children) {
  return el("section", {}, el("h4", {}, title, extra), ...children);
}

const progress = (done, total) => el("div", { class: "progress" }, el("div", { style: `width:${total ? Math.round((100 * done) / total) : 0}%` }));

// What one message of the user led to; the panel shows the newest one open and the earlier ones as a history.
function newRound(title) {
  return { title, plan: null, files: new Map(), verifies: [], subs: [], sources: new Map() };
}
const round = (ctx) => ctx.panel.rounds[ctx.panel.rounds.length - 1];

function totals(files) {
  let added = 0, removed = 0;
  for (const f of files.values()) { added += f.added; removed += f.removed; }
  return { added, removed };
}

// The changes of the whole chat per file, for the file tree.
function allFiles(ctx) {
  const all = new Map();
  for (const r of ctx.panel.rounds) {
    for (const [path, f] of r.files) {
      const sum = all.get(path) || { added: 0, removed: 0 };
      all.set(path, { added: sum.added + f.added, removed: sum.removed + f.removed });
    }
  }
  return all;
}

const VERDICT_SIGN = { pass: "✓", fail: "✕", warn: "•" };

function roundParts(r) {
  const { plan, files, verifies, subs, sources } = r;
  const parts = [];
  if (plan) {
    const done = plan.steps.filter((s) => s.done).length;
    parts.push(section(t("Plan"), el("span", {}, `${done}/${plan.steps.length} ${t("steps")}`),
      el("div", { class: "goal" }, plan.goal),
      progress(done, plan.steps.length),
      el("ul", { class: "steps" }, plan.steps.map((s, i) => el("li", { class: s.done ? "done" : i === plan.current ? "current" : "" }, s.text))),
      el("details", {}, el("summary", {}, `${t("Acceptance criteria")} (${plan.criteria.length})`), el("ol", {}, plan.criteria.map((c) => el("li", {}, c))))));
  }
  if (plan || verifies.length) {
    const last = plan ? (plan.finished ? "ALL DONE" : plan.last) : "";
    const [cls, label] = !plan ? verifies[verifies.length - 1]
      : plan.finished ? ["pass", "ALL DONE"] : /^PASSED/.test(last) ? ["pass", "PASSED"] : /^FAILED/.test(last) ? ["fail", "FAILED"] : ["", "NOT RUN"];
    parts.push(section(t("Verifications"), el("span", {}, plan && plan.review ? `${t("review")}: ${t(plan.review)}` : verifies.length || null),
      // every verify of this message in order: the way from the first failure to ALL DONE
      verifies.length ? el("div", { class: "verify-trail" }, verifies.map(([c, l]) => el("span", { class: `vdot ${c}`, title: l }, VERDICT_SIGN[c] || "•"))) : null,
      el("div", { class: "last-verify" }, el("span", { class: `verdict ${cls}` }, label),
        el("span", { class: "muted" }, last.replace(/^(PASSED|FAILED|ALL DONE|not run yet)\s*/, "")))));
  }
  if (files.size) {
    const sum = totals(files);
    parts.push(section(t("Files changed"), counts(sum.added, sum.removed),
      el("ul", { class: "file-list" }, [...files].map(([path, f]) => el("li", {}, el("span", { class: "path", title: path }, path), counts(f.added, f.removed))))));
  }
  if (subs.length) {
    const done = subs.filter((s) => s.state === "done").length;
    parts.push(section(t("Sub-questions"), el("span", {}, `${done}/${subs.length}`), progress(done, subs.length),
      el("ul", { class: "steps" }, subs.map((s) => el("li", { class: s.state || "" }, s.question, s.skill ? [" ", el("span", { class: "badge" }, s.skill)] : null)))));
  }
  if (sources.size) {
    parts.push(section(t("Sources"), el("span", {}, sources.size),
      el("ul", { class: "source-list" }, [...sources].sort((x, y) => x[0] - y[0]).map(([n, s]) =>
        el("li", { title: s.quote || s.quotes?.[0] || "" }, el("span", { class: "n" }, `[${n}]`), s.title || s.source_id, " ",
          el("span", { class: "muted" }, [s.origin, s.locator].filter(Boolean).join(" · ")))))));
  }
  return parts;
}

// One line for a message in the history: what was asked, how far it got, how much it changed.
function roundSummary(r, number) {
  const sum = totals(r.files);
  const last = r.plan && r.plan.finished ? ["pass", "ALL DONE"] : r.verifies[r.verifies.length - 1];
  const auto = /^\((continue|resume)\b/.test(r.title);
  return el("summary", {},
    el("span", { class: "round-title", title: r.title }, `${number}. ${shortTitle(auto ? t(r.title.slice(1, -1)) : r.title, 60)}`),
    el("span", { class: "round-facts" },
      r.plan ? el("span", { class: "muted" }, `${r.plan.steps.filter((s) => s.done).length}/${r.plan.steps.length}`) : null,
      last ? el("span", { class: `verdict ${last[0]}` }, last[1]) : null,
      r.files.size ? counts(sum.added, sum.removed) : null));
}

function renderPanel(ctx) {
  const { rounds, tree, openRounds } = ctx.panel;
  const shown = rounds.map((r, i) => [r, i, roundParts(r)]).filter(([, , parts]) => parts.length);
  const parts = [];
  if (shown.length === 1) parts.push(...shown[0][2]); // a chat with one message needs no history
  else if (shown.length) {
    const newest = shown[shown.length - 1][1];
    parts.push(el("h4", { class: "history-head" }, t("History"), el("span", {}, shown.length)));
    for (const [r, i, body] of [...shown].reverse()) { // newest first, open; the earlier ones fold
      const summary = roundSummary(r, i + 1);
      const entry = el("details", { class: "round" + (i === newest ? " newest" : ""), open: openRounds[i] ?? i === newest }, summary, ...body);
      summary.addEventListener("click", () => { openRounds[i] = !entry.open; }); // what the user chose stays; the click comes before the change
      parts.push(entry);
    }
  }
  if (tree && tree.files.length) parts.push(fileTree(ctx));
  const panel = $("#side-panel");
  panel.hidden = !parts.length;
  panel.replaceChildren(...parts);
}

function sub(ctx, index, fields) {
  const subs = round(ctx).subs;
  while (subs.length < index) subs.push({ question: `${t("Sub-question")} ${subs.length + 1}` });
  Object.assign(subs[index - 1], fields);
  renderPanel(ctx);
}

// After the report the notebook's source list is the truth (numbers, quotes).
async function loadSources(ctx, project) {
  const asked = round(ctx); // the message this report belongs to
  const p = await api(`/api/projects/${encodeURIComponent(project)}`).catch(() => null);
  if (!p) return;
  asked.sources = new Map(p.sources.map((s) => [s.n, s]));
  renderPanel(ctx);
}

// -- events ---------------------------------------------------------------------------------------
function renderEvent(e, ctx) {
  switch (e.kind) {
    case "user":
      nextMessage(ctx, e.text);
      clearSendNow();
      if (!e.queued) userBubble(e.text, e.images); // a queued message is on the page already
      break;
    case "steer": // sent while the agent was working: read by a code run, queued by a chat or a research
      userBubble(e.text, e.images);
      if (state.running) addSendNow();
      break;
    case "dequeued": // the oldest queued message is being read now
      document.querySelector(".send-now")?.remove();
      break;
    case "token": {
      const box = liveBox(ctx, e.index ?? "main");
      if (e.thinking) box.querySelector(".live-thinking").textContent += e.text;
      else renderRich(box.querySelector(".live-text"), (box.text += e.text));
      break;
    }
    case "stream_end":
      closeLive(ctx, e.index ?? "main");
      break;
    case "status":
      break;
    case "phase":
      append(el("div", { class: "phase" }, e.title));
      ctx.current = null;
      break;
    case "memory":
      append(el("details", { class: "card" }, el("summary", {}, t("Recalled from memory")), el("pre", {}, e.text)));
      break;
    case "plan":
      round(ctx).subs = e.sub_questions.map((s) => ({ ...s }));
      renderPanel(ctx);
      append(el("div", { class: "card" }, el("h4", {}, t("Plan")),
        el("ol", {}, e.sub_questions.map((s) => el("li", {}, s.question, s.skill ? el("span", { class: "badge" }, s.skill) : null)))));
      break;
    case "subquestion":
      ctx.current = e.index;
      sub(ctx, e.index, { question: e.question, skill: e.skill, state: "current" });
      card(ctx, e.index, `${e.index}/${e.total} · ${e.question}`, e.skill);
      break;
    case "tool_call":
      renderToolCall(e, ctx);
      break;
    case "tool_result":
      renderToolResult(e, ctx);
      break;
    case "findings": {
      const c = ctx.cards[e.index] || card(ctx, e.index, e.question);
      c.append(el("div", { class: "findings", html: e.html }));
      sub(ctx, e.index, { state: "done" });
      break;
    }
    case "gaps":
      if (e.sub_questions.length) {
        round(ctx).subs.push(...e.sub_questions.map((q) => ({ question: q })));
        renderPanel(ctx);
        append(el("div", { class: "card" }, el("h4", {}, t("Gaps found — researching more")), el("ul", {}, e.sub_questions.map((q) => el("li", {}, q)))));
      }
      break;
    case "problems":
      if (e.items.length) append(el("div", { class: "card" }, el("h4", {}, e.source === "reviewer" ? t("Reviewer") : t("Automatic checks")), el("ul", { class: "problems" }, e.items.map((p) => el("li", {}, p)))));
      else append(el("div", { class: "log success" }, e.source === "reviewer" ? t("Reviewer found no issues.") : t("Checks passed.")));
      break;
    case "log":
      append(el("div", { class: `log ${e.level || ""}` }, e.text));
      break;
    case "notebook": {
      const url = fileUrl(e.path);
      append(el("div", { class: "log" }, t("Computations notebook: "), url ? el("a", { href: url, download: "" }, e.path) : e.path));
      break;
    }
    case "report": {
      const extras = e.cache_hits ? [el("span", { class: "muted" }, `· ${e.cache_hits} ${t("cached tool calls")}`)] : [];
      delete sourceCache[e.project]; // a new report may have new sources
      append(el("article", { class: "report", "data-project": e.project }, exportBar(e.project, extras), el("div", { html: e.html })));
      loadSources(ctx, e.project);
      break;
    }
    case "answer":
      append(el("article", { class: "report" }, el("div", { html: e.html })));
      break;
    case "turn": {
      $("#compact").hidden = !state.running || ctx.mode === "chat";
      renderMeter(e);
      if (ctx.mode === "chat" && !e.tool_calls.length && e.finish !== "length") break; // a plain answer: no turn card
      const key = `turn-${e.turn}`;
      const info = [`${Math.round(e.seconds)} s`, e.tokens ? `${e.tokens} ${t("tokens")}` : null, e.finish === "length" ? t("cut off") : null].filter(Boolean).join(" · ");
      if (e.tool_calls.length || e.finish === "length") {
        card(ctx, key, `${t("Turn")} ${e.turn}`, info);
        ctx.current = key;
      } else {
        append(el("div", { class: "log" }, `${t("Turn")} ${e.turn} · ${info} · ${t("no tool call")}`));
      }
      break;
    }
    case "state":
      round(ctx).plan = e;
      renderPanel(ctx);
      if (!ctx.panel.tree) refreshFiles(ctx);
      break;
    case "auto_verify": {
      const d = testCall(t("run by the program"));
      d.classList.remove("pending");
      testResult(d, e.output);
      round(ctx).verifies.push(verdict(e.output));
      renderPanel(ctx);
      append(el("div", { class: "card" }, el("div", { class: "calls" }, d)));
      break;
    }
    case "restart": // stuck too often: the run starts again with a clean context
      append(el("div", { class: "log warning" }, t("Stuck in long thinking {n} times: starting again with a clean context{notes}.",
        { n: e.stuck, notes: e.noted ? t(" and the model's notes") : "" })));
      break;
    case "judge": // the check of a finished task against the task itself, in a conversation of its own
      if (e.solved) append(el("div", { class: "log success" }, t("Judge: the change solves the task.")));
      else append(el("div", { class: "card" }, el("h4", {}, t("Judge: not solved yet")), el("div", { class: "problems" }, e.reasons)));
      break;
    case "compact":
      append(el("div", { class: "log" }, e.dropped
        ? t(e.manual ? "Context compacted on request: {n} old messages replaced by the plan, the file list{notes}." : "Context compacted: {n} old messages replaced by the plan, the file list{notes}.",
          { n: e.dropped, notes: e.noted ? t(" and the model's notes") : "" })
        : t("Nothing to compact yet.")));
      break;
    case "outcome":
      append(el("div", { class: `log ${e.finished ? "success" : "warning"}` }, e.finished ? t("Finished: verification passes.") : `${t("Stopped")}: ${e.reason}.`));
      break;
    case "error":
      append(el("div", { class: "card error-box" }, e.text));
      break;
    case "segment_end": // an earlier message of this chat ended here
    case "end":
      clearSendNow();
      if (e.status !== "done") append(el("div", { class: "log warning" }, `${t("Run")} ${t(e.status)}.`));
      break;
  }
}

function exportBar(project, extra = []) {
  const bar = el("div", { class: "toolbar" }, el("span", { class: "muted" }, t("Export:")));
  for (const fmt of state.status.formats || ["html", "md", "bib"]) {
    bar.append(el("button", {
      class: "ghost", onclick: async () => {
        try {
          const r = await api(`/api/projects/${encodeURIComponent(project)}/export?format=${fmt}`, { method: "POST" });
          window.open(r.url, "_blank");
        } catch (err) { fail(err); }
      },
    }, fmt.toUpperCase()));
  }
  bar.append(...extra);
  return bar;
}

// -- projects -------------------------------------------------------------------------
async function renderProjectsSidebar() {
  const projects = await api("/api/projects").catch(() => []);
  $("#side-list").replaceChildren(
    ...(projects.length ? projects.map((p) =>
      el("button", { class: "side-item" + (state.project === p.name ? " active" : ""), onclick: () => openProject(p.name) },
        el("span", { class: "t", title: p.question || p.name }, shortTitle(p.question || p.name)), el("small", {}, p.has_report ? p.name : `${p.name} · ${t("no report yet")}`))) : [el("p", { class: "muted" }, t("No projects yet."))])
  );
}

async function openProject(name) {
  state.project = name;
  show("project");
  renderProjectsSidebar();
  const view = $("#view-project");
  view.replaceChildren(el("p", { class: "muted" }, t("Loading…")));
  const p = await api(`/api/projects/${encodeURIComponent(name)}`);
  const extras = [];
  if (p.notebook) extras.push(el("a", { class: "ghost", href: `/files/${encodeURIComponent(p.name)}/computations.ipynb`, download: "" }, t("Notebook (.ipynb)")));
  extras.push(el("button", { class: "ghost", onclick: () => resume(p.name) }, t("Resume")));
  const parts = [el("h2", {}, p.plan.question || p.name)];
  if (p.report_html) parts.push(el("article", { class: "report", "data-project": p.name }, exportBar(p.name, extras), el("div", { html: p.report_html })));
  else parts.push(el("div", { class: "toolbar" }, ...extras));
  if (p.sources.length) {
    parts.push(el("h3", {}, t("Sources")), el("table", { class: "sources" }, p.sources.map((s) =>
      el("tr", {}, el("td", {}, `[${s.n}]`), el("td", {}, el("strong", {}, s.title), el("div", { class: "muted" },
        [s.authors, s.year, s.origin, s.locator].filter(Boolean).join(" · ")), ...(s.quotes || []).map((q) => el("div", {}, `“${q}”`)))))));
  }
  if (p.findings.length) {
    parts.push(el("h3", {}, t("Findings")), ...p.findings.map((f) => el("details", { class: "card" }, el("summary", {}, f.name), el("div", { html: f.html }))));
  }
  view.replaceChildren(...parts);
}

async function resume(project) {
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify({ project, resume: true }) });
    state.runs[run.id] = run;
    attach(run.id);
    renderRunsSidebar();
  } catch (e) { fail(e); }
}

// -- memory & skills ------------------------------------------------------------------
async function loadMemory(q = "") {
  const items = await api(`/api/memory?q=${encodeURIComponent(q)}`).catch(() => []);
  $("#memory-list").replaceChildren(
    ...(items.length ? items.map((m) => el("li", {},
      el("span", { class: `badge ${m.kind}` }, m.kind),
      el("div", {}, m.text, m.sources ? el("div", { class: "muted" }, m.sources) : null),
      el("button", { class: "ghost danger", title: t("Forget"), onclick: async () => {
        if (!(await confirmDialog(t("Forget this memory?"), t("Forget")))) return;
        await api(`/api/memory/${m.id}`, { method: "DELETE" }).catch(fail);
        loadMemory($("#memory-q").value);
      } }, t("Forget")))) : [el("li", { class: "muted" }, t("Nothing in memory yet."))])
  );
}
$("#memory-search").addEventListener("submit", (ev) => { ev.preventDefault(); loadMemory($("#memory-q").value.trim()); });

async function loadSkills() {
  const skills = await api("/api/skills").catch(() => []);
  $("#skills-list").replaceChildren(...skills.map((s) =>
    el("div", { class: "card" },
      el("h4", {}, s.name, s.category ? el("span", { class: `badge ${s.category}` }, s.category) : null),
      el("div", {}, s.description),
      s.when_to_use ? el("div", { class: "muted" }, `${t("Use when")}: ${s.when_to_use}`) : null)));
}

// -- citations: [n] in a report shows its source; a click keeps the card open and loads the passage ------
// A report stands in an element with data-project; its sources are read once per project.
const sourceCache = {};
const projectSources = (project) => (sourceCache[project] ||= api(`/api/projects/${encodeURIComponent(project)}`)
  .then((p) => new Map(p.sources.map((s) => [s.n, s]))).catch(() => new Map()));
const cite = { card: null, link: null, pinned: false, shown: 0 };

function closeCite() {
  if (cite.card) cite.card.remove();
  Object.assign(cite, { card: null, link: null, pinned: false });
}

function passageNode(p) {
  if (!p.match) return el("div", { class: "passage muted" }, t("This quote was not found in the saved tool output."));
  return el("div", { class: "passage" }, p.before ? "…" + p.before : "", el("mark", {}, p.match), p.after ? p.after + "…" : "");
}

async function showCite(link, pin) {
  const report = link.closest("[data-project]");
  const n = parseInt(link.textContent, 10);
  if (!report || !n) return;
  const shown = ++cite.shown;
  link.removeAttribute("title"); // the card replaces the browser's tooltip
  const project = report.dataset.project;
  const source = (await projectSources(project)).get(n);
  if (shown !== cite.shown) return; // the pointer has moved on
  closeCite();
  const quotes = el("div", { class: "quotes" }, source ? (source.quotes || []).map((q) => el("blockquote", {}, `“${q}”`)) : null);
  const card = el("div", { class: "cite-card" + (pin ? " pinned" : ""), role: "dialog", "aria-label": `${t("Source")} ${n}` },
    el("div", { class: "cite-head" }, el("span", { class: "n" }, `[${n}]`), el("strong", {}, source ? source.title : t("Unknown source")),
      pin ? el("button", { class: "icon", type: "button", title: t("Close (Esc)"), onclick: closeCite }, icon("x")) : null),
    source ? el("div", { class: "muted" }, [source.authors, source.year, source.origin, source.locator].filter(Boolean).join(" · ")) : el("div", { class: "muted" }, t("No source with this number is registered.")),
    quotes,
    source && !pin ? el("div", { class: "hint muted" }, t("Click the number for the passage.")) : null);
  document.body.append(card);
  const rect = link.getBoundingClientRect();
  card.style.left = `${Math.max(8, Math.min(rect.left - 20, window.innerWidth - card.offsetWidth - 8))}px`;
  const below = rect.bottom + 6 + card.offsetHeight < window.innerHeight || rect.top < card.offsetHeight + 12;
  card.style.top = below ? `${rect.bottom + 6}px` : `${rect.top - card.offsetHeight - 6}px`;
  Object.assign(cite, { card, link, pinned: pin });
  if (!pin || !source) return;
  const data = await api(`/api/projects/${encodeURIComponent(project)}/sources/${n}/passages`).catch(() => null);
  if (cite.card !== card) return;
  if (data && data.passages.length) quotes.replaceChildren(...data.passages.flatMap((p) => [el("blockquote", {}, `“${p.quote}”`), passageNode(p)]));
  else quotes.append(el("div", { class: "passage muted" }, t("No quote is registered for this source.")));
  card.style.top = `${Math.max(8, Math.min(parseFloat(card.style.top), window.innerHeight - card.offsetHeight - 8))}px`;
}

document.addEventListener("mouseover", (ev) => {
  const link = ev.target.closest && ev.target.closest("a.cite");
  if (link && !cite.pinned && link !== cite.link) showCite(link, false);
});
document.addEventListener("mouseout", (ev) => {
  if (!cite.pinned && ev.target.closest && ev.target.closest("a.cite")) { cite.shown++; closeCite(); }
});
document.addEventListener("click", (ev) => {
  const link = ev.target.closest("a.cite");
  if (link && link.closest("[data-project]")) { ev.preventDefault(); showCite(link, true); }
  else if (cite.pinned && !ev.target.closest(".cite-card")) closeCite();
});

// -- the command palette (Ctrl+K) and the keyboard shortcuts ------------------------------------
const goTo = (view) => document.querySelector(`.tab[data-view="${view}"]`).click();

function toggleSettings() {
  show("new");
  $("#settings-toggle").click();
}

function focusSearch() {
  goTo("new");
  renderChats();
  $("#chat-search").focus();
}

function commands() {
  const run = state.runs[state.currentRun];
  return [
    { label: t("New chat"), hint: "Alt+N", action: newChat },
    { label: t("New chat in Research mode"), action: () => { newChat(); remember("mode", "research"); setMode("research"); } },
    { label: t("New chat in Code mode"), action: () => { newChat(); remember("mode", "code"); setMode("code"); } },
    { label: t("Model and settings of this chat"), hint: "Ctrl+,", action: toggleSettings },
    { label: t("Search chats"), hint: "/", action: focusSearch },
    { label: t("New folder"), action: () => { goTo("new"); newFolder(); } },
    state.running && { label: t("Cancel the run"), action: () => $("#cancel").click() },
    !$("#compact").hidden && { label: t("Compact the conversation"), action: () => $("#compact").click() },
    run && { label: run.pinned ? t("Unpin this chat") : t("Pin this chat"), action: () => patchRun(run.id, { pinned: !run.pinned }) },
    run && { label: t("Rename this chat"), action: () => { goTo("new"); $("#chat-search").value = ""; renderChats(); renameChat(run); } },
    { label: t("Switch the theme"), action: nextTheme },
    { label: t(LANG === "tr" ? "Switch the language to English" : "Switch the language to Turkish"), action: switchLanguage },
    { label: t("Go to Projects"), action: () => goTo("projects") },
    { label: t("Go to Memory"), action: () => goTo("memory") },
    { label: t("Go to Skills"), action: () => goTo("skills") },
    { label: t("Go to Tools (MCP servers)"), action: () => goTo("tools") },
    ...state.runList.map((r) => ({ label: chatTitle(r), hint: `${t(knownMode(r.mode))} · ${t(r.status)}`, action: () => attach(r.id) })),
  ].filter(Boolean);
}

function openPalette() {
  if ($("dialog.palette")) return;
  const all = commands();
  let found = all, at = 0;
  const input = el("input", { type: "text", placeholder: t("Type a command or the name of a chat"), "aria-label": t("Command") });
  const list = el("div", { class: "palette-list", role: "listbox" });
  const dialog = el("dialog", { class: "dialog palette" }, input, list);
  const close = () => { dialog.close(); dialog.remove(); };
  const choose = (command) => { close(); command.action(); };
  const render = () => {
    list.replaceChildren(...(found.length ? found.slice(0, 40).map((c, i) =>
      el("button", { class: "palette-item" + (i === at ? " active" : ""), type: "button", role: "option", "aria-selected": String(i === at), onclick: () => choose(c) },
        el("span", {}, c.label), c.hint ? el("small", { class: "muted" }, c.hint) : null)) : [el("p", { class: "muted" }, t("Nothing matches."))]));
    const active = list.querySelector(".active");
    if (active) active.scrollIntoView({ block: "nearest" });
  };
  input.addEventListener("input", () => {
    const terms = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    found = all.filter((c) => terms.every((t) => c.label.toLowerCase().includes(t)));
    at = 0;
    render();
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      at = Math.max(0, Math.min(found.length - 1, 39, at + (ev.key === "ArrowDown" ? 1 : -1)));
      render();
    } else if (ev.key === "Enter" && found[at]) {
      ev.preventDefault();
      choose(found[at]);
    }
  });
  dialog.addEventListener("click", (ev) => { if (ev.target === dialog) close(); }); // a click on the backdrop
  dialog.addEventListener("close", () => dialog.remove()); // closed with Escape
  document.body.append(dialog);
  render();
  dialog.showModal();
}

document.addEventListener("keydown", (ev) => {
  const typing = ev.target.closest && ev.target.closest("input, textarea, select, [contenteditable]");
  const mod = ev.ctrlKey || ev.metaKey;
  if (mod && ev.key.toLowerCase() === "k") { ev.preventDefault(); openPalette(); }
  else if (mod && ev.key === ",") { ev.preventDefault(); toggleSettings(); }
  else if (ev.altKey && ev.key.toLowerCase() === "n") { ev.preventDefault(); newChat(); }
  else if (ev.key === "/" && !typing && !mod && !ev.altKey) { ev.preventDefault(); focusSearch(); }
  else if (ev.key === "Escape") { closeMenu(); closeCite(); }
});

$("#palette").addEventListener("click", openPalette);

// -- init -------------------------------------------------------------------------------
$("#folder").value = recall("folder");
state.targetFolder = recall("target") || null;
translatePage(document.body);
applyTheme(currentTheme());
loadStatus();
setInterval(loadStatus, 15000);
loadModels();
loadOffline(); // the notebooks for the chooser of the message box
newChat();
