"use strict";
// Slide studio – the design pipeline as a sequence of screens. Every action is an MCP tool call via /api/call.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const url = (rel) => `/files/${rel}?t=${Date.now()}`;
const hex = (h) => "#" + String(h || "000000").replace("#", "");

const STEPS = [
  ["paper", "Sources"], ["brief", "Theme"], ["kickoff", "Kick-off"], ["palette", "Palette"], ["type", "Type"],
  ["graphics", "Graphics"], ["visual", "Shapes"], ["layout", "Main style"], ["spec", "Content"], ["motion", "Deck look"], ["refine", "Polish"], ["deck", "Final"],
];
const STAGES = ["palette", "type", "graphics", "visual", "layout", "motion"];
const NEXT = Object.fromEntries(STEPS.map(([id], i) => [id, STEPS[i + 1]?.[0]]));
const COPY = {
  palette: ["Pick a colour system", "Background, text, the paper's own method, comparisons and call-outs."],
  type: ["Pick a type pairing", "Headings, body text and numbers, on your palette."],
  graphics: ["How should data look?", "Charts, tables and figure frames. One chart, one claim."],
  visual: ["Pick a shape language", "Boxes, arrows and emphasis for diagrams and cards."],
  layout: ["Pick a main style", "Your choices so far, composed into slides with placeholder text. Flip through each."],
  motion: ["Pick the deck", "Four near-final decks with your content, each with its own look and motion. Play the video or flip through the slides."],
};
const DIRS = [
  ["academic", "Calm, academic", "Light, restrained, lots of air, classic type."],
  ["bold", "Bold, expressive", "High contrast, strong colour, poster-like headlines."],
  ["tech", "Dark, tech", "Dark ground, vivid accent, geometric and mono type."],
  ["minimal", "Minimal, clean", "Few colours, lots of white space, fine lines."],
];

let S = { session: null, status: null, step: null, brief: null };
const cut = (t, n) => { t = String(t || ""); return t.length > n ? t.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : t; };
const kw = (i, dflt) => cut((S.brief?.keywords || [])[i] || dflt, 18);

// ------------------------------------------------------------------ API
async function call(server, tool, args = {}, onLog = null) {
  const r = await fetch("/api/call", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server, tool, args }) });
  const d = await r.json();
  if (d.error) throw new Error(d.error);
  if (!d.job) return d.result;
  for (;;) {
    const j = await (await fetch(`/api/jobs/${d.job}`)).json();
    if (onLog && j.log.length) onLog(j.log[j.log.length - 1]);
    if (j.state === "done") return j.result;
    if (j.state === "error") throw new Error(j.error);
    await new Promise((res) => setTimeout(res, 900));
  }
}

async function refresh() {
  S.status = S.session ? await call("content", "session_status", { session: S.session }) : null;
  drawRail();
}

const done = (id) => S.status?.steps.find((s) => s.id === id)?.done;
function firstOpen() {
  const s = S.status?.steps.find((x) => !x.done);
  return s ? s.id : "deck";
}

// ------------------------------------------------------------------ shell
function drawRail() {
  const rail = $("#rail");
  if (!S.status) { rail.innerHTML = ""; return; }
  const open = firstOpen();
  const reach = STEPS.findIndex(([id]) => id === open);
  rail.innerHTML = STEPS.map(([id, label], i) =>
    `<button data-step="${id}" class="${done(id) ? "done" : ""} ${S.step === id ? "here" : ""}" ${i > reach ? "disabled" : ""}>${label}</button>`).join("");
  $$("button", rail).forEach((b) => b.addEventListener("click", () => go(b.dataset.step)));
}

function screen(html, center = false) {
  const st = $("#stage");
  st.innerHTML = `<section class="screen enter">${html}</section>`;
  window.scrollTo({ top: 0 });
  return $(".screen", st);
}

function working(title, sub = "") {
  const el = screen(`<div class="head center"><h1>${esc(title)}</h1>${sub ? `<p class="sub">${esc(sub)}</p>` : ""}</div>
    <div class="working"><div class="pulse"></div><div class="log"></div></div>`);
  return (msg) => { const l = $(".log", el); if (l) l.textContent = msg; };
}

function failed(title, err, retry) {
  const el = screen(`<div class="head center"><h1>${esc(title)}</h1></div><div class="error">${esc(err.message || err)}</div>
    <div class="actions" style="margin-top:20px"><button class="btn primary" id="retry">Try again</button></div>`);
  $("#retry", el).addEventListener("click", retry);
}

async function go(step) {
  S.step = step;
  drawRail();
  history.replaceState(null, "", S.session ? `#${S.session}/${step}` : "#");
  const view = { paper: S.session ? viewSources : viewStart, brief: viewBrief, kickoff: viewKickoff, spec: viewSpec, refine: viewRefine, deck: viewDeck }[step];
  try {
    await (view ? view() : viewStage(step));
  } catch (e) {
    failed("Something went wrong", e, () => go(step));
  }
}

async function openSession(name, step) {
  S.session = name;
  S.brief = await call("agents", "get_brief", { session: name }).catch(() => null);
  try { localStorage.setItem("sdl.session", name); } catch { /* private mode */ }
  await refresh();
  env();
  await go(step || firstOpen());
}

async function env() {
  const bits = [];
  try {
    const pick = aiDefault();                      // no session yet: the choice on the first screen
    const l = await call("agents", "llm_status", S.session ? { session: S.session } : { provider: pick.provider });
    if (!S.session) l.model = pick.model;
    const name = { replay: "replay", claude_cli: "Claude Code", anthropic: "Claude API" }[l.provider] || l.provider;
    bits.push(`<span title="${esc(l.hint || l.model)}"><i class="${l.ready ? "" : "bad"}"></i>AI · ${esc(name)} ${esc(l.model || "")}</span>`);
  } catch { bits.push(`<span><i class="bad"></i>AI</span>`); }
  try {
    const r = await call("design", "renderer");
    bits.push(`<span><i class="${r.renderer ? "" : "bad"}"></i>${r.renderer === "powerpoint" ? "PowerPoint" : esc(r.renderer || "no renderer")}</span>`);
  } catch { bits.push(`<span><i class="bad"></i>render</span>`); }
  $("#env").innerHTML = bits.join("");
}

// ------------------------------------------------------------------ fonts for the boards
const loadedFonts = new Set();
function fontFamily(name, files) {
  const fam = "sdl " + name;
  if (files && files[name] && !loadedFonts.has(name)) {
    loadedFonts.add(name);
    const f = files[name];
    const css = `@font-face{font-family:"${fam}";src:url(/files/${f.regular}) format("truetype");font-weight:400;font-display:block}
      @font-face{font-family:"${fam}";src:url(/files/${f.bold}) format("truetype");font-weight:700;font-display:block}`;
    document.head.insertAdjacentHTML("beforeend", `<style>${css}</style>`);
  }
  return `'${fam}', '${name.replace(/ SemiBold$/, "")}', system-ui, sans-serif`;
}

// ------------------------------------------------------------------ AI choice (first screen, Sources)
const AI = [["anthropic", "Claude API", "With an API key (config.json or ANTHROPIC_API_KEY). Fastest."],
  ["claude_cli", "Claude Code CLI", "Your own Claude subscription, no API key. Needs `claude` installed and logged in. Slower."]];
const MODELS = [["sonnet", "Sonnet"], ["opus", "Opus"], ["haiku", "Haiku"]];
function aiDefault() {
  try { return JSON.parse(localStorage.getItem("sdl.llm")) || { provider: "anthropic", model: "sonnet" }; }
  catch { return { provider: "anthropic", model: "sonnet" }; }
}
function aiPicker(cur) {
  return `<div class="aipick" id="ai"><div class="aiopts">${AI.map(([id, t, d]) => `
      <button class="aiopt ${cur.provider === id ? "on" : ""}" data-p="${id}"><strong>${t}</strong><em>${esc(d)}</em><span class="aistat" data-s="${id}"></span></button>`).join("")}</div>
    <label class="aimodel">Model <select id="aimodel">${MODELS.map(([id, l]) => `<option value="${id}" ${cur.model === id ? "selected" : ""}>${l}</option>`).join("")}</select>
      <span class="note">runs every agent: theme, design, content, checkers, script, visual judge</span></label></div>`;
}
function wireAi(el, cur, onChange) {
  $$(".aiopt", el).forEach((b) => b.addEventListener("click", () => {
    cur.provider = b.dataset.p;
    $$(".aiopt", el).forEach((x) => x.classList.toggle("on", x === b));
    onChange(cur);
  }));
  $("#aimodel", el).addEventListener("change", (e) => { cur.model = e.target.value; onChange(cur); });
  AI.forEach(([id]) => call("agents", "llm_status", { provider: id }).then((l) => {
    const s = $(`.aistat[data-s="${id}"]`, el);
    if (s) { s.textContent = l.ready ? "ready" : l.hint; s.className = `aistat ${l.ready ? "ok" : "bad"}`; }
  }).catch(() => {}));
}

