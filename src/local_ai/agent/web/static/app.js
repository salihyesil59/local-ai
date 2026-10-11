// Research Agent UI — vanilla JS, no external dependencies (works offline).
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v; // only for server-rendered (escaped) Markdown
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

const state = { status: {}, view: "new", currentRun: null, source: null, project: null, mode: "research", folders: [], targetFolder: null };

// Remembered per browser; storage may be unavailable, the page works without it.
const remember = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
const recall = (key) => { try { return localStorage.getItem(key) || ""; } catch { return ""; } };

// -- status -----------------------------------------------------------------------
async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const s = state.status;
    const box = $("#status");
    box.classList.toggle("err", !!s.error);
    box.textContent = s.error ? s.error : `${s.model || "model: auto"} · ${s.servers.join(", ") || "no servers"} · ${s.tools} tools${s.code ? "" : " · code mode off"}`;
    box.title = `Workspace: ${s.workspace}\nLM Studio: ${s.base_url}`;
  } catch (e) {
    $("#status").textContent = "server not reachable";
    $("#status").classList.add("err");
  }
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
    if (v === "new") { show("new"); renderRunsSidebar(); }
    if (v === "projects") {
      renderProjectsSidebar();
      if (state.project) openProject(state.project);
      else { show("project"); $("#view-project").replaceChildren(el("p", { class: "muted" }, "Pick a project on the left.")); }
    }
    if (v === "memory") { show("memory"); $("#side-list").replaceChildren(); loadMemory(); }
    if (v === "skills") { show("skills"); $("#side-list").replaceChildren(); loadSkills(); }
  })
);

// -- runs -----------------------------------------------------------------------------
// Chats are sorted into the user's folders, like in LM Studio or Goose; titles can be renamed.
const chatTitle = (r) => r.title || shortTitle(r.question);

function collapsedFolders() {
  try { return new Set(JSON.parse(recall("collapsed") || "[]")); } catch { return new Set(); }
}

function toggleFolder(id) {
  const set = collapsedFolders();
  set.has(id) ? set.delete(id) : set.add(id);
  remember("collapsed", JSON.stringify([...set]));
  renderRunsSidebar();
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
  try { await fn(); } catch (e) { alert(e.message); }
  renderRunsSidebar();
}

function chatMenu(anchor, r) {
  showMenu(anchor, [
    { label: "Rename", action: () => {
      const title = prompt("Chat title (empty = automatic)", r.title || shortTitle(r.question));
      if (title !== null) guarded(() => api(`/api/runs/${r.id}`, { method: "PATCH", body: JSON.stringify({ title }) }));
    } },
    { label: "Move to folder…", action: () => showMenu(anchor, [
      ...state.folders.filter((f) => f.id !== r.folder).map((f) => ({ label: f.name, action: () => guarded(() => api(`/api/runs/${r.id}`, { method: "PATCH", body: JSON.stringify({ folder: f.id }) })) })),
      ...(r.folder ? [{ label: "No folder", action: () => guarded(() => api(`/api/runs/${r.id}`, { method: "PATCH", body: JSON.stringify({ folder: null }) })) }] : []),
      { label: "New folder…", action: () => newFolder(async (f) => api(`/api/runs/${r.id}`, { method: "PATCH", body: JSON.stringify({ folder: f.id }) })) },
    ]) },
    { label: "Delete", danger: true, action: () => {
      if (confirm(`Delete this chat?\n${chatTitle(r)}`)) guarded(async () => {
        await api(`/api/runs/${r.id}`, { method: "DELETE" });
        if (state.currentRun === r.id) { state.currentRun = null; $("#timeline").replaceChildren(); $("#plan-panel").hidden = true; $("#report").hidden = true; }
      });
    } },
  ]);
}

function folderMenu(anchor, f) {
  showMenu(anchor, [
    { label: "Rename", action: () => {
      const name = prompt("Folder name", f.name);
      if (name && name.trim()) guarded(() => api(`/api/folders/${f.id}`, { method: "PATCH", body: JSON.stringify({ name }) }));
    } },
    { label: "Delete folder (chats stay)", danger: true, action: () => {
      if (confirm(`Delete the folder "${f.name}"? Its chats move out of it.`)) guarded(async () => {
        await api(`/api/folders/${f.id}`, { method: "DELETE" });
        if (state.targetFolder === f.id) setTarget(null);
      });
    } },
  ]);
}

