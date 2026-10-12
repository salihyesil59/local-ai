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
const state = { status: {}, view: "new", runs: {}, runList: [], currentRun: null, running: false, source: null, project: null, mode: "research", folders: [], targetFolder: null };

// Remembered per browser; storage may be unavailable, the page works without it.
const remember = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
const recall = (key) => { try { return localStorage.getItem(key) || ""; } catch { return ""; } };

// -- icons: one small set of line icons, drawn inline -------------------------------------------
const ICONS = {
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.8 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.8-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.8H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 2.8-1.2V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.8 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.8h.1a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  dots: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
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

$("#theme").addEventListener("click", nextTheme);
$("#lang").addEventListener("click", switchLanguage);
$("#lang").textContent = LANG.toUpperCase();
$("#lang").title = t("Language");

// -- the width of the sidebar: dragged at its edge, remembered in this browser -------------------
const SIDEBAR = { min: 200, max: 640, initial: 280 };

function setSidebar(width, save = true) {
  width = Math.round(Math.max(SIDEBAR.min, Math.min(width, SIDEBAR.max, window.innerWidth - 360)));
  $(".layout").style.setProperty("--sidebar", `${width}px`);
  $("#resizer").setAttribute("aria-valuenow", width);
  if (save) remember("sidebar", String(width));
}

$("#resizer").addEventListener("pointerdown", (ev) => {
  const handle = ev.currentTarget, left = $(".layout").getBoundingClientRect().left;
  handle.setPointerCapture(ev.pointerId);
  handle.classList.add("dragging");
  document.body.classList.add("resizing");
  const move = (e) => setSidebar(e.clientX - left, false);
  const stop = () => {
    handle.removeEventListener("pointermove", move);
    handle.classList.remove("dragging");
    document.body.classList.remove("resizing");
    remember("sidebar", String(parseInt($(".layout").style.getPropertyValue("--sidebar"), 10) || SIDEBAR.initial));
  };
  handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", stop, { once: true });
  handle.addEventListener("pointercancel", stop, { once: true });
});
$("#resizer").addEventListener("dblclick", () => setSidebar(SIDEBAR.initial));
$("#resizer").addEventListener("keydown", (ev) => {
  if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
  ev.preventDefault();
  setSidebar((parseInt($(".layout").style.getPropertyValue("--sidebar"), 10) || SIDEBAR.initial) + (ev.key === "ArrowRight" ? 16 : -16));
});
if (+recall("sidebar")) setSidebar(+recall("sidebar"), false);

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

// -- MCP servers selection (Goose-style) ------------------------------------------
function renderMcpSelectors() {
  const container = $("#mcp-selectors");
  const section = $("#mcp-section");
  const servers = (state.status.servers || []).filter((s) => s !== "notebook" && s !== "coder");
  section.hidden = !servers.length;
  if (!servers.length) return;
  let saved = null;
  try {
    const raw = recall("mcp_servers_" + state.mode);
    if (raw) saved = new Set(JSON.parse(raw));
  } catch {}

  container.replaceChildren(...servers.map((s) => {
    const checked = saved ? saved.has(s) : true;
    const chip = el("label", { class: "mcp-chip" + (checked ? " active" : ""), title: `${s} ${t("MCP server")}` },
      el("input", { type: "checkbox", value: s, checked, onchange: (ev) => { chip.classList.toggle("active", ev.target.checked); saveMcpSelection(); } }),
      el("span", {}, s));
    return chip;
  }));
}

function getSelectedMcpServers() {
  return Array.from($("#mcp-selectors").querySelectorAll('input[type="checkbox"]:checked')).map((cb) => cb.value);
}

function saveMcpSelection() {
  remember("mcp_servers_" + state.mode, JSON.stringify(getSelectedMcpServers()));
}

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
  })
);

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
      el("small", {}, el("span", { class: `dot ${r.status}` }), `${t(r.mode === "code" ? "code" : "research")} · ${t(r.status)}`)),
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
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  renderMcpSelectors();
  updateComposer();
}
document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => { remember("mode", b.dataset.mode); setMode(b.dataset.mode); }));

