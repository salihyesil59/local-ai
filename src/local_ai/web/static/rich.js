// Rich text for the page: Markdown while it streams, colours for code, LaTeX (drawn by the bundled KaTeX).
// No network: everything here and in vendor/ is served by the local server.
"use strict";

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// -- code colours ---------------------------------------------------------------------------------
// A small tokenizer per language family: comments, strings, numbers, keywords, called names.
const words = (s) => new Set(s.split(" "));
const FAMILIES = {
  py: {
    re: /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d[\w.]*)|([A-Za-z_]\w*)/g,
    keywords: words("and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None self"),
  },
  js: {
    re: /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|(\b\d[\w.]*)|([A-Za-z_$][\w$]*)/g,
    keywords: words("async await break case catch class const continue default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while yield true false null undefined"),
  },
  c: {
    re: /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d[\w.]*)|([A-Za-z_]\w*)/g,
    keywords: words("auto bool break case char class const continue default do double else enum extern float fn for func go if impl import inline int interface let long match mut namespace new package private protected pub public return short signed sizeof static struct switch template this type typedef union unsigned use using var void while true false null nil nullptr"),
  },
  sh: {
    re: /(#[^\n]*)|("(?:\\.|[^"\\\n])*"|'[^'\n]*')|(\b\d[\w.]*)|([A-Za-z_][\w-]*)/g,
    keywords: words("if then else elif fi for in do done while case esac function return export local echo cd true false null"),
  },
};
const LANGUAGES = {
  python: "py", py: "py", javascript: "js", js: "js", typescript: "js", ts: "js", jsx: "js", tsx: "js", json: "js",
  bash: "sh", sh: "sh", shell: "sh", powershell: "sh", yaml: "sh", yml: "sh", toml: "sh",
  c: "c", cpp: "c", "c++": "c", java: "c", go: "c", rust: "c", rs: "c", csharp: "c", cs: "c",
};

function highlight(code, language) {
  const family = FAMILIES[LANGUAGES[(language || "").toLowerCase()]];
  if (!family) return esc(code);
  const span = (cls, text) => `<span class="tok-${cls}">${esc(text)}</span>`;
  let out = "", last = 0;
  for (const m of code.matchAll(family.re)) {
    out += esc(code.slice(last, m.index));
    last = m.index + m[0].length;
    if (m[1]) out += span("c", m[0]);
    else if (m[2]) out += span("s", m[0]);
    else if (m[3]) out += span("n", m[0]);
    else if (family.keywords.has(m[0])) out += span("k", m[0]);
    else if (code[last] === "(") out += span("f", m[0]);
    else out += esc(m[0]);
  }
  return out + esc(code.slice(last));
}

// -- Markdown -------------------------------------------------------------------------------------
// The subset models write: headings, paragraphs, lists, quotes, tables, fenced code, emphasis, links, LaTeX.
// Text is escaped before any tag is added, so model output cannot inject HTML.
// Code spans, or LaTeX: $$..$$, \[..\], \(..\), $..$ (not prices like "$5 and $10"). Same rule as the server's.
const SPAN_RE = /(`+)([\s\S]*?[^`])\1(?!`)|\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|(?<![\\$\w])\$(?!\s)[^$\n`]+?(?<![\s\\])\$(?!\d)/g;
const LINK_OK = /^(https?:\/\/|\/|#)/;

function inline(text) {
  const kept = [];
  const keep = (html) => `\u0000${kept.push(html) - 1}\u0000`;
  text = text.replace(SPAN_RE, (m, ticks, code) =>
    keep(ticks ? `<code>${esc(code.trim())}</code>` : `<span class="math">${esc(m)}</span>`));
  text = esc(text)
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, label, url) => (LINK_OK.test(url) ? `<a href="${url}" target="_blank" rel="noopener">${label}</a>` : m))
    .replace(/\*\*(?!\s)([\s\S]+?)(?<!\s)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<![\w*])\*(?![\s*])([^*\n]+?)(?<![\s*])\*(?![\w*])/g, "<em>$1</em>")
    .replace(/(?<![\w_])_(?![\s_])([^_\n]+?)(?<![\s_])_(?![\w_])/g, "<em>$1</em>")
    .replace(/~~(?!\s)([^~\n]+?)(?<!\s)~~/g, "<del>$1</del>");
  return text.replace(/\u0000(\d+)\u0000/g, (m, i) => kept[+i]);
}