function newFolder(then) {
  const name = prompt("Folder name");
  if (!name || !name.trim()) return;
  guarded(async () => {
    const folder = await api("/api/folders", { method: "POST", body: JSON.stringify({ name }) });
    if (then) await then(folder);
  });
}

// New chats go into the target folder (set with the + of a folder).
function setTarget(id) {
  state.targetFolder = id;
  remember("target", id || "");
  renderTarget();
  renderRunsSidebar();
}

function renderTarget() {
  const box = $("#target");
  const folder = state.folders.find((f) => f.id === state.targetFolder);
  if (!folder) { state.targetFolder = null; box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren("New chat goes into folder: ", el("strong", {}, folder.name), " ",
    el("button", { class: "icon", type: "button", title: "No folder", onclick: () => setTarget(null) }, "✕"));
}

function chatRow(r) {
  return el("div", { class: "side-row" + (state.currentRun === r.id ? " active" : "") },
    el("button", { class: "side-item", onclick: () => attach(r.id), title: r.question },
      el("span", { class: "t" }, chatTitle(r)), el("small", {}, `${r.mode === "code" ? "code" : "research"} · ${r.status}`)),
    el("button", { class: "icon", title: "Rename, move, delete", onclick: (ev) => chatMenu(ev.currentTarget, r) }, "⋯"));
}

async function renderRunsSidebar() {
  const [runs, folders] = await Promise.all([api("/api/runs").catch(() => []), api("/api/folders").catch(() => [])]);
  state.folders = folders;
  renderTarget();
  const collapsed = collapsedFolders();
  const items = [el("div", { class: "side-head" }, el("span", { class: "muted" }, "Chats"),
    el("button", { class: "ghost small", type: "button", onclick: () => newFolder() }, "+ Folder"))];
  for (const f of folders) {
    const inside = runs.filter((r) => r.folder === f.id);
    items.push(el("div", { class: "folder" + (state.targetFolder === f.id ? " target" : "") },
      el("button", { class: "folder-name", onclick: () => toggleFolder(f.id) }, collapsed.has(f.id) ? "▸ " : "▾ ", f.name, el("small", {}, ` ${inside.length}`)),
      el("button", { class: "icon", title: "New chats go into this folder", onclick: () => setTarget(state.targetFolder === f.id ? null : f.id) }, "＋"),
      el("button", { class: "icon", title: "Folder menu", onclick: (ev) => folderMenu(ev.currentTarget, f) }, "⋯")));
    if (!collapsed.has(f.id)) items.push(...inside.map((r) => el("div", { class: "indent" }, chatRow(r))));
  }
  const loose = runs.filter((r) => !folders.some((f) => f.id === r.folder));
  if (folders.length && loose.length) items.push(el("div", { class: "group-label muted" }, "No folder"));
  items.push(...loose.map(chatRow));
  if (!runs.length && !folders.length) items.push(el("p", { class: "muted" }, "Your chats appear here."));
  $("#side-list").replaceChildren(...items);
}

// A short sidebar title: the first line of the prompt, cut at a word, like a chat title.
function shortTitle(text, max = 40) {
  const line = (text || "").split("\n").map((l) => l.trim()).find(Boolean) || "";
  if (line.length <= max) return line;
  const cut = line.slice(0, max);
  return cut.slice(0, Math.max(cut.lastIndexOf(" "), max / 2)).replace(/[\s.,;:]+$/, "") + "…";
}

function setMode(mode) {
  state.mode = mode;
  remember("mode", mode);
  const code = mode === "code";
  document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  document.querySelectorAll(".code-only").forEach((n) => (n.hidden = !code));
  document.querySelectorAll(".research-only").forEach((n) => (n.hidden = code));
  $("#run").textContent = code ? "Code" : "Research";
  $("#question").required = !code; // code mode may continue an existing plan without a new task
  $("#question").placeholder = code
    ? "What to build, change or fix. Leave empty to continue the plan in the folder."
    : "Ask a research question — any language. e.g. “Hidrojen atomunun taban durum enerjisini türet.”";
}
document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

$("#browse").addEventListener("click", async () => {
  const button = $("#browse");
  button.disabled = true;
  button.textContent = "Waiting for the dialog…";
  try {
    const { path } = await api("/api/pick-folder", { method: "POST", body: JSON.stringify({ initial: $("#folder").value.trim() }) });
    if (path) { $("#folder").value = path; remember("folder", path); }
  } catch (e) {
    alert(e.message);
  } finally {
    button.disabled = false;
    button.textContent = "Browse…";
  }
});

$("#ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const question = $("#question").value.trim();
  const code = state.mode === "code";
  if (!question && !code) return;
  try {
    let body;
    if (code) {
      const folder = $("#folder").value.trim();
      remember("folder", folder);
      body = { mode: "code", question, folder, chat_folder: state.targetFolder };
    } else {
      body = { chat_folder: state.targetFolder, question, project: $("#project").value.trim(), fast: $("#fast").checked, quick: $("#quick").checked, parallel: +$("#parallel").value };
    }
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify(body) });
    attach(run.id);
    renderRunsSidebar();
  } catch (e) {
    alert(e.message);
  }
});