// ------------------------------------------------------------------ 1. start: drop the paper
async function viewStart() {
  const list = await call("content", "list_sessions");
  const ai = aiDefault();
  const el = screen(`
    <div class="head center"><h1>From a paper to a finished talk.</h1>
      <p class="sub">First the AI, then drop the paper's PDF. Next you add more documents and the presenters, then choose step by step: colours, type, shapes, style and motion.</p></div>
    ${aiPicker(ai)}
    <label class="drop" id="drop"><input type="file" accept="application/pdf" id="file">
      <div><strong>Drop the PDF here</strong><span>or click to browse</span></div></label>
    ${list.length ? `<div class="recent"><h2>Earlier sessions</h2>${list.map((s) =>
      `<button data-s="${esc(s.name)}"><span>${esc(s.title || s.name)}</span><small>${esc(s.name)}</small></button>`).join("")}</div>` : ""}`);
  wireAi(el, ai, (c) => { try { localStorage.setItem("sdl.llm", JSON.stringify(c)); } catch { /* private mode */ } env(); });
  const drop = $("#drop", el);
  const take = async (file) => {
    if (!file || !/\.pdf$/i.test(file.name)) { drop.querySelector("span").textContent = "Only PDF files can be read."; return; }
    const base = file.name.replace(/\.pdf$/i, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32) || "cikk";
    let name = base, k = 2;
    while (list.some((s) => s.name === name)) name = `${base}_${k++}`;
    const log = working("Reading the paper", file.name);
    const r = await fetch(`/api/upload?dir=${encodeURIComponent(name)}&name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    const path = (await r.json()).path;
    S.session = name;
    const ing = await call("content", "ingest_paper", { session: name, pdf_path: path }, log);
    await call("content", "set_llm", { session: name, provider: ai.provider, model: ai.model });
    if (ing.paper?.scanned) {                      // slides or scans as pictures: Claude reads the pages
      log("This PDF has no text layer – Claude reads its pages…");
      await call("agents", "read_document", { session: name, index: 0 }, log);
    }
    await openSession(name, "paper");
  };
  $("#file", el).addEventListener("change", (e) => take(e.target.files[0]).catch((err) => failed("The paper could not be read", err, viewStart)));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); take(e.dataTransfer.files[0]).catch((err) => failed("The paper could not be read", err, viewStart)); });
  $$(".recent button", el).forEach((b) => b.addEventListener("click", () => openSession(b.dataset.s, "paper")));
}

// ------------------------------------------------------------------ 1b. sources: more documents, comments, team
const ROLES = [["own_results", "Our own results"], ["notes", "Notes / extra material"], ["other", "Other"]];

const CLOSINGS = [["Thank you", "Questions?"], ["Thank you for your attention", ""],
  ["Thank you for your attention", "Questions?"], ["Questions?", "Thank you for your attention"]];

async function viewSources(msg = "") {
  const srcs = S.status.sources || [];
  const team = S.status.team || {};
  const cl = team.closing || { title: "Thank you", subtitle: "Questions?" };
  const custom = !CLOSINGS.some(([t, s]) => t === cl.title && s === cl.subtitle);
  const el = screen(`
    <div class="head center"><h1>What goes into the talk?</h1>
      <p class="sub">The paper, plus any other document – e.g. your own replication results. Tell the agents how to use each one. Then the presenters.</p></div>
    <div class="specbox">
      <div class="srclist">${srcs.map((s, i) => `
        <div class="src" data-i="${i}">
          <div class="srchead"><strong>${esc(cut(s.title || s.file, 90))}</strong>
            <span class="note">${esc(s.file.split("/").pop())}${s.pages ? ` · ${s.pages} pages` : ""}</span>
            ${i === 0 ? `<span class="chip">the paper</span><button class="link newpaper" title="Start a new talk with another paper">another paper</button>` : `<select class="role">${ROLES.map(([id, l]) => `<option value="${id}" ${s.role === id ? "selected" : ""}>${l}</option>`).join("")}</select>
              <button class="link rm" title="Remove">remove</button>`}</div>
          ${s.scanned ? (s.read_by === "claude" ? `<div class="note">No text layer in this PDF – Claude read the pages (${(s.chars || 0).toLocaleString()} characters).</div>`
            : `<div class="error" style="margin:0">This PDF has no text layer (pictures of slides or a scan). <button class="link readit">Let Claude read the pages</button></div>`) : ""}
          <textarea class="comment" rows="2" placeholder="${i === 0 ? "Optional: what to focus on, what to skip…" : "How should the talk use this? e.g. “Our replication: 2–3 slides comparing our numbers with the paper's.”"}">${esc(s.comment || "")}</textarea>
        </div>`).join("")}</div>
      <label class="drop" id="drop" style="aspect-ratio:auto;padding:24px"><input type="file" accept="application/pdf" id="file" multiple>
        <div><strong>Add another document</strong><span>PDF · drop here or click</span></div></label>
      <div class="teambox">
        <label>Presenters <span class="note">(on the title slide)</span>
          <input id="team" type="text" placeholder="Name One, Name Two, Name Three" value="${esc((team.members || []).join(", "))}"></label>
        <label>Venue line <span class="note">(optional, e.g. “ACL 2026 · CSC 53432 paper presentation”)</span>
          <input id="venue" type="text" value="${esc(team.venue || "")}"></label>
        <label>Last slide <span class="note">(after the pipeline slide)</span>
          <select id="closing">${CLOSINGS.map(([t, s], k) => `<option value="${k}" ${cl.title === t && cl.subtitle === s ? "selected" : ""}>${esc(t)}${s ? " · " + esc(s) : ""}</option>`).join("")}
            <option value="custom" ${custom ? "selected" : ""}>Your own words…</option></select>
          <span class="closecustom" ${custom ? "" : "hidden"}><input id="ctitle" type="text" placeholder="Big line, e.g. Thank you for listening" value="${esc(custom ? cl.title : "")}">
            <input id="csub" type="text" placeholder="Small line (optional)" value="${esc(custom ? cl.subtitle : "")}"></span></label>
        <p class="note" style="margin:0">Who presents which slide is your decision: the presenter script only says what is worth saying on each slide.</p>
      </div>
      <div class="teambox"><strong>AI for this session</strong>${aiPicker(S.status.llm || aiDefault())}</div>
      ${msg}
      <div class="actions" style="justify-content:center"><button class="btn" id="testteam" title="Skip theme and the ★ design steps">Test the team pipeline only</button><button class="btn primary big" id="next">Continue</button></div>
    </div>`);
  const llm = { ...(S.status.llm || aiDefault()) };
  wireAi(el, llm, (c) => call("content", "set_llm", { session: S.session, provider: c.provider, model: c.model }).then(env).catch(() => {}));
  if (!S.status.llm) call("content", "set_llm", { session: S.session, provider: llm.provider, model: llm.model }).catch(() => {});
  $(".newpaper", el)?.addEventListener("click", () => { S.session = null; S.status = null; drawRail(); history.replaceState(null, "", "#"); env(); go("paper"); });
  $("#closing", el).addEventListener("change", (e) => { $(".closecustom", el).hidden = e.target.value !== "custom"; });
  $$(".src", el).forEach((row) => {
    const i = +row.dataset.i;
    $(".comment", row).addEventListener("change", (e) => call("content", "update_source", { session: S.session, index: i, comment: e.target.value }).catch(() => {}));
    $(".role", row)?.addEventListener("change", (e) => call("content", "update_source", { session: S.session, index: i, role: e.target.value }).catch(() => {}));
    $(".readit", row)?.addEventListener("click", async () => {
      const log = working("Claude is reading the pages", srcs[i].file.split("/").pop());
      try { await call("agents", "read_document", { session: S.session, index: i }, log); await refresh(); viewSources(); }
      catch (err) { failed("The pages could not be read", err, () => go("paper")); }
    });
    $(".rm", row)?.addEventListener("click", async () => { await call("content", "remove_source", { session: S.session, index: i }); await refresh(); viewSources(); });
  });
  const add = async (files) => {
    const pdfs = [...files].filter((f) => /\.pdf$/i.test(f.name));
    if (!pdfs.length) return;
    const log = working("Reading the documents", pdfs.map((f) => f.name).join(", "));
    for (const f of pdfs) {
      const r = await fetch(`/api/upload?dir=${encodeURIComponent(S.session)}&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      const res = await call("content", "add_source", { session: S.session, pdf_path: (await r.json()).path, role: "own_results", comment: "" }, log);
      const k = res.sources.length - 1;
      if (res.sources[k].scanned) {
        log(`${f.name} has no text layer – Claude reads its pages…`);
        await call("agents", "read_document", { session: S.session, index: k }, log);
      }
    }
    await refresh();
    viewSources();
  };
  const drop = $("#drop", el);
  $("#file", el).addEventListener("change", (e) => add(e.target.files).catch((err) => failed("The document could not be read", err, () => go("paper"))));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); add(e.dataTransfer.files).catch((err) => failed("The document could not be read", err, () => go("paper"))); });
  const save = async () => {
    for (const row of $$(".src", el)) {           // one after the other: every save rewrites session.json
      await call("content", "update_source", { session: S.session, index: +row.dataset.i, comment: $(".comment", row).value });
    }
    if (srcs.some((s) => s.scanned && s.read_by !== "claude")) {
      viewSources(`<div class="error">A PDF has no text layer yet: let Claude read its pages first (button above).</div>`); return false;
    }
    const members = $("#team", el).value.split(/[,;]/).map((x) => x.trim()).filter(Boolean);
    if (!members.length) { viewSources(`<div class="error">Enter at least one presenter's name.</div>`); return false; }
    const ck = $("#closing", el).value;
    const [ctitle, csub] = ck === "custom" ? [$("#ctitle", el).value.trim(), $("#csub", el).value.trim()] : CLOSINGS[+ck];
    if (!ctitle) { viewSources(`<div class="error">Write the last slide's big line (or pick one of the options).</div>`); return false; }
    await call("content", "set_team", { session: S.session, team: members, venue: $("#venue", el).value, closing_title: ctitle, closing_subtitle: csub });
    await refresh();
    return true;
  };
  $("#next", el).addEventListener("click", async () => {
    if (await save()) go("brief");                 // always in order: theme, kick-off, the ★ steps, content, deck
  });
  $("#testteam", el).addEventListener("click", async () => {   // straight to the content pipeline
    if (!(await save())) return;
    if (!S.status.kickoff) {
      await call("content", "kickoff", { session: S.session, slides: 13, density: "medium", max_chars: 290, minutes: 20, direction: "" });
      await refresh();
    }
    go("spec");
  });
}