function updateComposer() {
  const code = state.mode === "code";
  const run = state.runs[state.currentRun];
  for (const n of document.querySelectorAll(".code-only, .research-only, .new-only")) {
    n.hidden = (n.classList.contains("code-only") && !code) || (n.classList.contains("research-only") && code) || (n.classList.contains("new-only") && !!run);
  }
  const info = $("#chat-info");
  info.hidden = !run;
  if (run) info.textContent = code ? `${t("Code")} · ${run.project}` : t("Research · follow-up in this chat");
  const steer = state.running && code && !!run; // a code run takes a message while it works
  $("#run").textContent = steer ? t("Steer") : run ? t("Send") : code ? t("Code") : t("Research");
  $("#run").disabled = state.running && !steer;
  $("#cancel").hidden = !state.running;
  if (!state.running) $("#compact").hidden = true; // shown by the first "turn" event, which only code runs send
  const q = $("#question");
  q.required = !code || steer; // code mode may continue an existing plan without a new task
  q.placeholder = steer ? t("Tell the agent what to change. It reads the message before its next reply.") : run
    ? (code ? t("Next instruction for this project. Leave empty to continue the plan.") : t("Ask a follow-up question."))
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

$("#ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const question = $("#question").value.trim();
  const code = state.mode === "code";
  const steer = state.running && code && state.currentRun;
  if ((!question && (!code || steer)) || (state.running && !steer)) return;
  if (steer) { // the live stream shows the message when the server has taken it
    try {
      await api(`/api/runs/${state.currentRun}/messages`, { method: "POST", body: JSON.stringify({ question }) });
      $("#question").value = "";
      resizeQuestion();
    } catch (e) { fail(e); }
    return;
  }
  const options = { question, servers: getSelectedMcpServers(), settings: getSettings() };
  if (!code) Object.assign(options, { fast: $("#fast").checked, quick: $("#quick").checked, parallel: +$("#parallel").value });
  try {
    let run;
    if (state.currentRun) {
      run = await api(`/api/runs/${state.currentRun}/messages`, { method: "POST", body: JSON.stringify(options) });
    } else {
      const folder = $("#folder").value.trim();
      if (code) remember("folder", folder);
      const body = code ? { ...options, mode: "code", folder } : { ...options, project: $("#project").value.trim() };
      run = await api("/api/runs", { method: "POST", body: JSON.stringify({ ...body, chat_folder: state.targetFolder }) });
    }
    $("#question").value = "";
    resizeQuestion();
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
const SETTINGS = ["model", "temperature", "max_tokens", "max_turns", "context_chars", "max_steps"];
const CODE_SETTINGS = ["max_turns", "context_chars"], RESEARCH_SETTINGS = ["max_steps"];

function getSettings() {
  const code = state.mode === "code";
  const settings = {};
  for (const key of SETTINGS) {
    if ((code ? RESEARCH_SETTINGS : CODE_SETTINGS).includes(key)) continue; // the other agent's limits
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
  setMode(recall("mode") === "code" ? "code" : "research");
  setRunning(false);
  renderRunsSidebar();
  $("#question").focus();
}

function newContext() {
  // cards, calls, live: what the current message has put on the timeline; panel: what the progress panel shows
  return { cards: {}, calls: {}, live: {}, current: null, panel: { plan: null, files: new Map(), subs: [], sources: new Map(), tree: null, dirs: {}, treeOpen: false } };
}

// A new message of the user: its turns and sub-questions start counting again.
function nextMessage(ctx) {
  Object.assign(ctx, { cards: {}, calls: {}, live: {}, current: null });
  ctx.panel.subs = [];
  ctx.panel.sources = new Map(); // a research follow-up has its own project and numbers
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
    setMode(run.mode === "code" ? "code" : "research");
    userBubble(run.question);
  }
  const ctx = newContext();
  setRunning(true);
  const source = new EventSource(`/api/runs/${runId}/events`);
  state.source = source;
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    const stick = atBottom();
    renderEvent(event, ctx);
    if (stick) scrollDown();
    if (event.kind === "end") { source.close(); setRunning(false); renderRunsSidebar(); }
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

function userBubble(text) {
  const auto = /^\((continue|resume)\b/.test(text); // sent by the page, not typed
  append(el("div", { class: "turn" }, el("div", { class: "bubble" + (auto ? " auto" : "") }, auto ? t(text.slice(1, -1)) : text)));
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
  if (box.text.trim()) parts.push(el("details", { class: "call" }, el("summary", {}, `${t("Model output")} (${box.text.length} ${t("chars")})`), box.querySelector(".live-text")));
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

function diffCall(e, a, ctx) {
  const lines = (s) => String(s ?? "").replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n");
  const edit = toolName(e.name) === "edit_file";
  const rows = edit ? lineDiff(lines(a.old), lines(a.new)) : lines(a.content).map((t) => ["add", t]);
  const added = rows.filter((r) => r[0] === "add").length, removed = rows.filter((r) => r[0] === "del").length;
  const body = el("div", { class: "diff-body" }, rows.slice(0, DIFF_LINES).map(([kind, text]) =>
    el("div", { class: `dl ${kind}` }, el("span", { class: "sign" }, SIGN[kind]), el("span", {}, text))));
  if (rows.length > DIFF_LINES) body.append(el("div", { class: "dl more" }, `… ${rows.length - DIFF_LINES} ${t("more lines")}`));
  const path = String(a.path ?? "");
  const file = ctx.panel.files.get(path) || { added: 0, removed: 0 };
  ctx.panel.files.set(path, { added: file.added + added, removed: file.removed + removed });
  renderPanel(ctx);
  return el("details", { class: "call diff pending", open: rows.length <= DIFF_OPEN },
    el("summary", {}, el("span", {}, edit ? "edit" : "write"), el("span", { class: "path" }, path), counts(added, removed)), body);
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
  if (d.classList.contains("test")) testResult(d, e.output);
  else if (d.classList.contains("diff")) { d.append(el("div", { class: "call-note" }, e.output)); if (e.error) d.open = true; refreshFiles(ctx); }
  else d.append(el("pre", {}, e.output));
  const n = d.source && !e.error && /\[(\d+)\]/.exec(e.output);
  if (n) { ctx.panel.sources.set(+n[1], d.source); renderPanel(ctx); }
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
  const { tree, files, dirs } = ctx.panel;
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

function renderPanel(ctx) {
  const { plan, files, subs, sources, tree } = ctx.panel;
  const parts = [];
  if (plan) {
    const done = plan.steps.filter((s) => s.done).length;
    const last = plan.finished ? "ALL DONE" : plan.last;
    const [cls, label] = plan.finished ? ["pass", "ALL DONE"] : /^PASSED/.test(last) ? ["pass", "PASSED"] : /^FAILED/.test(last) ? ["fail", "FAILED"] : ["", "NOT RUN"];
    parts.push(section(t("Plan"), el("span", {}, `${done}/${plan.steps.length} ${t("steps")}`),
      el("div", { class: "goal" }, plan.goal),
      progress(done, plan.steps.length),
      el("ul", { class: "steps" }, plan.steps.map((s, i) => el("li", { class: s.done ? "done" : i === plan.current ? "current" : "" }, s.text))),
      el("details", {}, el("summary", {}, `${t("Acceptance criteria")} (${plan.criteria.length})`), el("ol", {}, plan.criteria.map((c) => el("li", {}, c))))));
    parts.push(section(t("Last verify"), plan.review ? el("span", {}, `${t("review")}: ${t(plan.review)}`) : null,
      el("div", { class: "last-verify" }, el("span", { class: `verdict ${cls}` }, label),
        el("span", { class: "muted" }, last.replace(/^(PASSED|FAILED|ALL DONE|not run yet)\s*/, "")))));
  }
  if (files.size) {
    parts.push(section(t("Files changed"), el("span", {}, files.size),
      el("ul", { class: "file-list" }, [...files].map(([path, f]) => el("li", {}, el("span", { class: "path", title: path }, path), counts(f.added, f.removed))))));
  }
  if (tree && tree.files.length) parts.push(fileTree(ctx));
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
  const panel = $("#side-panel");
  panel.hidden = !parts.length;
  panel.replaceChildren(...parts);
}

function sub(ctx, index, fields) {
  const subs = ctx.panel.subs;
  while (subs.length < index) subs.push({ question: `${t("Sub-question")} ${subs.length + 1}` });
  Object.assign(subs[index - 1], fields);
  renderPanel(ctx);
}

// After the report the notebook's source list is the truth (numbers, quotes).
async function loadSources(ctx, project) {
  const p = await api(`/api/projects/${encodeURIComponent(project)}`).catch(() => null);
  if (!p) return;
  ctx.panel.sources = new Map(p.sources.map((s) => [s.n, s]));
  renderPanel(ctx);
}

// -- events ---------------------------------------------------------------------------------------
function renderEvent(e, ctx) {
  switch (e.kind) {
    case "user":
      nextMessage(ctx);
      userBubble(e.text);
      break;
    case "steer": // sent while the agent was working
      userBubble(e.text);
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
      ctx.panel.subs = e.sub_questions.map((s) => ({ ...s }));
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
        ctx.panel.subs.push(...e.sub_questions.map((q) => ({ question: q })));
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
      $("#compact").hidden = !state.running;
      renderMeter(e);
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
      ctx.panel.plan = e;
      renderPanel(ctx);
      if (!ctx.panel.tree) refreshFiles(ctx);
      break;
    case "auto_verify": {
      const d = testCall(t("run by the program"));
      d.classList.remove("pending");
      testResult(d, e.output);
      append(el("div", { class: "card" }, el("div", { class: "calls" }, d)));
      break;
    }
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
    ...state.runList.map((r) => ({ label: chatTitle(r), hint: `${t(r.mode === "code" ? "code" : "research")} · ${t(r.status)}`, action: () => attach(r.id) })),
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
newChat();