$("#cancel").addEventListener("click", () => state.currentRun && api(`/api/runs/${state.currentRun}/cancel`, { method: "POST" }));

function attach(runId) {
  show("new");
  if (state.source) state.source.close();
  state.currentRun = runId;
  const tl = $("#timeline");
  tl.replaceChildren();
  $("#report").hidden = true;
  $("#plan-panel").hidden = true;
  const ctx = { cards: {}, calls: {}, current: null, live: {} };
  setRunning(true);
  const source = new EventSource(`/api/runs/${runId}/events`);
  state.source = source;
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    renderEvent(event, ctx);
    if (event.kind === "end") { source.close(); setRunning(false); renderRunsSidebar(); }
  };
  source.onerror = () => { source.close(); setRunning(false); };
}

$("#compact").addEventListener("click", async () => {
  if (!state.currentRun) return;
  try {
    await api(`/api/runs/${state.currentRun}/compact`, { method: "POST" });
    $("#timeline").append(el("div", { class: "log" }, "Compaction requested: it happens before the next model reply."));
  } catch (e) { alert(e.message); }
});

function setRunning(on) {
  $("#run").disabled = on;
  $("#cancel").hidden = !on;
  if (!on) $("#compact").hidden = true; // shown by the first "turn" event, which only code runs send
}

function card(ctx, key, title, badge) {
  if (!ctx.cards[key]) {
    const c = el("div", { class: "card" }, el("h4", {}, title, badge ? el("span", { class: "badge" }, badge) : null), el("div", { class: "calls" }));
    ctx.cards[key] = c;
    $("#timeline").append(c);
  }
  return ctx.cards[key];
}