// ------------------------------------------------------------------ 2. theme
async function viewBrief(regen = false) {
  let brief = await call("agents", "get_brief", { session: S.session });
  if (regen || !brief || !Object.keys(brief).length) {
    const log = working("Reading the theme", "Mood, keywords, what to communicate, visual metaphors.");
    await call("agents", "write_brief", { session: S.session }, log);
    await refresh();
    brief = await call("agents", "get_brief", { session: S.session });
  }
  S.brief = brief;
  const needs = { math_heavy: "math heavy", architecture: "architecture", empirical_results: "results", comparison: "comparison",
    process: "process", qualitative_examples: "examples" };
  const el = screen(`
    <div class="head center"><h1>This is what the paper is about.</h1><p class="sub">The design starts from here. If it misses, regenerate it.</p></div>
    <article class="brief">
      <p class="title">${esc(brief.title)}</p><p class="line">${esc(brief.one_line)}</p>
      <div class="chips">${(brief.mood || []).map((m) => `<span class="chip">${esc(m)}</span>`).join("")}
        ${(brief.communication_needs || []).map((m) => `<span class="chip need">${esc(needs[m] || m)}</span>`).join("")}</div>
      <h3>Visual metaphors</h3><ul>${(brief.metaphors || []).map((m) => `<li>${esc(m.idea)}</li>`).join("")}</ul>
    </article>
    <div class="actions" style="margin-top:28px"><button class="btn" id="regen">Regenerate</button><button class="btn primary big" id="next">Continue</button></div>`);
  $("#regen", el).addEventListener("click", () => viewBrief(true).catch((e) => failed("The theme was not written", e, () => viewBrief(true))));
  $("#next", el).addEventListener("click", () => go("kickoff"));
}

// ------------------------------------------------------------------ 3. kick-off, one question at a time
const POOL = ["Lorem ipsum dolor sit amet, consectetur adipiscing", "Sed do eiusmod tempor incididunt ut labore",
  "Ut enim ad minim veniam, quis nostrud exercitation", "Duis aute irure dolor in reprehenderit in voluptate",
  "Excepteur sint occaecat cupidatat non proident", "Sunt in culpa qui officia deserunt mollit anim",
  "Nemo enim ipsam voluptatem quia voluptas sit", "Neque porro quisquam est, qui dolorem ipsum",
  "Quis autem vel eum iure reprehenderit qui in ea", "At vero eos et accusamus et iusto odio dignissimos"];

function sampleSlide(chars) {
  const n = Math.max(1, Math.min(POOL.length, Math.round(chars / 52)));
  const size = Math.max(10, Math.min(22, 22 * Math.sqrt(170 / chars)));
  return `<b style="font-size:${size * 1.45}px">Lorem ipsum dolor sit amet</b>
    <ul style="font-size:${size}px">${POOL.slice(0, n).map((t) => `<li>${t}</li>`).join("")}</ul>`;
}

async function viewKickoff() {
  const k = S.status.kickoff || {};
  const ans = { slides: k.logical_slides || 13, chars: k.max_chars || 290, direction: k.direction || "", minutes: k.talk_minutes || 20 };
  const q1 = () => {
    const el = screen(`<div class="head center"><h1>How many slides?</h1><p class="sub">Logical slides, without section dividers – the title, credits, pipeline and thank-you slides included. For 20 minutes, 13–16 works well.</p></div>
      <div class="question"><div class="bignum" id="n">${ans.slides}<small>slides</small></div>
        <input type="range" min="8" max="24" value="${ans.slides}" id="r" aria-label="Number of slides">
        <div class="scale"><span>8</span><span>24</span></div>
        <label class="minutes">Talk length <input type="number" id="min" min="5" max="60" value="${ans.minutes}"> minutes <span class="note">(times the presenter script)</span></label>
        <button class="btn primary big" id="ok">Continue</button></div>`);
    const r = $("#r", el);
    r.addEventListener("input", () => { ans.slides = +r.value; $("#n", el).innerHTML = `${ans.slides}<small>slides</small>`; });
    $("#ok", el).addEventListener("click", () => { ans.minutes = Math.max(5, Math.min(60, +$("#min", el).value || 20)); q2(); });
    r.focus();
  };
  const q2 = () => {
    const label = (c) => (c < 240 ? "tight" : c < 340 ? "medium" : "dense");
    const el = screen(`<div class="head center"><h1>How much text per slide?</h1><p class="sub">The content team writes to this limit. More text means smaller type.</p></div>
      <div class="question"><div class="sample" id="sm">${sampleSlide(ans.chars)}</div>
        <input type="range" min="160" max="480" step="10" value="${ans.chars}" id="r" aria-label="Text density">
        <div class="scale"><span>less</span><span>more</span></div>
        <div class="dens" id="d"></div>
        <button class="btn primary big" id="ok">Continue</button></div>`);
    const r = $("#r", el);
    const upd = () => { ans.chars = +r.value; $("#sm", el).innerHTML = sampleSlide(ans.chars); $("#d", el).textContent = `${label(ans.chars)} · up to ${ans.chars} characters on a content slide`; };
    r.addEventListener("input", upd); upd();
    $("#ok", el).addEventListener("click", q3);
  };
  const q3 = () => {
    const el = screen(`<div class="head center"><h1>Which direction?</h1><p class="sub">Just a direction. You pick colours, type and shapes one by one next.</p></div>
      <div class="dirs">${DIRS.map(([id, t, d]) => `<button class="dir ${id}" data-d="${id}"><strong>${t}</strong><em>${d}</em></button>`).join("")}</div>`);
    $$(".dir", el).forEach((b) => b.addEventListener("click", async () => {
      ans.direction = b.dataset.d;
      const density = ans.chars < 240 ? "tight" : ans.chars < 340 ? "medium" : "dense";
      await call("content", "kickoff", { session: S.session, slides: ans.slides, density, max_chars: ans.chars, direction: ans.direction, minutes: ans.minutes });
      await refresh();
      go("palette");
    }));
  };
  q1();
}

