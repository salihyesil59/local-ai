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

const state = { status: {}, view: "new", currentRun: null, source: null, project: null };

// -- status -----------------------------------------------------------------------
async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const s = state.status;
    const box = $("#status");
    box.classList.toggle("err", !!s.error);
    box.textContent = s.error ? s.error : `${s.model || "model: auto"} · ${s.servers.join(", ") || "no servers"} · ${s.tools} tools`;
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
async function renderRunsSidebar() {
  const runs = await api("/api/runs").catch(() => []);
  $("#side-list").replaceChildren(
    ...(runs.length ? runs.map((r) =>
      el("button", { class: "side-item" + (state.currentRun === r.id ? " active" : ""), onclick: () => attach(r.id) },
        r.question, el("small", {}, r.status))) : [el("p", { class: "muted" }, "Runs from this session appear here.")])
  );
}

$("#ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const question = $("#question").value.trim();
  if (!question) return;
  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({ question, project: $("#project").value.trim(), fast: $("#fast").checked, quick: $("#quick").checked, parallel: +$("#parallel").value }),
    });
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

function setRunning(on) {
  $("#run").disabled = on;
  $("#cancel").hidden = !on;
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
    case "error":
      tl.append(el("div", { class: "card error-box" }, e.text));
      break;
    case "end":
      if (e.status !== "done") tl.append(el("div", { class: "log warning" }, `Run ${e.status}.`));
      break;
  }
  if (state.view === "new" && e.kind !== "report" && e.kind !== "answer") window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
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
        p.question || p.name, el("small", {}, p.has_report ? p.name : `${p.name} · no report yet`))) : [el("p", { class: "muted" }, "No projects yet.")])
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
loadStatus();
setInterval(loadStatus, 15000);
renderRunsSidebar();
$("#question").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) $("#ask").requestSubmit(); });