function liveBox(ctx, key) {
  if (!ctx.live[key]) {
    const box = el("div", { class: "live" }, el("div", { class: "live-thinking" }), el("div", { class: "live-text" }));
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
  for (const [cls, label] of [["live-thinking", "Thinking"], ["live-text", "Model output"]]) {
    const text = box.querySelector(`.${cls}`).textContent;
    if (text.trim()) parts.push(el("details", { class: `call ${cls === "live-thinking" ? "thinking" : ""}` }, el("summary", {}, `${label} (${text.length} chars)`), el("pre", {}, text)));
  }
  box.replaceWith(...parts);
}

function renderEvent(e, ctx) {
  const tl = $("#timeline");
  switch (e.kind) {
    case "token": {
      const box = liveBox(ctx, e.index ?? "main");
      box.querySelector(e.thinking ? ".live-thinking" : ".live-text").textContent += e.text;
      break;
    }
    case "stream_end":
      closeLive(ctx, e.index ?? "main");
      break;
    case "status":
      break;
    case "phase":
      tl.append(el("div", { class: "phase" }, e.title));
      ctx.current = null;
      break;
    case "memory":
      tl.append(el("details", { class: "card" }, el("summary", {}, "Recalled from memory"), el("pre", {}, e.text)));
      break;
    case "plan":
      tl.append(el("div", { class: "card" }, el("h4", {}, "Plan"),
        el("ol", {}, e.sub_questions.map((s) => el("li", {}, s.question, s.skill ? el("span", { class: "badge" }, s.skill) : null)))));
      break;
    case "subquestion":
      ctx.current = e.index;
      card(ctx, e.index, `${e.index}/${e.total} · ${e.question}`, e.skill);
      break;
    case "tool_call": {
      const key = e.index ?? ctx.current ?? "misc";
      const c = ctx.cards[key] || card(ctx, key, "Tool calls");
      const d = el("details", { class: "call pending" }, el("summary", {}, `${e.name} ${e.arguments}`), el("pre", {}, prettyArgs(e.arguments)));
      ctx.calls[e.id + key] = d;
      c.querySelector(".calls").append(d);
      break;
    }
    case "tool_result": {
      const key = e.index ?? ctx.current ?? "misc";
      const d = ctx.calls[e.id + key];
      if (d) {
        d.classList.remove("pending");
        if (e.error) d.classList.add("error");
        d.append(el("pre", {}, e.output));
      }
      break;
    }
    case "findings": {
      const c = ctx.cards[e.index] || card(ctx, e.index, e.question);
      c.append(el("div", { class: "findings", html: e.html }));
      break;
    }
    case "gaps":
      if (e.sub_questions.length) tl.append(el("div", { class: "card" }, el("h4", {}, "Gaps found — researching more"), el("ul", {}, e.sub_questions.map((q) => el("li", {}, q)))));
      break;
    case "problems":
      if (e.items.length) tl.append(el("div", { class: "card" }, el("h4", {}, e.source === "reviewer" ? "Reviewer" : "Automatic checks"), el("ul", { class: "problems" }, e.items.map((p) => el("li", {}, p)))));
      else tl.append(el("div", { class: "log success" }, e.source === "reviewer" ? "Reviewer found no issues." : "Checks passed."));
      break;
    case "log":
      tl.append(el("div", { class: `log ${e.level || ""}` }, e.text));
      break;
    case "notebook": {
      const url = fileUrl(e.path);
      tl.append(el("div", { class: "log" }, "Computations notebook: ", url ? el("a", { href: url, download: "" }, e.path) : e.path));
      break;
    }
    case "report":
      showReport($("#report"), e.html, e.project, e.cache_hits);
      break;
    case "answer": {
      const box = $("#report");
      box.hidden = false;
      box.replaceChildren(el("div", { html: e.html }));
      box.scrollIntoView({ behavior: "smooth", block: "start" });
      break;
    }
    case "turn": {
      $("#compact").hidden = false;
      const key = `turn-${e.turn}`;
      const info = [`${Math.round(e.seconds)} s`, e.tokens ? `${e.tokens} tokens` : null, e.finish === "length" ? "cut off" : null].filter(Boolean).join(" · ");
      if (e.tool_calls.length || e.finish === "length") {
        card(ctx, key, `Turn ${e.turn}`, info);
        ctx.current = key;
      } else {
        tl.append(el("div", { class: "log" }, `Turn ${e.turn} · ${info} · no tool call`));
      }
      break;
    }
    case "state":
      renderPlan(e);
      break;
    case "auto_verify":
      tl.append(el("details", { class: "card" }, el("summary", {}, "The program ran verify for the model"), el("pre", {}, e.output)));
      break;
    case "compact":
      tl.append(el("div", { class: "log" }, e.dropped
        ? `Context compacted${e.manual ? " on request" : ""}: ${e.dropped} old messages replaced by the plan, the file list${e.noted ? " and the model's notes" : ""}.`
        : "Nothing to compact yet."));
      break;
    case "outcome":
      tl.append(el("div", { class: `log ${e.finished ? "success" : "warning"}` }, e.finished ? "Finished: verification passes." : `Stopped: ${e.reason}.`));
      break;
    case "error":
      tl.append(el("div", { class: "card error-box" }, e.text));
      break;
    case "end":
      if (e.status !== "done") tl.append(el("div", { class: "log warning" }, `Run ${e.status}.`));
      break;
  }
  if (state.view === "new" && e.kind !== "report" && e.kind !== "answer") window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function renderPlan(e) {
  const panel = $("#plan-panel");
  panel.hidden = false;
  const last = e.finished ? "ALL DONE" : e.last;
  panel.replaceChildren(
    el("h4", {}, "Plan", e.review ? el("span", { class: "badge" }, `review: ${e.review}`) : null),
    el("div", { class: "muted" }, e.goal),
    el("ul", { class: "steps" }, e.steps.map((s, i) => el("li", { class: s.done ? "done" : i === e.current ? "current" : "" }, s.text))),
    el("details", {}, el("summary", {}, `Acceptance criteria (${e.criteria.length})`), el("ol", {}, e.criteria.map((c) => el("li", {}, c)))),
    el("div", { class: `last ${e.finished ? "good" : /FAILED/.test(last) ? "bad" : ""}` }, `Last verify: ${last}`)
  );
}

function prettyArgs(args) {
  try { return JSON.stringify(JSON.parse(args), null, 2); } catch { return args; }
}

function exportBar(project, extra = []) {
  const bar = el("div", { class: "toolbar" }, el("span", { class: "muted" }, "Export:"));
  for (const fmt of state.status.formats || ["html", "md", "bib"]) {
    bar.append(el("button", {
      class: "ghost", onclick: async () => {
        try {
          const r = await api(`/api/projects/${encodeURIComponent(project)}/export?format=${fmt}`, { method: "POST" });
          window.open(r.url, "_blank");
        } catch (err) { alert(err.message); }
      },
    }, fmt.toUpperCase()));
  }
  bar.append(...extra);
  return bar;
}

function showReport(container, html, project, cacheHits) {
  container.hidden = false;
  const extras = [];
  if (cacheHits) extras.push(el("span", { class: "muted" }, `· ${cacheHits} cached tool calls`));
  container.replaceChildren(exportBar(project, extras), el("div", { html }));
  container.scrollIntoView({ behavior: "smooth", block: "start" });
}

// -- projects -------------------------------------------------------------------------
async function renderProjectsSidebar() {
  const projects = await api("/api/projects").catch(() => []);
  $("#side-list").replaceChildren(
    ...(projects.length ? projects.map((p) =>
      el("button", { class: "side-item" + (state.project === p.name ? " active" : ""), onclick: () => openProject(p.name) },
        el("span", { class: "t", title: p.question || p.name }, shortTitle(p.question || p.name)), el("small", {}, p.has_report ? p.name : `${p.name} · no report yet`))) : [el("p", { class: "muted" }, "No projects yet.")])
  );
}

async function openProject(name) {
  state.project = name;
  show("project");
  renderProjectsSidebar();
  const view = $("#view-project");
  view.replaceChildren(el("p", { class: "muted" }, "Loading…"));
  const p = await api(`/api/projects/${encodeURIComponent(name)}`);
  const extras = [];
  if (p.notebook) extras.push(el("a", { class: "ghost", href: `/files/${encodeURIComponent(p.name)}/computations.ipynb`, download: "" }, "Notebook (.ipynb)"));
  extras.push(el("button", { class: "ghost", onclick: () => resume(p.name) }, "Resume"));
  const parts = [el("h2", {}, p.plan.question || p.name)];
  if (p.report_html) parts.push(el("article", { class: "report" }, exportBar(p.name, extras), el("div", { html: p.report_html })));
  else parts.push(el("div", { class: "toolbar" }, ...extras));
  if (p.sources.length) {
    parts.push(el("h3", {}, "Sources"), el("table", { class: "sources" }, p.sources.map((s) =>
      el("tr", {}, el("td", {}, `[${s.n}]`), el("td", {}, el("strong", {}, s.title), el("div", { class: "muted" },
        [s.authors, s.year, s.origin, s.locator].filter(Boolean).join(" · ")), ...(s.quotes || []).map((q) => el("div", {}, `“${q}”`)))))));
  }
  if (p.findings.length) {
    parts.push(el("h3", {}, "Findings"), ...p.findings.map((f) => el("details", { class: "card" }, el("summary", {}, f.name), el("div", { html: f.html }))));
  }
  view.replaceChildren(...parts);
}

async function resume(project) {
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify({ project, resume: true }) });
    attach(run.id);
  } catch (e) { alert(e.message); }
}