// ------------------------------------------------------------------ ★ stages
async function viewStage(stage, regen = false) {
  const [title, sub] = COPY[stage];
  let rend = (S.status.renders || {})[stage];
  const opts = await call("design", "get_options", { session: S.session, stage });
  const has = opts && opts.options;
  if (regen || !has || !rend) {
    const long = stage === "motion" ? "Four full decks and their videos are being made. This takes a few minutes." :
      stage === "layout" ? "PowerPoint renders the four main styles." : "The design agent writes four options.";
    const log = working(title, long);
    if (regen || !has) await call("agents", "propose_options", { session: S.session, stage }, log);
    log("Rendering previews…");
    try {
      rend = await call("design", "render_options", { session: S.session, stage }, log);
    } catch (e) {                                   // options written by an older version: ask for new ones
      if (!/do not validate/.test(e.message) || regen || !has) throw e;
      log("The earlier options are outdated – asking for new ones…");
      await call("agents", "propose_options", { session: S.session, stage }, log);
      rend = await call("design", "render_options", { session: S.session, stage }, log);
    }
    await refresh();
    rend = (S.status.renders || {})[stage] || rend;
  }
  const chosen = (S.status.choices || {})[stage]?.key;
  const el = screen(`
    <div class="head"><div><h1>${esc(title)}</h1><p class="sub">${esc(sub)}</p></div>
      <div class="actions"><button class="btn" id="regen">Regenerate</button></div></div>
    <div class="grid" id="grid"></div>`);
  const grid = $("#grid", el);
  rend.options.forEach((o) => grid.insertAdjacentHTML("beforeend", card(stage, o, o.key === chosen)));
  wireCards(grid, stage);
  $("#regen", el).addEventListener("click", () => viewStage(stage, true).catch((e) => failed("Not finished", e, () => viewStage(stage, true))));
}

function card(stage, o, chosen) {
  const art = { palette: artPalette, type: artType, graphics: artGraphics, visual: artVisual, layout: artSlides, motion: artMotion }[stage](o);
  return `<div class="card pickable ${chosen ? "chosen" : ""}" data-key="${esc(o.key)}" role="button" tabindex="0" aria-label="${esc(o.label)}">
    <div class="art">${art}</div>
    <div class="meta"><div class="row"><strong>${esc(o.label)}</strong>${chosen ? `<span class="picked">chosen</span>` : ""}</div></div></div>`;
}

// One way to choose everywhere: the card lights up under the pointer, a click anywhere on it chooses.
// Controls inside a card (flip arrows, video, tabs) only do their own thing.
function wireCards(grid, stage) {
  const pick = async (key) => {
    const log = working("Saving your choice");
    await call("design", "choose_option", { session: S.session, stage, key }, log);
    await refresh();
    go(NEXT[stage]);
  };
  $$(".card", grid).forEach((c) => {
    const choose = () => pick(c.dataset.key).catch((e) => failed("That did not work", e, () => go(stage)));
    c.addEventListener("click", (e) => { if (!e.target.closest(".nav, .tabs, video, .lb")) choose(); });
    c.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } });
    wireFlip(c);
  });
}

// ---- palette board: the identity sheet of a colour system
function artPalette(o) {
  const b = o.resolved.brand;
  const roles = [["bg", "background"], ["surface", "surface"], ["text", "text"], ["muted", "muted"], ["ours", "our method"],
    ["baseline", "baseline"], ["highlight", "highlight"], ["accent2", "second accent"]];
  const bars = [0.52, 0.6, 0.66, 0.86];
  return `<div class="pal" style="background:${hex(b.bg)};color:${hex(b.text)}">
      <div class="face"><div class="aa">Aa</div>
        <svg viewBox="0 0 200 70" width="100%" height="34%" aria-hidden="true">${bars.map((v, i) =>
          `<rect x="${i * 50 + 6}" y="${70 - v * 70}" width="34" height="${v * 70}" rx="2" fill="${hex(i === 3 ? b.ours : b.baseline)}"/>`).join("")}</svg>
        <div class="t"><span style="color:${hex(b.muted)}">muted note · source</span><br>
          Our method <b style="color:${hex(b.ours)}">+12.4</b> – <span style="border-bottom:3px solid ${hex(b.highlight)}">the key claim</span></div></div>
      <div class="sw"><div style="background:${hex(b.ours)};color:${hex(b.bg)}">our method</div>
        <div style="background:${hex(b.surface)};color:${hex(b.text)}">surface</div>
        <div style="background:${hex(b.highlight)};color:${hex(b.bg)}">highlight</div>
        <div style="background:${hex(b.accent2)};color:${hex(b.bg)}">second accent</div></div></div>
    <div class="swatches" style="position:absolute;left:0;right:0;bottom:0">${roles.map(([k, n]) =>
      `<span style="background:${hex(b[k])}" data-n="${n} · ${hex(b[k])}"></span>`).join("")}</div>`;
}

// ---- type board: specimen on the chosen palette
function artType(o) {
  const b = o.resolved.brand, f = o.resolved.fonts || {};
  const H = fontFamily(b.heading_font, f), B = fontFamily(b.body_font, f), M = fontFamily(b.mono_font, f);
  const hw = /SemiBold$/.test(b.heading_font) || b.heading_bold ? 700 : 400;
  return `<div style="position:absolute;inset:0;background:${hex(b.bg)};color:${hex(b.text)};padding:6% 7%;display:flex;flex-direction:column;justify-content:space-between">
    <div style="display:flex;justify-content:space-between;align-items:flex-start">
      <div style="font-family:${H};font-weight:${hw};font-size:clamp(48px,6vw,92px);line-height:.9;letter-spacing:-.02em">Aa</div>
      <div style="font-family:${M};font-size:12px;color:${hex(b.muted)};text-align:right">${esc(b.heading_font)}<br>${esc(b.body_font)}<br>${esc(b.mono_font)}</div></div>
    <div><div style="font-family:${H};font-weight:${hw};font-size:clamp(18px,2.1vw,28px);line-height:1.12;margin-bottom:10px">${esc(cut(S.brief?.title || "The paper title goes here", 70))}</div>
      <div style="font-family:${B};font-size:clamp(12px,1.1vw,15px);line-height:1.5;color:${hex(b.text)};max-width:46ch">${esc(cut(S.brief?.one_line || "One sentence on what the paper shows and why it matters.", 150))}</div>
      <div style="font-family:${M};font-size:clamp(12px,1.1vw,15px);margin-top:10px;color:${hex(b.ours)}">0123456789 · 79.8% · ±0.4</div></div></div>`;
}