const FENCE = /^\s*(```|~~~)\s*([\w+#-]*)\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const cells = (line) => line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim());
const startsBlock = (lines, i) =>
  FENCE.test(lines[i]) || HEADING.test(lines[i]) || RULE.test(lines[i]) || ITEM.test(lines[i]) || /^\s*>/.test(lines[i]) ||
  (lines[i].includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1]));

function blocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (!line.trim()) { i++; continue; }
    if ((m = FENCE.exec(line))) {
      const body = [];
      for (i++; i < lines.length && lines[i].trim() !== m[1]; i++) body.push(lines[i]);
      i++; // the closing fence; a fence still open (the text is streaming) runs to the end
      out.push(`<pre><code>${highlight(body.join("\n"), m[2])}</code></pre>`);
    } else if ((m = HEADING.exec(line))) {
      out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
      i++;
    } else if (RULE.test(line)) {
      out.push("<hr>");
      i++;
    } else if (/^\s*>/.test(line)) {
      const quoted = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) quoted.push(lines[i].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${blocks(quoted)}</blockquote>`);
    } else if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const head = cells(line), rows = [];
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(cells(lines[i]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((r) => `<tr>${head.map((_, k) => `<td>${inline(r[k] || "")}</td>`).join("")}</tr>`).join("") + "</tbody></table>");
    } else if ((m = ITEM.exec(line))) {
      // A list: every item is its first line plus the more deeply indented lines after it (text or a nested list).
      const indent = m[1].length, ordered = /\d/.test(m[2]), items = [];
      while (i < lines.length && (m = ITEM.exec(lines[i])) && m[1].length === indent && /\d/.test(m[2]) === ordered) {
        const item = [m[3]];
        for (i++; i < lines.length; i++) {
          const deeper = lines[i].search(/\S/) > indent;
          if (deeper || (!lines[i].trim() && i + 1 < lines.length && lines[i + 1].search(/\S/) > indent)) item.push(lines[i].slice(Math.min(indent + 2, Math.max(0, lines[i].search(/\S/)))));
          else break;
        }
        items.push(item.length === 1 ? inline(item[0]) : inline(item[0]) + blocks(item.slice(1)));
      }
      const start = ordered && parseInt(ITEM.exec(line)[2], 10) !== 1 ? ` start="${parseInt(ITEM.exec(line)[2], 10)}"` : "";
      out.push(`<${ordered ? "ol" + start : "ul"}>${items.map((t) => `<li>${t}</li>`).join("")}</${ordered ? "ol" : "ul"}>`);
    } else {
      const para = [line];
      for (i++; i < lines.length && lines[i].trim() && !startsBlock(lines, i); i++) para.push(lines[i]);
      out.push(`<p>${inline(para.join("\n"))}</p>`);
    }
  }
  return out.join("");
}

function markdownToHtml(text) {
  return blocks(String(text || "").replace(/\u0000/g, "").replace(/\r\n?/g, "\n").split("\n"));
}

// -- LaTeX ----------------------------------------------------------------------------------------
// <span class="math"> holds the formula as written, with its delimiters; KaTeX draws it in place.
// A streamed reply is drawn again and again: each formula is laid out by KaTeX once and then taken from here.
const mathCache = new Map();

function renderMath(root) {
  if (!window.katex) return;
  for (const node of root.querySelectorAll("span.math:not(.drawn)")) {
    const source = node.textContent;
    let html = mathCache.get(source);
    if (html === undefined) {
      const display = source.startsWith("$$") || source.startsWith("\\[");
      const tex = source.replace(/^(\$\$|\$|\\\[|\\\()/, "").replace(/(\$\$|\$|\\\]|\\\))$/, "");
      try {
        html = window.katex.renderToString(tex, { displayMode: display, throwOnError: false });
      } catch {
        html = null; // not LaTeX after all: the text stays as written
      }
      if (mathCache.size > 2000) mathCache.clear();
      mathCache.set(source, html);
    }
    if (html !== null) {
      node.innerHTML = html; // KaTeX's own markup, built from the formula
      node.title = source;
    }
    node.classList.add("drawn");
  }
}

// A Copy button on every code block (the page copies the block's text when it is pressed).
function addCopyButtons(root) {
  for (const pre of root.querySelectorAll("pre:not(:has(> .copy))")) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy";
    button.textContent = typeof t === "function" ? t("Copy") : "Copy";
    pre.append(button);
  }
}

// HTML rendered by the server (reports, answers, findings): colour its code blocks and draw its formulas.
function enhance(root) {
  for (const code of root.querySelectorAll('pre > code[class*="language-"]:not(.coloured)')) {
    const language = [...code.classList].find((c) => c.startsWith("language-")).slice(9);
    code.innerHTML = highlight(code.textContent, language);
    code.classList.add("coloured");
  }
  renderMath(root);
  addCopyButtons(root);
}

// Markdown text (model output) into a node.
function renderRich(node, text) {
  node.innerHTML = markdownToHtml(text);
  renderMath(node);
  addCopyButtons(node);
}

// Markdown that is still streaming, drawn again as it grows. The text up to its last blank line outside a code
// block does not change any more: it is drawn once and kept, and only what follows is drawn again. A long reply
// with many formulas costs as much per draw as its last paragraph.
function renderStream(node, text) {
  let s = node.stream;
  if (!s || !text.startsWith(s.stable)) {
    node.replaceChildren();
    s = node.stream = { stable: "", tail: [] };
  }
  const cut = stableEnd(text, s.stable.length);
  for (const n of s.tail) n.remove();
  if (cut > s.stable.length) {
    node.append(fragment(text.slice(s.stable.length, cut)));
    s.stable = text.slice(0, cut);
  }
  const tail = fragment(text.slice(cut));
  s.tail = [...tail.childNodes];
  node.append(tail);
}

function fragment(markdown) {
  const template = document.createElement("template");
  template.innerHTML = markdownToHtml(markdown);
  renderMath(template.content);
  addCopyButtons(template.content);
  return template.content;
}

// Where the settled part of streamed Markdown ends: after its last blank line that is not inside a code block.
function stableEnd(text, from) {
  let end = from, open = null;
  for (let at = from, next; at < text.length; at = next + 1) {
    next = text.indexOf("\n", at);
    if (next < 0) break; // the last line is still being written
    const line = text.slice(at, next);
    if (open) { if (line.trim() === open) open = null; } // closed as blocks() closes it
    else if (FENCE.test(line)) open = FENCE.exec(line)[1];
    else if (!line.trim()) end = next + 1;
  }
  return end;
}