// -- memory & skills ------------------------------------------------------------------
async function loadMemory(q = "") {
  const items = await api(`/api/memory?q=${encodeURIComponent(q)}`).catch(() => []);
  $("#memory-list").replaceChildren(
    ...(items.length ? items.map((m) => el("li", {},
      el("span", { class: `badge ${m.kind}` }, m.kind),
      el("div", {}, m.text, m.sources ? el("div", { class: "muted" }, m.sources) : null),
      el("button", { class: "ghost danger", title: "Forget", onclick: async () => {
        if (!confirm("Forget this memory?")) return;
        await api(`/api/memory/${m.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
        loadMemory($("#memory-q").value);
      } }, "Forget"))) : [el("li", { class: "muted" }, "Nothing in memory yet.")])
  );
}
$("#memory-search").addEventListener("submit", (ev) => { ev.preventDefault(); loadMemory($("#memory-q").value.trim()); });

async function loadSkills() {
  const skills = await api("/api/skills").catch(() => []);
  $("#skills-list").replaceChildren(...skills.map((s) =>
    el("div", { class: "card" }, el("h4", {}, s.name), el("div", {}, s.description), s.when_to_use ? el("div", { class: "muted" }, `Use when: ${s.when_to_use}`) : null)));
}

// -- init -------------------------------------------------------------------------------
$("#folder").value = recall("folder");
state.targetFolder = recall("target") || null;
setMode(recall("mode") === "code" ? "code" : "research");
loadStatus();
setInterval(loadStatus, 15000);
renderRunsSidebar();
$("#question").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) $("#ask").requestSubmit(); });