// ---- graphics board: chart, table, figure frame
function artGraphics(o) {
  const b = o.resolved.brand, g = b.graphics || {}, c = g.chart || {}, t = g.table || {}, f = g.figure || {};
  const vals = [39.2, 63.6, 71.4, 79.8], names = ["Base", "Prior", "Strong", "Ours"];
  const other = c.others === "muted" ? b.muted : b.baseline;
  const gapF = Math.min(0.75, Math.max(0.15, (c.gap ?? 60) / 200));
  const W = 260, Hh = 150, bw = (W - 20) / vals.length * (1 - gapF);
  const font = fontFamily(b.body_font, o.resolved.fonts);
  let svg = "";
  if (c.gridlines) for (let y = 0; y <= 4; y++) svg += `<line x1="10" x2="${W - 10}" y1="${20 + y * 30}" y2="${20 + y * 30}" stroke="${hex(b.surface)}" stroke-width="1"/>`;
  vals.forEach((v, i) => {
    const h = v / 90 * 120, x = 10 + i * (W - 20) / vals.length + ((W - 20) / vals.length - bw) / 2, y = 140 - h;
    const col = hex(i === 3 ? b.ours : other);
    svg += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" fill="${col}"/>`;
    if (c.labels !== "none") {
      const inside = c.labels === "inside";
      svg += `<text x="${x + bw / 2}" y="${inside ? y + 13 : y - 4}" text-anchor="middle" font-size="10" font-weight="${i === 3 ? 700 : 400}"
        fill="${hex(inside ? b.bg : i === 3 ? b.ours : b.text)}">${v}</text>`;
    }
    svg += `<text x="${x + bw / 2}" y="${Hh - 0}" text-anchor="middle" font-size="9" fill="${hex(b.muted)}">${names[i]}</text>`;
  });
  if (c.axis_line !== false) svg += `<line x1="10" x2="${W - 10}" y1="140" y2="140" stroke="${hex(b.baseline)}" stroke-width="1"/>`;
  const rows = [["Method", "Score", "Cost"], ["Base", "39.2", "1.0×"], ["Strong", "71.4", "3.2×"], ["Ours", "79.8", "1.1×"]];
  const table = rows.map((r, i) => {
    const hl = i === 3;
    const bg = i === 0 && t.header === "fill" ? hex(b.surface) : hl && (t.highlight || "tint") === "tint" ? hex(b.ours) + "22" :
      t.stripes && i === 2 ? hex(b.surface) + "aa" : "transparent";
    return `<div style="display:grid;grid-template-columns:1.2fr 1fr 1fr;padding:3px 6px;background:${bg};
      ${i === 0 && (t.header || "line") === "line" ? `border-bottom:1px solid ${hex(b.baseline)};` : ""}
      ${hl && t.highlight === "bar" ? `box-shadow:inset 3px 0 0 ${hex(b.ours)};` : ""}
      color:${hex(i === 0 ? b.muted : hl ? b.ours : b.text)};font-weight:${hl ? 700 : 400}">${r.map((x) => `<span>${x}</span>`).join("")}</div>`;
  }).join("");
  const frame = f.frame === "card" ? `background:${hex(b.surface)};padding:8px;` : f.frame === "border" ? `border:1px solid ${hex(b.baseline)};` : "";
  const shadow = f.shadow ? "box-shadow:0 10px 22px -12px rgba(0,0,0,.55);" : "";
  return `<div style="position:absolute;inset:0;background:${hex(b.bg)};padding:5% 5.5%;display:grid;grid-template-columns:1.25fr 1fr;gap:5%;font-family:${font}">
    <svg viewBox="0 0 ${W} ${Hh + 4}" width="100%" height="100%" style="font-family:${font}" aria-hidden="true">${svg}</svg>
    <div style="display:flex;flex-direction:column;gap:10%;justify-content:center">
      <div style="font-size:11px">${table}</div>
      <div style="${frame}${shadow}border-radius:${(f.radius || 0) * 60}px">
        <div style="aspect-ratio:16/8;border-radius:${(f.radius || 0) * 48}px;background:#fff;display:flex;align-items:flex-end;gap:4%;padding:8% 10%">
          ${[30, 55, 42, 75, 62].map((h) => `<div style="flex:1;height:${h}%;background:#9AA7BF"></div>`).join("")}</div></div></div></div>`;
}

// ---- visual board: shape language
function artVisual(o) {
  const b = o.resolved.brand, v = o.resolved.visual_language;
  const r = v.corner === "sharp" ? 0 : v.corner === "pill" ? 22 : Math.max(4, v.radius * 60);
  const fillOf = (hot) => v.fill_style === "solid" ? hex(hot ? b.ours : b.surface) : v.fill_style === "outline" ? "none" : hex(hot ? b.ours : b.baseline) + (hot ? "33" : "22");
  const strokeOf = (hot) => v.fill_style === "solid" ? "none" : hex(hot ? b.ours : b.baseline);
  const textOf = (hot) => v.fill_style === "solid" && hot ? hex(b.bg) : hex(b.text);
  const dash = v.arrow === "dashed" ? `stroke-dasharray="6 5"` : "";
  const head = v.arrow_head === "none" ? "" : `marker-end="url(#h${o.key})"`;
  const font = fontFamily(b.body_font, o.resolved.fonts);
  const boxes = [kw(0, "Input"), kw(1, "Our method"), kw(2, "Result")];
  let s = `<defs><marker id="h${o.key}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      ${v.arrow_head === "stealth" ? `<path d="M0 0 L10 5 L0 10 L3 5 Z" fill="${hex(b.muted)}"/>` : `<path d="M0 0 L10 5 L0 10 Z" fill="${hex(b.muted)}"/>`}</marker>
    <filter id="sh${o.key}" x="-20%" y="-20%" width="140%" height="160%"><feDropShadow dx="0" dy="4" stdDeviation="5" flood-opacity=".28"/></filter>
    <filter id="gl${o.key}" x="-40%" y="-40%" width="180%" height="180%"><feDropShadow dx="0" dy="0" stdDeviation="7" flood-color="${hex(b.ours)}" flood-opacity=".75"/></filter></defs>`;
  boxes.forEach((t, i) => {
    const hot = i === 1, x = 14 + i * 104, y = 28;
    const filt = v.depth === "shadow" ? `filter="url(#sh${o.key})"` : "";
    const glow = hot && v.highlight === "glow" ? `filter="url(#gl${o.key})"` : filt;
    s += `<rect x="${x}" y="${y}" width="84" height="44" rx="${r}" fill="${fillOf(hot)}" stroke="${strokeOf(hot)}" stroke-width="${v.stroke_w}" ${glow}/>
      <text x="${x + 42}" y="${y + 26}" text-anchor="middle" font-size="10" fill="${textOf(hot)}">${t}</text>`;
    if (hot && v.highlight === "underline") s += `<rect x="${x}" y="${y + 50}" width="84" height="3" fill="${hex(b.highlight)}"/>`;
    if (hot && v.highlight === "box") s += `<rect x="${x - 5}" y="${y - 5}" width="94" height="54" rx="${r + 4}" fill="none" stroke="${hex(b.highlight)}" stroke-width="1.5"/>`;
    if (hot && v.highlight === "marker") s += `<rect x="${x + 6}" y="${y + 30}" width="72" height="8" fill="${hex(b.highlight)}" opacity=".45"/>`;
    if (i < 2 && v.arrow !== "none") s += `<line x1="${x + 88}" y1="${y + 22}" x2="${x + 100}" y2="${y + 22}" stroke="${hex(b.muted)}" stroke-width="${v.stroke_w}" ${dash} ${head}/>`;
  });
  s += `<rect x="14" y="100" width="190" height="46" rx="${r}" fill="${fillOf(false)}" stroke="${strokeOf(false)}" stroke-width="${v.stroke_w}" ${v.depth === "shadow" ? `filter="url(#sh${o.key})"` : ""}/>
    <text x="26" y="119" font-size="10" fill="${hex(b.text)}">• ${esc(kw(3, "First claim"))}</text><text x="26" y="135" font-size="10" fill="${hex(b.text)}">• ${esc(kw(4, "Second claim"))}</text>
    <circle cx="250" cy="123" r="22" fill="${fillOf(true)}" stroke="${strokeOf(true)}" stroke-width="${v.stroke_w}"/>
    <text x="250" y="127" text-anchor="middle" font-size="11" font-weight="700" fill="${textOf(true)}">${esc(String(S.brief?.title || "A").trim()[0] || "A")}</text>`;
  return `<div style="position:absolute;inset:0;background:${hex(b.bg)};display:grid;place-items:center;padding:4%">
    <svg viewBox="0 0 330 160" width="100%" height="100%" style="font-family:${font}" aria-hidden="true">${s}</svg></div>`;
}

// ---- layout / motion: flip through real PowerPoint renders
function artSlides(o) {
  const n = (o.pngs || []).length;
  return `<div class="flip" data-pngs='${esc(JSON.stringify(o.pngs || []))}' data-i="0">
    <img src="${url(o.pngs[0])}" alt="${esc(o.label)} – slide 1">
    <div class="nav"><button class="prev" aria-label="Previous slide">‹</button><span>1 / ${n}</span><button class="next" aria-label="Next slide">›</button></div></div>`;
}

function artMotion(o) {
  const n = (o.pngs || []).length;
  const media = o.video ? `<video src="${url(o.video)}" muted loop playsinline preload="metadata" controls></video>` :
    o.gif ? `<img src="${url(o.gif)}" alt="Morph simulation">` : `<img src="${url(o.pngs[0])}" alt="">`;
  return `<div class="flip motion" data-pngs='${esc(JSON.stringify(o.pngs || []))}' data-i="0" data-media='${esc(media)}'>
    <div class="tabs"><button class="on" data-t="play">${o.video ? "Video" : "Simulation"}</button><button data-t="slides">Slides</button></div>
    <div class="body">${media}</div>
    <div class="nav" hidden><button class="prev" aria-label="Previous slide">‹</button><span>1 / ${n}</span><button class="next" aria-label="Next slide">›</button></div></div>`;
}

function wireFlip(root) {
  const f = $(".flip", root);
  if (!f) return;
  const pngs = JSON.parse(f.dataset.pngs || "[]");
  const show = (i) => {
    i = (i + pngs.length) % pngs.length;
    f.dataset.i = i;
    const img = f.classList.contains("motion") ? $(".body", f) : null;
    if (img) img.innerHTML = `<img src="${url(pngs[i])}" alt="">`;
    else $("img", f).src = url(pngs[i]);
    $(".nav span", f).textContent = `${i + 1} / ${pngs.length}`;
  };
  $(".prev", f)?.addEventListener("click", (e) => { e.stopPropagation(); show(+f.dataset.i - 1); });
  $(".next", f)?.addEventListener("click", (e) => { e.stopPropagation(); show(+f.dataset.i + 1); });
  $$(".tabs button", f).forEach((t) => t.addEventListener("click", (e) => {
    e.stopPropagation();
    $$(".tabs button", f).forEach((x) => x.classList.toggle("on", x === t));
    const nav = $(".nav", f);
    if (t.dataset.t === "slides") { nav.hidden = false; show(+f.dataset.i); }
    else { nav.hidden = true; $(".body", f).innerHTML = f.dataset.media; playVisible(); }
  }));
  $("img", f)?.addEventListener("dblclick", (e) => lightbox(e.target.src));
}

function playVisible() {
  $$("video").forEach((v) => { v.play().catch(() => {}); });
}

// ------------------------------------------------------------------ content (the team's pipeline, or a deck.json)
async function viewSpec(msg = null) {
  if (!msg && S.status.team_run?.state === "done" && S.status.team_run.run_id !== S.status.spec?.team_run) {
    msg = reimportBox(S.status.team_run);            // the last run never reached the session
  }
  if (S.status.team_run && S.status.team_run.state !== "done") {   // where is the run now?
    try {
      const tr = await call("team", "team_status", { session: S.session });
      if (tr.state === "waiting_for_human") return viewTeamReview(tr);
      S.status.team_run.state = tr.state;
    } catch (e) { msg = msg || `<div class="error">${esc(e.message)}</div>`; }
  }
  if (!msg && S.status.team_run?.state === "running") {   // stopped by an error in the middle
    msg = `<div class="error" style="white-space:normal">The last run stopped in the middle (an error). The finished steps are saved.
      <button class="btn" id="continue" style="margin-top:8px">Continue the interrupted run</button></div>`;
  }
  const inbox = await (await fetch("/api/inbox")).json();
  const info = await call("team", "team_info", { session: S.session }).catch((e) => ({ ready: false, error: e.message }));
  const sp = S.status.spec;
  let slides = [];
  if (sp) slides = (await call("content", "get_spec", { session: S.session })).slides || [];
  const ko = S.status.kickoff || {};
  const lim = ko.char_limits || {};
  const ndoc = (S.status.sources || []).length;
  const roles = info.ready ? Object.entries(info.roles).map(([r, m]) => `${r}: ${m}`).join(" · ") : "";
  const el = screen(`
    <div class="head center"><h1>Now the content.</h1>
      <p class="sub">${ko.logical_slides || "?"} slides (plus section dividers), up to ${lim.content || "?"} characters on a content slide, ${ko.talk_minutes || 20} minutes, from ${ndoc} document${ndoc === 1 ? "" : "s"}. The team's agents plan the talk, write every slide, check it, and write what is worth saying on each slide.</p></div>
    <div class="specbox">
      ${sp ? `<div class="specsum"><strong>${slides.filter((x) => x.type !== "section").length} slides + ${slides.filter((x) => x.type === "section").length} section dividers</strong> <span class="note">${sp.team_run ? "the team's pipeline" : sp.placeholder ? "written by Claude" : esc(sp.source)}</span>
        ${sp.script ? `<a class="note" href="${url(sp.script)}" target="_blank" style="margin-left:10px">presenter script ↗</a>` : ""}
        <ol class="speclines">${(() => { let k = 0; return slides.map((x) => {
          const c = x.content || {}, t = esc(String(c.title || c.text || c.message || c.number || x.type).replace(/==|\*\*|\$/g, ""));
          return x.type === "section" ? `<li class="sec">${t}</li>` : `<li><span>${++k}</span>${t}${x.seconds ? ` <em class="note">· ${x.seconds} s</em>` : ""}</li>`; }).join(""); })()}</ol></div>` : ""}
      <div class="actions" style="justify-content:center">
        ${info.ready ? `<button class="btn ${sp ? "" : "primary big"}" id="team">${sp ? "Run the team pipeline again" : "Run the team pipeline"}</button>` : ""}
        <button class="btn" id="draft">${sp ? "Quick redraft (one Claude call)" : "Quick draft (one Claude call)"}</button>
        ${sp?.preview ? `<a class="btn" href="${url(sp.preview)}" download>Preview pptx</a>` : ""}
        ${sp && done("layout") ? `<button class="btn primary big" id="next">Continue</button>` : ""}</div>
      ${sp && !done("layout") ? `<p class="note" style="text-align:center">Content test: the design steps are not chosen yet, so the preview uses the default look. For the real deck go through Theme → Main style, then Deck look.</p>` : ""}
      <p class="note" style="text-align:center">${info.ready ? `Models: ${esc(roles)}${info.fake ? " – <b>fake</b> roles write placeholder text (set config/models.local.yaml in GeoRAProject)" : ""}` : `Team pipeline not available: ${esc(info.error || "")}`}</p>
      ${msg || ""}
      <label class="drop" id="drop" style="aspect-ratio:auto;padding:28px;margin-top:8px"><input type="file" accept=".json,application/json" id="file">
        <div><strong>${sp ? "Load another deck.json" : "Or drop the team's deck.json here"}</strong><span>or pick one that arrived</span></div></label>
      ${inbox.specs.length ? `<div class="speclist">${inbox.specs.map((p) => `<button data-p="${esc(p)}"><span>${esc(p.split(/[\\/]/).slice(-2).join("/"))}</span><span class="note">load</span></button>`).join("")}</div>` : ""}
    </div>`);
  const load = async (path) => {
    const log = working("Checking the content");
    const r = await call("content", "import_spec", { session: S.session, spec_path: path }, log);
    await refresh();
    if (r.imported) return go("motion");
    const li = r.errors.map((e) => `<li class="e">${esc(e)}</li>`).concat(r.warnings.map((w) => `<li class="w">${esc(w)}</li>`)).join("");
    viewSpec(`<div class="error" style="white-space:normal"><strong>Not accepted – fix these first:</strong><ul class="issues">${li}</ul></div>`);
  };
  $$(".speclist button", el).forEach((b) => b.addEventListener("click", () => load(b.dataset.p).catch((e) => failed("That did not work", e, () => go("spec")))));
  $("#file", el).addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const r = await fetch(`/api/upload?dir=${encodeURIComponent(S.session)}&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
    load((await r.json()).path).catch((err) => failed("That did not work", err, () => go("spec")));
  });
  $("#next", el)?.addEventListener("click", () => go("motion"));
  $("#continue", el)?.addEventListener("click", async () => {
    const log = working("Continuing the run", "From the last saved step: nothing that is finished is redone.");
    try { teamNext(await call("team", "team_continue", { session: S.session }, log)); }
    catch (err) { failed("The run stopped again", err, () => go("spec")); }
  });
  $("#reimport", el)?.addEventListener("click", async () => {
    const log = working("Importing the slides", "No agents run: the finished slides of the last run go to the design side again.");
    try { await call("team", "team_reimport", { session: S.session }, log); await refresh(); viewSpec(`<div class="okbox">The slides and the presenter script are in.</div>`); }
    catch (err) { failed("The import did not work", err, () => go("spec")); }
  });
  $("#team", el)?.addEventListener("click", async () => {
    const log = working("The team's agents are planning the talk", `Reading ${ndoc} document${ndoc === 1 ? "" : "s"}, planning ${ko.logical_slides || ""} slides, checking the plan. You review the outline next.`);
    try { teamNext(await call("team", "team_start", { session: S.session }, log)); }
    catch (err) { failed("The team pipeline stopped", err, () => go("spec")); }
  });
  $("#draft", el).addEventListener("click", async () => {
    const log = working("Writing the content", `${ko.logical_slides || ""} slides from the documents, with the paper's figures, within the character limits. This takes a minute or two.`);
    try { await call("agents", "draft_placeholder_spec", { session: S.session }, log); await refresh(); viewSpec(); }
    catch (err) { failed("The content could not be written", err, () => go("spec")); }
  });
}

async function teamNext(tr) {
  await refresh();
  if (tr.state === "waiting_for_human") return viewTeamReview(tr);
  if (tr.state === "done" && S.status.spec?.team_run === tr.run_id)
    return viewSpec(`<div class="okbox">The slides and the presenter script are in. Check the list, then continue to the deck look.</div>`);
  if (tr.state === "done") return viewSpec(reimportBox(tr));
  return viewSpec(tr.state === "aborted" ? `<div class="error">The run was stopped.</div>` :
    `<div class="error" style="white-space:normal">The run ended without slides (${esc(tr.state)}).${(tr.open_feedback || []).length ? `<ul class="issues">${tr.open_feedback.map((f) => `<li class="e">${esc(f.slide_id)}: ${esc(f.message)}</li>`).join("")}</ul>` : ""}</div>`);
}

function reimportBox(tr) {
  return `<div class="error" style="white-space:normal">The agents finished, but the slides did not reach this session (the list below is older content).
    ${(tr.open_feedback || []).length ? `<ul class="issues">${tr.open_feedback.map((f) => `<li class="e">${esc(f.slide_id)}: ${esc(f.message)}</li>`).join("")}</ul>` : ""}
    <button class="btn" id="reimport" style="margin-top:8px">Import the last run's slides</button></div>`;
}

function issueList(items, cls) {
  return items.length ? `<ul class="issues">${items.map((f) => `<li class="${cls}">${esc(f.slide_id)}: ${esc(f.message)}</li>`).join("")}</ul>` : "";
}

function viewTeamReview(tr) {
  const p = tr.pending, c = p.context || {};
  const outline = p.kind === "outline_review";
  const secs = Object.fromEntries((c.sections || []).map((x) => [x.id, x.title]));
  let prevSec = null;
  const rows = (c.slides || []).map((x) => {
    const head = outline && x.section_id !== prevSec ? `<li class="sec">${esc(secs[x.section_id] || x.section_id)}</li>` : "";
    prevSec = x.section_id;
    const body = outline ? `${esc(x.goal)}${(x.figure_ids || []).length ? ` <em class="note">· ${esc(x.figure_ids.join(", "))}</em>` : ""}`
      : `<b>${esc(x.title)}</b>${(x.bullets || []).length ? `<small>${(x.bullets || []).slice(0, 4).map(esc).join(" · ")}</small>` : ""}`;
    return `${head}<li><span>${esc(x.id)}</span><i class="note">${esc(x.type)}</i> ${body}</li>`;
  }).join("");
  const el = screen(`
    <div class="head center"><h1>${outline ? "Check the plan of the talk." : "Check the slides' content."}</h1>
      <p class="sub">${outline ? "The outline agent's plan, already checked by the rules and an LLM checker. Approve it, or say what to change." : "Every slide was checked against the limits, the chosen fonts, the documents and by an LLM checker. Approve to get the presenter script and the design, or name slide ids to change (e.g. “s04: use the replication numbers”)."}</p></div>
    <div class="specbox">
      <div class="specsum"><ol class="speclines trlist ${outline ? "" : "deck"}">${rows}</ol></div>
      ${issueList(c.unresolved_feedback || [], "e")}${issueList(c.warnings || [], "w")}
      <textarea id="txt" rows="3" placeholder="${outline ? "What should change? (optional when you approve: extra notes for the writers)" : "What should change? Mention slide ids."}"></textarea>
      <div class="actions" style="justify-content:center">
        ${p.options.map((o) => `<button class="btn ${o.id === "approve" ? "primary big" : ""}" data-c="${o.id}">${esc(o.label)}</button>`).join("")}</div>
      <p class="note" style="text-align:center"><button class="link" id="fresh">Start a new run instead (this one is dropped)</button></p>
    </div>`);
  $("#fresh", el).addEventListener("click", async () => {
    const log = working("The team's agents are planning the talk", "A new run from the documents and the kick-off.");
    try { teamNext(await call("team", "team_start", { session: S.session }, log)); }
    catch (err) { failed("The team pipeline stopped", err, () => go("spec")); }
  });
  $$("[data-c]", el).forEach((b) => b.addEventListener("click", async () => {
    const choice = b.dataset.c, text = $("#txt", el).value.trim();
    if (choice === "revise" && !text) { $("#txt", el).focus(); $("#txt", el).placeholder = "Write what should change first."; return; }
    const ids = text.match(/\b(s\d{2}|title|credits|workflow|thanks)\b/g) || [];
    const log = working(choice === "approve" ? (outline ? "Writing the slides" : "Writing the presenter script and handing over to design") : choice === "abort" ? "Stopping" : "Revising",
      choice === "approve" && outline ? "Each slide is written to its type and limit, then checked by the rules, with your fonts, against the documents and by an LLM checker." : "");
    try { teamNext(await call("team", "team_resume", { session: S.session, choice, text, slide_ids: ids.length ? ids : null }, log)); }
    catch (err) { failed("The team pipeline stopped", err, () => go("spec")); }
  }));
}

// ------------------------------------------------------------------ done
// ------------------------------------------------------------------ polish: change requests until it is right
const REQ = { key: () => `sdl.refine.${S.session}.${S.status?.refine?.round || 0}` };
function loadReq() { try { return JSON.parse(localStorage.getItem(REQ.key()) || "{}"); } catch { return {}; } }
function saveReq(r) { try { localStorage.setItem(REQ.key(), JSON.stringify(r)); } catch { /* private mode */ } }

async function viewRefine() {
  if (!done("motion")) return go(firstOpen());
  if (!S.status.deck) {                           // first time here: the deck of the chosen look, with its video
    const log = working("Building your deck", "The slides and their video (PowerPoint needs a few minutes for the video).");
    await call("deck", "build_deck", { session: S.session, video: true }, log);
    if (!(S.status.refine?.round)) await polish(log);   // the automatic layout check only before your first request
    await refresh();
  }
  const d = S.status.deck, rf = S.status.refine || { round: 0, history: [] };
  const pngs = d.pngs || [], ids = d.ids || [];
  const n = pngs.length;
  const motionVideo = rf.round ? null : (S.status.renders?.motion?.options || []).find((o) => o.key === S.status.choices?.motion?.key)?.video;
  const vid = d.video || motionVideo;
  const media = vid ? `<video src="${url(vid)}" muted loop playsinline controls autoplay></video>` :
    d.gif ? `<img src="${url(d.gif)}" alt="Morph simulation">` : `<div class="note">No video (PowerPoint is needed for it); flip through the slides below.</div>`;
  const reqs = loadReq();                          // { "<index>": "text" }, kept while you write
  const last = rf.history[rf.history.length - 1];
  const el = screen(`
    <div class="head"><div><h1>Final touches</h1>
      <p class="sub">Version ${rf.round + 1} · ${n} slides. Ask for changes until you are happy – only what you ask for changes.</p></div>
      <div class="actions"><button class="btn primary big" id="happy">I'm happy → Final export</button></div></div>
    ${last ? `<div class="okbox rround"><b>Version ${rf.round + 1} is in.</b>
      ${last.changed ? ` Changed: <b>${esc(last.changed.join(", ") || "nothing")}</b> · ${last.same} slide${last.same === 1 ? "" : "s"} pixel-identical to the previous version${last.removed?.length ? ` · removed: ${esc(last.removed.join(", "))}` : ""}.` : ""}
      <ul>${(last.summary || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
      ${(last.asked_same || []).length ? `<div class="flag">Asked, but no visible change on: ${esc(last.asked_same.join(", "))} – write the request again more concretely.</div>` : ""}
      ${(last.not_done || []).length ? `<div class="flag">Not done here: ${last.not_done.map(esc).join(" · ")}</div>` : ""}</div>` : ""}
    <div class="polish">
      <div class="pcol">
        <div class="card pvideo"><h3>The deck in motion</h3>${media}</div>
        <div class="card pslide">
          <div class="pbar"><button class="btn" id="prev" aria-label="Previous slide">‹</button>
            <div class="pnum">Slide <b id="num">1</b> / ${n} <span id="sid" class="muted"></span></div>
            <button class="btn" id="next" aria-label="Next slide">›</button></div>
          <img id="big" alt="">
          <label class="preq"><span>Change request for <b>slide <span id="num2">1</span> only</b></span>
            <textarea id="req" rows="3" placeholder="e.g. shorten the title; put the 23.75 in the accent colour; swap the two columns"></textarea></label>
          <div class="pstrip" id="strip">${pngs.map((p, i) => `<button data-i="${i}" title="Slide ${i + 1}"><img src="${url(p)}" alt="" loading="lazy"><i>${i + 1}</i></button>`).join("")}</div>
        </div>
      </div>
      <div class="panel">
        <h3>Your requests</h3>
        <ul class="issues" id="list"></ul>
        <label class="preq"><span>For the whole deck (optional)</span>
          <textarea id="general" rows="3" placeholder="e.g. one consistent wording for “GeoRA” vs “our method”"></textarea></label>
        <button class="btn primary" id="send" disabled>Send</button>
        <div id="out" class="note"></div>
        ${rf.round ? `<button class="link" id="undo">Back to version ${rf.round} (undo the last round)</button>` : ""}
        ${rf.history.length ? `<h3 style="margin-top:10px">History</h3><ol class="hist">${rf.history.map((h) =>
          `<li><b>Version ${h.round + 1}</b> · ${esc(h.time || "")}<br>${esc((h.summary || []).join(" · ") || "–")}</li>`).join("")}</ol>` : ""}
      </div>
    </div>`);
  let i = 0;
  const show = (k) => {
    i = (k + n) % n;
    $("#big", el).src = url(pngs[i]);
    $("#num", el).textContent = $("#num2", el).textContent = i + 1;
    $("#sid", el).textContent = `· ${ids[i] || ""}`;
    $("#req", el).value = reqs[i] || "";
    $$("#strip button", el).forEach((b) => b.classList.toggle("on", +b.dataset.i === i));
    $(`#strip button[data-i="${i}"]`, el)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  const draw = () => {
    const keys = Object.keys(reqs).filter((k) => k !== "general" && reqs[k]?.trim()).sort((a, b) => a - b);
    $("#list", el).innerHTML = keys.length ? keys.map((k) => `<li><a href="#" data-i="${k}">Slide ${+k + 1}</a> · ${esc(reqs[k])}
      <button class="link" data-x="${k}" aria-label="Remove">✕</button></li>`).join("") : `<li class="muted">None yet: write under a slide.</li>`;
    $$("#strip button", el).forEach((b) => b.classList.toggle("has", !!reqs[b.dataset.i]?.trim()));
    const g = $("#general", el).value.trim();
    const cnt = keys.length + (g ? 1 : 0);
    $("#send", el).disabled = !cnt;
    $("#send", el).textContent = cnt ? `Send ${cnt} change request${cnt === 1 ? "" : "s"} → new version` : "Send";
    $$("#list a", el).forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); show(+a.dataset.i); }));
    $$("#list [data-x]", el).forEach((b) => b.addEventListener("click", () => { delete reqs[b.dataset.x]; saveReq(reqs); draw(); if (+b.dataset.x === i) show(i); }));
  };
  $("#prev", el).addEventListener("click", () => show(i - 1));
  $("#next", el).addEventListener("click", () => show(i + 1));
  $$("#strip button", el).forEach((b) => b.addEventListener("click", () => show(+b.dataset.i)));
  $("#big", el).addEventListener("dblclick", () => lightbox(url(pngs[i])));
  $("#req", el).addEventListener("input", (e) => { reqs[i] = e.target.value; if (!reqs[i].trim()) delete reqs[i]; saveReq(reqs); draw(); });
  $("#general", el).value = reqs.general || "";
  $("#general", el).addEventListener("input", (e) => { reqs.general = e.target.value; saveReq(reqs); draw(); });
  el.addEventListener("keydown", (e) => { if (e.target.tagName === "TEXTAREA") return;
    if (e.key === "ArrowLeft") show(i - 1); if (e.key === "ArrowRight") show(i + 1); });
  show(0); draw();
  const out = $("#out", el);
  $("#send", el).addEventListener("click", async () => {
    const list = Object.keys(reqs).filter((k) => k !== "general" && reqs[k]?.trim())
      .map((k) => ({ id: String(ids[+k] || "").split(".")[0], n: +k + 1, text: reqs[k].trim() }));
    const general = (reqs.general || "").trim();
    const log = working("Applying your changes", `${list.length + (general ? 1 : 0)} request(s). Only these slides change.`);
    try {
      const r = await call("agents", "refine_deck", { session: S.session, requests: list, general }, log);
      log("Rebuilding the deck and its video…");
      await call("deck", "build_deck", { session: S.session, video: true }, log);
      await refresh();
      viewRefine();
    } catch (e) { failed("The changes could not be applied", e, () => viewRefine()); }
  });
  $("#undo", el)?.addEventListener("click", async () => {
    const log = working("Restoring the previous version");
    try {
      await call("deck", "refine_undo", { session: S.session });
      await call("deck", "build_deck", { session: S.session, video: true }, log);
      await refresh(); viewRefine();
    } catch (e) { failed("Undo failed", e, () => viewRefine()); }
  });
  $("#happy", el).addEventListener("click", async () => {
    const left = Object.keys(reqs).filter((k) => reqs[k]?.trim()).length;
    if (left && !confirm(`You have ${left} unsent request(s). Go to the export without them?`)) return;
    await call("deck", "refine_approve", { session: S.session });
    await refresh(); go("deck");
  });
}

async function viewDeck() {
  if (!done("refine")) return go(done("motion") ? "refine" : firstOpen());
  const d = S.status.deck;
  const ch = S.status.choices;
  const el = screen(`
    <div class="head"><div><h1>The deck is ready.</h1><p class="sub">${d.slides} slides</p></div></div>
    <div class="final">
      <div><div class="card">${artSlides({ pngs: d.pngs, label: "final deck" })}</div></div>
      <div class="panel">
        <button class="btn primary big" id="open">Open in PowerPoint</button>
        <button class="btn" id="folder">Open folder</button>
        <button class="btn" id="pdf">Export PDF and video</button>
        ${S.status.spec?.script ? `<a class="btn" href="${url(S.status.spec.script)}" target="_blank">Presenter script</a>` : ""}
        <div id="out" class="note"></div>
        <h3 style="margin-top:12px">Your choices</h3>
        <ul class="issues" style="color:var(--ink-2)">${STAGES.map((s) => `<li>${esc(STEPS.find(([id]) => id === s)[1])}: <b>${esc(ch[s]?.label || "–")}</b></li>`).join("")}</ul>
        <button class="link" id="rebuild">← Back to Polish (more changes)</button>
      </div></div>`);
  $$(".flip", el).forEach((f) => wireFlip(f.parentElement));
  const out = $("#out", el);
  const act = (btn, fn) => btn.addEventListener("click", async () => { btn.disabled = true; try { await fn(); } catch (e) { out.innerHTML = `<span class="flag">${esc(e.message)}</span>`; } btn.disabled = false; });
  act($("#open", el), () => call("office", "open_deck", { session: S.session, what: "pptx" }));
  act($("#folder", el), () => call("office", "open_deck", { session: S.session, what: "folder" }));
  act($("#pdf", el), async () => {
    out.textContent = "PowerPoint is exporting… (the video takes a few minutes)";
    const r = await call("office", "export_deck", { session: S.session, png: false, pdf: true, mp4: true }, (m) => { out.textContent = m; });
    out.innerHTML = r.files.map((f) => `<a href="${url(f)}" target="_blank">${esc(f.split("/").pop())}</a>`).join("<br>");
  });
  act($("#rebuild", el), async () => { await call("deck", "refine_approve", { session: S.session, ok: false }); await refresh(); go("refine"); });
}

// Claude looks at the finished slides and applies the layout fixes it finds - silently, no report.
async function polish(log) {
  try {
    log("Checking the slides…");
    const r = await call("agents", "review_deck", { session: S.session, target: "deck" });
    if ((r.slides || []).some((i) => ["variant", "text"].includes((i.fix || {}).type))) {
      log("Polishing the layout…");
      await call("deck", "apply_review_fixes", { session: S.session, target: "deck" }, log);
    }
  } catch (e) { console.warn("review skipped:", e); }
}

function lightbox(src) { const lb = $("#lightbox"); $("img", lb).src = src; lb.hidden = false; }
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
$("#home").addEventListener("click", () => { S.session = null; S.status = null; drawRail(); go("paper"); });

// ------------------------------------------------------------------ boot
(async () => {
  env();
  const [hs, hstep] = location.hash.slice(1).split("/");   // only an explicit link (e.g. F5 mid-work) resumes
  const list = hs ? await call("content", "list_sessions").catch(() => []) : [];
  if (hs && list.some((s) => s.name === hs)) await openSession(hs, hstep || "paper");
  else go("paper");
})();
