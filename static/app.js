"use strict";
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- state

const state = { char: "", chars: [], gen: 0, status: null };      // gen bumps on every character switch
const player = { groups: [], gi: 0, frames: [], images: [], box: null, index: 0, timer: null };
const pal = { slot: "", rows: null, original: null, used: [], rev: 0, hover: null, try: "" };   // try: colours being previewed, not saved yet
const rowmaps = new Map();   // row-map URL -> { data: ImageData } once loaded, or "loading"
let highlightCache = null;   // last overlay drawn: { key, canvas, count }
const view = { stages: [], backdrop: "checker", stageImg: null, stageMeta: null, anchor: null,
  zoom: "fit", panX: 0, panY: 0, xf: null };     // zoom: "stage" | "fit" | css px per game px; pan in game px
const ZOOM_MIN = 0.1, ZOOM_MAX = 2;               // 10% .. 200%
const ZOOM_STEPS = [0.1, 0.15, 0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2];
const sel = { strip: new Set(), stripAnchor: -1, edited: new Set(), editedAnchor: -1 };
let current = null;          // { category, move } of the open move
let edited = { frames: [], palettes: [], folders: {}, missing: [] };
const moveButtons = new Map();   // "category/move" -> sidebar button

// ---------------------------------------------------------------- helpers

const withChar = (path) => path + (path.includes("?") ? "&" : "?") + "char=" + encodeURIComponent(state.char);

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Skin-Editor": "1" },
    body: JSON.stringify({ char: state.char, ...body }),
  };
  const res = await fetch(body === undefined && state.char ? withChar(path) : path, opt);
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

// The message strip. Plain and "ok" messages fade after a few seconds; errors, work in progress
// (text ending in …) and messages passed keep=true stay until closed with the X or Esc.
let msgTimer = null, msgFades = false;
function showMsg(text, kind, keep) {
  const m = $("msg");
  clearTimeout(msgTimer);
  $("msgText").textContent = text || "";
  m.className = "strip " + (kind || "");
  m.hidden = !text;
  msgFades = !!text && kind !== "bad" && !keep && !/…$/.test(text);
  if (msgFades && !m.matches(":hover")) armMsgFade();
}
function armMsgFade() {
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => {
    $("msg").classList.add("fading");
    msgTimer = setTimeout(() => showMsg(""), 400);
  }, 6000);
}
$("msg").onmouseenter = () => { clearTimeout(msgTimer); $("msg").classList.remove("fading"); };
$("msg").onmouseleave = () => { if (msgFades && !$("msg").hidden) armMsgFade(); };
$("msgClose").onclick = () => showMsg("");

// In-page confirmation (the browser's own confirm() box is blocked in some embedded browsers).
// check: { label } adds a tick box; its state afterwards is in askChecked.
let askChecked = false;
function ask(text, okLabel, danger, check) {
  return new Promise((resolve) => {
    const dlg = $("askDlg");
    $("askText").textContent = text;
    $("askCheckWrap").hidden = !check;
    $("askCheck").checked = false;
    if (check) $("askCheckText").textContent = check.label;
    $("askOk").textContent = okLabel || "OK";
    $("askOk").className = "tb " + (danger ? "danger" : "primary");
    const done = (answer) => {
      askChecked = !!check && $("askCheck").checked;
      $("askOk").onclick = $("askCancel").onclick = dlg.oncancel = null;
      dlg.close();
      resolve(answer);
    };
    $("askOk").onclick = () => done(true);
    $("askCancel").onclick = () => done(false);
    dlg.oncancel = (e) => { e.preventDefault(); done(false); };
    dlg.showModal();
    $("askCancel").focus();
  });
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const pngUrl = (name, thumb) => withChar("/api/png?name=" + encodeURIComponent(name) + (thumb ? "&thumb=1" : ""));
const editedUrl = (name, v) =>
  withChar("/api/png?name=" + encodeURIComponent(name) + "&edited=1&v=" + encodeURIComponent(v || ""));
// timeline thumbnail: drawn with the costume slot being edited, so the strip matches the stage
function thumbUrl(f) {
  if (pal.slot && f.type === 3) {
    return withChar("/api/png?name=" + encodeURIComponent(f.name) + "&palette=" + encodeURIComponent(pal.slot) +
      "&thumb=1&v=" + pal.rev + (f.edit ? f.edit.png_sha256.slice(0, 8) : ""));
  }
  return f.edit ? editedUrl(f.name, f.edit.png_sha256) : pngUrl(f.name, true);
}
function frameUrl(f, thumb) {
  if (!thumb && pal.slot && f.type === 3) {     // preview with the palette being edited
    return withChar("/api/png?name=" + encodeURIComponent(f.name) + "&palette=" + encodeURIComponent(pal.slot) +
      "&v=" + pal.rev + (f.edit ? f.edit.png_sha256.slice(0, 8) : "") + (pal.try ? "&try=" + pal.try : ""));
  }
  return f.edit ? editedUrl(f.name, f.edit.png_sha256) : pngUrl(f.name, thumb);
}
const CATEGORY_LABELS = {
  supportchar: "Supports", hitstun: "Hit reactions", flow_attack: "Flow attacks", flow_defense: "Flow defense",
  flow: "Flow", poses: "Win and lose poses", story_poses: "Story poses", supers: "Supers", normals: "Normals",
  specials: "Specials", throws: "Throws", idle: "Idle", walk: "Walk", jump: "Jump", dash: "Dash",
  blocking: "Blocking", hud: "Interface art", "(other)": "Other",
};
const catLabel = (name) => CATEGORY_LABELS[name] || name.replace(/_/g, " ");
const typing = (e) => /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) || e.target.isContentEditable;
// a field with its own text undo (Ctrl+Z belongs to it)
const inTextField = (e) => e.target.tagName === "TEXTAREA" || e.target.isContentEditable ||
  (e.target.tagName === "INPUT" && /^(text|search|number|)$/.test(e.target.type));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};

// ---------------------------------------------------------------- popovers

function closePopovers(except) {
  for (const [pop, btn] of [["charPop", "charBtn"], ["gameMenu", "gameMenuBtn"], ["exportPop", "exportOptsBtn"]]) {
    if (pop === except) continue;
    $(pop).hidden = true;
    $(btn).setAttribute("aria-expanded", "false");
  }
}

function togglePopover(pop, btn) {
  const open = $(pop).hidden;
  closePopovers(open ? pop : null);
  $(pop).hidden = !open;
  $(btn).setAttribute("aria-expanded", String(open));
  return open;
}

document.addEventListener("mousedown", (e) => {
  if (!e.target.closest(".popover, #charBtn, #gameMenuBtn, #exportOptsBtn")) closePopovers();
});

// ---------------------------------------------------------------- characters

async function loadCharacters() {
  const data = await api("/api/characters");
  state.chars = data.characters;
  renderCharList();
  const installed = data.characters.filter((c) => c.installed).map((c) => c.name);
  return data;
}

function renderCharList() {
  const q = $("charSearch").value.trim().toLowerCase();
  const list = $("charList");
  list.textContent = "";
  const hits = state.chars.filter((c) => !q || c.name.includes(q));
  const mains = hits.filter((c) => c.main), more = hits.filter((c) => !c.main);
  const add = (c) => {
    const b = el("button", "charitem");
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", String(c.name === state.char));
    b.append(el("span", null, c.name));
    if (c.edits || c.colors) {
      const d = el("span", "edits");
      d.title = `${plural(c.edits, "edited frame")}, ${plural(c.colors, "changed colour")}`;
      b.append(d);
    }
    if (c.installed) b.append(el("span", "inst", "installed"));
    b.append(el("span", "tag", `${c.frames} frames`));
    b.onclick = () => { closePopovers(); selectChar(c.name); };
    list.append(b);
  };
  mains.forEach(add);
  if (more.length) {
    list.append(el("div", "charsec", "More: variants and bosses"));
    more.forEach(add);
  }
  if (!hits.length) list.append(el("p", "muted small", "No character matches."));
}

async function selectChar(name) {
  const gen = ++state.gen;
  state.char = name;
  store.set("skin-editor-char", name);
  stop();
  current = null;
  pal.slot = ""; pal.rows = pal.original = null; pal.used = [];
  sel.strip.clear(); sel.edited.clear();
  closeSwatchEditor();
  $("charName").textContent = name;
  renderCharList();
  $("tree").textContent = "";
  $("moveView").hidden = $("editedView").hidden = true;
  $("results").hidden = true;
  $("empty").hidden = false;
  $("empty").textContent = `Checking ${name}…`;
  $("browser").hidden = false;
  showMsg("");
  try {
    let s = await refreshStatus();
    if (gen !== state.gen) return;
    if (!s.backup_ok) {
      $("empty").textContent = s.backup_error || "No verified backup for this character.";
      return;
    }
    if (!s.cache_ready) {
      $("empty").textContent = `Unpacking ${name} for the first time… this can take up to a minute.`;
      await api("/api/setup", {});
      if (gen !== state.gen) return;
      s = await refreshStatus();
    }
    const [tree] = await Promise.all([api("/api/moves"), loadPaletteList(), loadEdited(), loadStages()]);
    if (gen !== state.gen) return;
    buildTree(tree);
    const first = moveButtons.get("idle/stand") || moveButtons.get(store.get("skin-editor-move-" + name) || "");
    if (first) await first.open(); else $("empty").textContent = "Pick a move on the left.";
  } catch (e) {
    if (gen === state.gen) { $("empty").textContent = ""; showMsg(e.message, "bad"); }
  }
}

async function refreshStatus() {
  const s = await api("/api/status");
  if (s.character !== state.char) return s;
  state.status = s;
  const box = $("gameStatus");
  const looks = {
    original: ["original", `${s.character}: original in game`],
    modded: ["modded", `${s.character}: your mod in game`],
    changed: ["changed", `${s.character}: game pak changed`],
    missing: ["missing", `${s.character}: pak not found`],
    unknown: ["missing", `${s.character}: unknown pak`],
  };
  const [cls, main] = looks[s.game_pak] || looks.unknown;
  box.className = "status " + (s.backup_ok ? cls : "nobackup");
  $("statusMain").textContent = main;
  $("statusSub").textContent = !s.backup_ok ? "no verified backup"
    : s.game_pak === "changed" ? "game updated? Scan game" : "backup verified";
  box.title = s.backup_ok ? "Backup SHA256 " + s.backup_hash : (s.backup_error || "");
  const colors = s.colors || 0;
  const total = s.edits + colors;
  $("editedCount").textContent = total;
  $("editedNavCount").textContent = total;
  $("editedBtn").title = `${s.character} in project ${s.project}: ${plural(s.edits, "edited frame")}, ${plural(colors, "changed colour")}`;
  $("installBtn").disabled = !s.backup_ok || !total || s.game_pak === "changed";
  $("installBtn").title = s.game_pak === "changed"
    ? "The game's pak changed (game update?). Use Scan game folder first."
    : "Build this character's pak with your edits and copy it into the game folder";
  $("restoreBtn").disabled = !s.backup_ok || s.game_pak === "original" || s.game_pak === "changed";
  return s;
}

async function refreshAll() {
  await Promise.all([refreshStatus(), loadCharacters(), loadEdited()]);
}

async function saveGameDir() {
  $("setupBtn").disabled = true;
  try {
    await api("/api/game-dir", { game_dir: $("gameDir").value });
    showMsg("");
    await openScan();
  } catch (e) {
    showMsg(e.message, "bad");
  }
  $("setupBtn").disabled = false;
}

// ---------------------------------------------------------------- scan the game folder

const SCAN_TEXT = {
  new: "not backed up yet",
  ok: "up to date",
  modded: "your mod is installed (Restore original to scan it)",
  update: "the game changed this pak (game update?)",
};

async function openScan() {
  closePopovers();
  const dlg = $("scanDlg");
  if (!dlg.open) dlg.showModal();
  $("scanInfo").textContent = "Scanning the game folder… the first scan reads every pak and can take half a minute.";
  $("scanList").textContent = "";
  $("scanAll").hidden = true;
  try {
    renderScan(await api("/api/scan", {}));
  } catch (e) {
    $("scanInfo").textContent = e.message;
  }
}

function renderScan(data) {
  const paks = data.paks;
  const news = paks.filter((p) => p.state === "new");
  const updates = paks.filter((p) => p.state === "update");
  $("scanInfo").textContent = `${data.game_dir} — ${plural(paks.length, "character pak")} found` +
    (news.length ? `, ${news.length} not backed up yet` : "") +
    (updates.length ? `, ${updates.length} changed by a game update` : "") + ".";
  const list = $("scanList");
  list.textContent = "";
  for (const p of paks) {
    const row = el("div", "srow");
    row.append(el("span", "nm", p.name), el("span", "st " + p.state, SCAN_TEXT[p.state]));
    if (p.state === "new" || p.state === "update") {
      const b = el("button", "tb", p.state === "new" ? "Back up" : "Update backup");
      b.onclick = () => scanAction(p, b);
      row.append(b);
    }
    list.append(row);
  }
  $("scanAll").hidden = news.length < 2;
  $("scanAll").onclick = async () => {
    $("scanAll").disabled = true;
    for (const p of news) { if (!await scanAction(p, null, true)) break; }
    $("scanAll").disabled = false;
    await openScan();
  };
}

async function scanAction(p, btn, quiet) {
  if (p.state === "update") {
    if (!await ask(`The game's ${p.name}.pak is different from your backup, so the game probably updated.\n\n` +
                   "Make sure this is the stock file (verify game files in Steam; Restore original if you installed a mod). " +
                   "Your old backup is kept in backup\\previous and your edits are re-applied to the new version.",
                   "Update backup")) return false;
  }
  if (btn) { btn.disabled = true; btn.textContent = "working…"; }
  try {
    await api(p.state === "new" ? "/api/backup" : "/api/refresh-backup", { char: p.name });
  } catch (e) {
    $("scanInfo").textContent = `${p.name}: ${e.message}`;
    if (btn) { btn.disabled = false; btn.textContent = p.state === "new" ? "Back up" : "Update backup"; }
    return false;
  }
  if (!quiet) await openScan();
  await loadCharacters();
  if (!$("setup").hidden) await start();
  return true;
}

// ---------------------------------------------------------------- sharing: skin packs

function openShare() {
  const frames = edited.frames.length;
  const colors = edited.palettes.reduce((n, p) => n + p.colors, 0);
  $("shareSummary").textContent = `${state.char}: ${plural(frames, "edited frame")} and ${plural(colors, "changed colour")} in this project.`;
  if (!$("shareName").value) $("shareName").value = `${state.char} skin`;
  if (!$("shareAuthor").value) $("shareAuthor").value = store.get("skin-editor-author") || "";
  $("shareResult").textContent = "";
  $("shareGo").disabled = !(frames || colors);
  if (!(frames || colors)) $("shareResult").textContent = "Nothing to share yet: edit some frames or colours first.";
  if (!$("shareDlg").open) $("shareDlg").showModal();
}

async function createPack() {
  $("shareGo").disabled = true;
  $("shareResult").textContent = "Creating…";
  try {
    store.set("skin-editor-author", $("shareAuthor").value);
    const r = await api("/api/skin/export", {
      name: $("shareName").value, author: $("shareAuthor").value, description: $("shareDesc").value,
      frames: $("shareFrames").checked, palettes: $("shareColors").checked,
    });
    const box = $("shareResult");
    box.textContent = "";
    box.append(el("div", "ok", `Created ${plural(r.frames, "frame")} + ${plural(r.colors, "colour")} (${(r.size / 1024).toFixed(0)} KB):`),
               el("div", null, r.path));
    const open = el("button", "tb", "Open exports folder");
    open.onclick = () => openFolder("exports");
    const file = r.path.split(/[\\/]/).pop();
    const dl = el("a", "dl", "Download");
    dl.href = "/api/skin/download?name=" + encodeURIComponent(file);
    dl.download = file;
    box.append(open, dl);
  } catch (e) {
    $("shareResult").textContent = e.message;
  }
  $("shareGo").disabled = false;
}

const imp = { file: null, report: null, slots: {} };

async function postPack(file, mode, extra) {
  const form = new FormData();
  form.append("char", state.char);
  form.append("mode", mode);
  form.append("file", file, file.name);
  for (const [k, v] of Object.entries(extra || {})) form.append(k, v);
  const res = await fetch("/api/skin/import", { method: "POST", headers: { "X-Skin-Editor": "1" }, body: form });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

async function importFile(file) {
  if ($("browser").hidden) return;
  showMsg("Reading the skin pack…");
  let report;
  try {
    report = await postPack(file, "check");
  } catch (e) {
    showMsg(e.message, "bad");
    return;
  }
  if (!report.character_ok) {
    if (state.chars.some((c) => c.name === report.character)) {
      await selectChar(report.character);
      return importFile(file);
    }
    showMsg(`This skin is for ${report.character}, which is not set up here yet. Use Scan game folder to back it up, then import again.`, "bad");
    return;
  }
  showMsg("");
  imp.file = file;
  imp.report = report;
  renderImport(report);
  if (!$("importDlg").open) $("importDlg").showModal();
}

function renderImport(r) {
  const body = $("importBody");
  body.textContent = "";
  const wrap = el("div", "imp");
  const head = el("div", "head");
  if (r.preview) { const img = el("img"); img.src = r.preview; img.alt = ""; head.append(img); }
  const meta = el("div");
  meta.append(el("strong", null, r.name));
  meta.append(el("div", "muted", `for ${r.character}` + (r.author ? ` · by ${r.author}` : "") + (r.created ? ` · ${r.created}` : "")));
  if (r.description) meta.append(el("div", null, r.description));
  head.append(meta);
  wrap.append(head);
  if (r.pak_match === false) {
    wrap.append(el("div", "warn", "Made for a different version of the game. The art is re-fitted to yours; check it afterwards."));
  }
  const by = (s) => r.frames.filter((f) => f.state === s);
  const usable = by("new").length + by("overwrite").length;
  wrap.append(el("h3", null, `Frames: ${usable} can be imported`));
  const lines = [["new", "new"], ["overwrite", "replace a frame you already edited"], ["missing", "not in your game version (skipped)"],
                 ["size", "wrong size (skipped)"], ["bad", "damaged (skipped)"]];
  const ul = el("ul");
  for (const [s, text] of lines) {
    const list = by(s);
    if (!list.length) continue;
    const li = el("li", s === "new" ? "ok" : s === "overwrite" ? "warn" : "bad");
    li.textContent = `${list.length} ${text}`;
    li.title = list.slice(0, 30).map((f) => f.label + (f.detail ? ` (${f.detail})` : "")).join("\n");
    ul.append(li);
  }
  if (!r.frames.length) ul.append(el("li", "muted", "no frames (colours only)"));
  wrap.append(ul);
  imp.slots = {};
  if (r.palettes.length) wrap.append(el("h3", null, "Palette colours"));
  for (const p of r.palettes) {
    const row = el("div", "prow2");
    row.append(el("span", null, `${p.slot} · ${plural(p.colors, "colour")} →`));
    const s = el("select");
    for (const name of r.slots) { const o = el("option", null, name); o.value = name; s.append(o); }
    const skip = el("option", null, "(skip)");
    skip.value = "";
    s.append(skip);
    s.value = p.target_ok ? p.slot : "";
    imp.slots[p.slot] = s;
    row.append(s);
    if (p.conflict) row.append(el("span", "warn", "has your colours"));
    wrap.append(row);
  }
  const conflicts = by("overwrite").length + r.palettes.filter((p) => p.conflict).length;
  if (conflicts) {
    const lab = el("label", "chk");
    const box = el("input");
    box.type = "checkbox";
    box.id = "impOverwrite";
    lab.append(box, ` replace my existing edits (${conflicts} would be overwritten); unticked keeps mine`);
    wrap.append(lab);
  }
  wrap.append(el("p", "muted", "Imported frames and colours go into your project; nothing is installed in the game until you click Build & install."));
  body.append(wrap);
  $("importGo").hidden = false;
  $("importGo").disabled = !(usable || r.palettes.length);
}

async function applyImport() {
  $("importGo").disabled = true;
  const note = el("p", "muted", "Importing… fitting frames to your game's palette can take a few seconds each.");
  $("importBody").append(note);
  try {
    const slotMap = {};
    for (const [slot, s] of Object.entries(imp.slots)) slotMap[slot] = s.value;
    const result = await postPack(imp.file, "apply", {
      overwrite: $("impOverwrite") && $("impOverwrite").checked ? "1" : "0",
      slot_map: JSON.stringify(slotMap),
    });
    const count = (list, s) => list.filter((x) => x.state === s).length;
    const body = $("importBody");
    body.textContent = "";
    const wrap = el("div", "imp");
    wrap.append(el("h3", null, "Done"));
    const ul = el("ul");
    const add = (cls, text) => ul.append(el("li", cls, text));
    add("ok", `${plural(count(result.frames, "imported"), "frame")} imported, ${plural(count(result.palettes, "imported"), "palette")} applied`);
    if (count(result.frames, "kept") + count(result.palettes, "kept")) {
      add("warn", `${count(result.frames, "kept") + count(result.palettes, "kept")} kept your existing edits`);
    }
    for (const x of [...result.frames, ...result.palettes].filter((x) => x.state === "failed")) {
      add("bad", `${x.label || x.slot}: ${x.detail}`);
    }
    wrap.append(ul, el("p", "muted", "Check the result on the stage, then Build & install to try it."));
    body.append(wrap);
    $("importGo").hidden = true;
    await refreshAll();
    await loadPaletteList();
    if (current) openMove(current.category, current.move);
  } catch (e) {
    note.textContent = e.message;
    note.className = "bad";
    $("importGo").disabled = false;
  }
}

// ---------------------------------------------------------------- build / install / export

async function rebuildCheck() {
  closePopovers();
  showMsg(`Rebuilding ${state.char}.pak with no edits…`);
  try {
    const r = await api("/api/rebuild-check", {});
    showMsg(r.identical
      ? `Rebuild OK: ${r.files} files, byte-identical to the original (${r.hash.slice(0, 12)}…). ` +
        (r.palettes_identical ? `Palette round trip OK (${r.palettes} palettes).` : "Palette round trip DIFFERS: do not install palette edits.")
      : `Rebuild DIFFERS from the original: got ${r.hash.slice(0, 12)}…, expected ${(r.expected || "?").slice(0, 12)}…`,
      r.identical && r.palettes_identical ? "ok" : "bad");
  } catch (e) {
    showMsg(e.message, "bad");
  }
}

const MOD_NOTICE = "Modified files have been tested in online matches without issues. As with any mod, the game's developers " +
  "could change their rules at any time, so use it at your own risk.";

function updateNoticeMenu() { $("noticeBtn").hidden = !settings.hide_install_notice; }

async function showNoticeAgain() {
  closePopovers();
  try {
    settings = await api("/api/settings", { hide_install_notice: false });
    updateNoticeMenu();
    showMsg("The modding reminder will show before Build & install again.");
  } catch (e) {
    showMsg(e.message, "bad");
  }
}

async function installMod() {
  const s = state.status || {};
  const colors = s.colors || 0;
  await loadSettings();
  const notice = !settings.hide_install_notice;
  if (!await ask(`Build ${state.char}.pak with your edits and put it in the game folder?\n\n` +
                 `${plural(s.edits || 0, "edited frame")}, ${plural(colors, "changed colour")}. Your verified backup stays untouched, ` +
                 "and Restore original puts it back at any time.\n\nClose the game first." +
                 (notice ? "\n\n" + MOD_NOTICE : ""),
                 "Build & install", true, notice ? { label: "Don't remind me again" } : null)) return;
  if (notice && askChecked) {
    try { settings = await api("/api/settings", { hide_install_notice: true }); updateNoticeMenu(); } catch (e) { /* shown again next time */ }
  }
  $("installBtn").disabled = true;
  showMsg("Building and installing…");
  try {
    const r = await api("/api/install", {});
    showMsg(`Installed ${state.char}: ${plural(r.edits, "edited frame")}, ${plural(r.colors, "changed colour")}. ` +
            (r.skipped ? `${plural(r.skipped, "edited frame")} no longer in the game were skipped. ` : "") +
            "Test it in Training mode, and use Restore original when you want the stock look back.", "ok", true);
  } catch (e) {
    showMsg(e.message, "bad");
  }
  await refreshAll();
}

async function restoreOriginal() {
  if (!await ask(`Copy the original ${state.char}.pak back into the game folder?\n\nClose the game first.`, "Restore original", true)) return;
  $("restoreBtn").disabled = true;
  showMsg("Restoring the original pak…");
  try {
    const r = await api("/api/restore", {});
    showMsg(`Original ${state.char}.pak restored and verified (${r.hash.slice(0, 12)}…).`, "ok");
  } catch (e) {
    showMsg(e.message, "bad");
  }
  await refreshAll();
}

// "Export move" shows the painting rules first (until "Don't show this again" is ticked; the ?
// button always opens them). The tick and the chosen folder live in config.json on disk.
let settings = { hide_export_rules: false, export_folder: "", default_export_folder: "" };

async function loadSettings() {
  try { settings = await api("/api/settings"); } catch (e) { /* keep the last known ones */ }
}

function paintExportFolder() {
  $("expFolderNow").textContent = settings.export_folder || `${settings.default_export_folder} (default)`;
  $("expDefault").disabled = !settings.export_folder;
}

async function openExportDlg() {
  if (!current) return;
  closePopovers();
  await loadSettings();
  $("expPalette").checked = store.get("skin-editor-export-palette") !== "0";
  $("expHide").checked = settings.hide_export_rules;
  $("expFolderType").hidden = true;
  $("expErr").textContent = "";
  paintExportFolder();
  $("exportDlg").showModal();
  $("expGo").focus();
}

async function setExportFolder(folder) {
  try {
    settings = await api("/api/settings", { export_folder: folder });
    $("expErr").textContent = "";
    $("expFolderType").hidden = true;
    paintExportFolder();
  } catch (e) {
    $("expErr").textContent = e.message;
  }
}

async function chooseExportFolder() {
  const bridge = window.pywebview && window.pywebview.api;
  if (bridge && bridge.choose_folder) {            // the Windows app: a real folder picker
    try {
      const folder = await bridge.choose_folder();
      if (folder) await setExportFolder(folder);
    } catch (e) {
      $("expErr").textContent = "The folder picker could not open. Paste the folder's path instead.";
      $("expFolderType").hidden = false;
    }
  } else {                                         // a browser page cannot open one: type the path
    $("expFolderType").hidden = false;
    $("expFolderText").value = settings.export_folder;
    $("expFolderText").focus();
  }
}

async function exportFromDlg() {
  store.set("skin-editor-export-palette", $("expPalette").checked ? "1" : "0");
  if ($("expHide").checked !== settings.hide_export_rules) {
    try { settings = await api("/api/settings", { hide_export_rules: $("expHide").checked }); } catch (e) { /* asked again next time */ }
  }
  $("exportDlg").close();
  exportMove();
}

async function exportClicked() {
  if (!current) return;
  await loadSettings();
  if (settings.hide_export_rules) exportMove(); else openExportDlg();
}

async function exportMove() {
  if (!current) return;
  $("exportBtn").disabled = true;
  const withPalette = store.get("skin-editor-export-palette") !== "0";
  showMsg(`Exporting ${current.move}${withPalette ? " and the palette image" : ""}. This can take up to a minute; ` +
          "the folder opens by itself when it is ready…");
  try {
    const r = await api("/api/export", { ...current, effects: $("expEffects").checked, masks: $("expMasks").checked,
                                         margin: Number($("expSpace").value),
                                         palette: withPalette });
    let text = `Exported ${plural(r.written, "PNG")} to ${r.folder}`;
    if (r.palette) text += `, plus ${r.palette} to pick colours from`;
    if (r.canvas) text += `: character frames share one ${r.canvas[2]}×${r.canvas[3]} canvas with room to draw beyond the outline, your current edits included`;
    if (r.kept) text += `. ${r.kept} already there were left untouched`;
    if (r.skipped_effects) text += `. ${plural(r.skipped_effects, "effect image")} not exported (tick “effects”)`;
    if (r.skipped_masks) text += `. ${plural(r.skipped_masks, "mask image")} not exported (tick “masks”)`;
    if (r.failed) text += `. ${r.failed} could not be read`;
    showMsg(text + ".", "ok");
  } catch (e) {
    showMsg(e.message, "bad");
  }
  $("exportBtn").disabled = false;
}

async function openFolder(which) {
  try {
    const r = await api("/api/open-folder", { which });
    showMsg(`Opened ${r.folder}`, "ok");
  } catch (e) {
    showMsg(e.message, "bad");
  }
}

// ---------------------------------------------------------------- drag & drop replace

async function replaceFiles(files) {
  const list = [...files];
  if (!list.length) return;
  if (!current) {
    const first = moveButtons.get("idle/stand") || moveButtons.values().next().value;
    if (first) await first.open();
  }
  const box = $("results");
  box.hidden = false;
  box.textContent = "";
  const head = el("div", "head");
  const title = el("strong", null, `Replacing 0 / ${list.length}…`);
  const close = el("button", "tb icononly");
  close.setAttribute("aria-label", "Close");
  close.innerHTML = '<svg class="ico"><use href="#i-close"/></svg>';
  close.onclick = () => { box.hidden = true; };
  head.append(title, el("span", "spacer"), close);
  box.append(head);
  showMsg(`Fitting colours to ${state.char}'s palette… the very first file for a character can take about a minute.`);
  let ok = 0;
  for (let i = 0; i < list.length; i++) {
    const file = list[i];
    const row = el("div", "res");
    const info = el("div", "info");
    info.append(el("div", "fn", file.name));
    const line = el("div", null, "working…");
    info.append(line);
    row.append(info);
    box.append(row);
    try {
      const form = new FormData();
      form.append("char", state.char);
      form.append("file", file, file.name);
      const res = await fetch("/api/replace", { method: "POST", headers: { "X-Skin-Editor": "1" }, body: form });
      const r = await res.json().catch(() => null);
      if (!res.ok) throw new Error((r && r.error) || `Request failed (${res.status})`);
      ok++;
      const pair = el("div", "pair");
      for (const [label, src] of [["before", pngUrl(r.name, false)], ["after", editedUrl(r.name, r.png_sha256)]]) {
        const fig = el("figure"), img = el("img");
        img.src = src;
        img.alt = label;
        fig.append(img, el("figcaption", null, label));
        pair.append(fig);
      }
      row.prepend(pair);
      line.textContent = r.kind === "effect" ? "Replaced · effect image, stored exactly as drawn"
        : `Replaced · ${r.repainted_pixels} repainted pixels fitted to the palette (mean colour error ${r.mean_error}); the rest kept exactly as in the game` +
          (r.grown ? ` · the frame grew to ${r.crop[2]}×${r.crop[3]} to fit your drawing` : "");
      line.className = "ok";
      if (r.far_pixels) {
        const eg = r.far_examples.map((c) => `rgb(${c.join(",")})`).join(", ");
        info.append(el("div", "warn", `${r.far_pixels} pixels are far from the palette and were changed noticeably (e.g. ${eg}).`));
      }
    } catch (e) {
      line.textContent = e.message;
      line.className = "bad";
    }
    title.textContent = `Replacing ${i + 1} / ${list.length}…`;
  }
  title.textContent = `${ok} of ${list.length} replaced`;
  showMsg(ok ? "Done. Use Build & install to try it in game." : "Nothing was replaced.", ok ? "ok" : "bad");
  await refreshAll();
  if (current) await reloadMove();
}

let dragDepth = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  $("dropVeil").hidden = $("browser").hidden;
});
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("dragleave", (e) => {
  if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; $("dropVeil").hidden = true; }
});
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  $("dropVeil").hidden = true;
  if ($("browser").hidden) return;
  const files = [...e.dataTransfer.files];
  const pack = files.find((f) => /\.zip$/i.test(f.name));
  if (pack) importFile(pack); else replaceFiles(files);
});

// ---------------------------------------------------------------- reverting (one or many)

async function revertNames(names, what) {
  const list = [...new Set(names)];
  if (!list.length) return false;
  if (!await ask(`Revert ${what || plural(list.length, "frame")} to the game's original?\n\n` +
                 "Your edited PNGs for them are removed from the project. The copies you exported to the work folder are kept.",
                 list.length === 1 ? "Revert" : `Revert ${list.length}`)) return false;
  try {
    const r = await api("/api/revert", { names: list });
    showMsg(`Reverted ${plural(r.reverted, "frame")}.`, "ok");
  } catch (e) {
    showMsg(e.message, "bad");
    return false;
  }
  sel.strip.clear(); sel.edited.clear();
  await refreshAll();
  if (current) await reloadMove(); else showEdited();
  return true;
}

// ---------------------------------------------------------------- edited files

async function loadEdited() {
  const asked = state.char;
  const data = await api("/api/edited");
  if (asked !== state.char) return;
  edited = data;
  markTree();
  if (!$("editedView").hidden) showEdited();
}

function markTree() {
  const counts = new Map();
  for (const f of edited.frames) {
    const key = f.category + "/" + f.move;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  for (const [key, btn] of moveButtons) {
    let badge = btn.querySelector(".ed");
    const n = counts.get(key);
    if (n && !badge) { badge = el("span", "ed"); btn.insertBefore(badge, btn.querySelector(".n")); }
    if (badge) { badge.textContent = n ? `${n} edited` : ""; badge.hidden = !n; }
  }
  for (const d of $("tree").children) {
    d.classList.toggle("has-edits", !!d.querySelector(".ed:not([hidden])"));
  }
}

function selectTreeButton(btn) {
  for (const b of document.querySelectorAll(".mv.sel, .navbtn.sel")) b.classList.remove("sel");
  if (btn) btn.classList.add("sel");
  $("editedBtn").setAttribute("aria-pressed", String(btn === $("editedNav")));
}

// Windows-style selection: click = only this, Ctrl = toggle, Shift = range from the anchor.
function pick(set, keys, i, e, anchorName) {
  const key = keys[i];
  if (e.shiftKey && sel[anchorName] >= 0) {
    if (!e.ctrlKey && !e.metaKey) set.clear();
    const [a, b] = [Math.min(sel[anchorName], i), Math.max(sel[anchorName], i)];
    for (let k = a; k <= b; k++) set.add(keys[k]);
  } else if (e.ctrlKey || e.metaKey) {
    set.has(key) ? set.delete(key) : set.add(key);
    sel[anchorName] = i;
  } else {
    set.clear();
    set.add(key);
    sel[anchorName] = i;
  }
}

function showEdited() {
  stop();
  current = null;
  selectTreeButton($("editedNav"));
  $("empty").hidden = true;
  $("moveView").hidden = true;
  $("editedView").hidden = false;
  $("editedTitle").textContent = `Edited files: ${state.char}`;
  $("editedPath").textContent = `Your edited PNGs are kept in ${edited.folders.edits || ""}`;
  const grid = $("editedGrid");
  grid.textContent = "";
  $("editedNone").hidden = edited.frames.length > 0;
  $("edSelectAll").hidden = !edited.frames.length;
  const keys = edited.frames.map((f) => f.name);
  for (const k of [...sel.edited]) if (!keys.includes(k)) sel.edited.delete(k);
  edited.frames.forEach((f, i) => {
    const card = el("button", "card");
    card.setAttribute("role", "option");
    card.dataset.name = f.name;
    const box = el("span", "img");
    const img = el("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = editedUrl(f.name, f.edit.png_sha256);
    box.append(img);
    card.append(box, el("span", "cap", `${f.move} · ${f.frame}`));
    card.title = `${f.name}\nedited ${f.edit.time}` + (f.edit.mean_error !== undefined ? ` · mean colour error ${f.edit.mean_error}` : "") +
      "\nDouble-click to open the move.";
    card.onclick = (e) => { pick(sel.edited, keys, i, e, "editedAnchor"); paintEditedSelection(); };
    card.ondblclick = () => jumpTo(f.category, f.move, f.name);
    card.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); jumpTo(f.category, f.move, f.name); } };
    grid.append(card);
  });
  paintEditedSelection();
  $("editedMissing").hidden = !(edited.missing && edited.missing.length);
  const lost = $("editedMissingList");
  lost.textContent = "";
  for (const stem of edited.missing || []) {
    const row = el("div", "mrow");
    const b = el("button", "tb", "Revert");
    b.onclick = () => revertNames([stem]);
    row.append(b, el("span", null, stem.split("~").slice(2).join(" / ")));
    lost.append(row);
  }
  const pals = $("editedPals");
  pals.textContent = "";
  if (!edited.palettes.length) pals.append(el("p", "muted", "No palette colours changed for this character."));
  for (const p of edited.palettes) {
    const b = el("button", "tb", `${p.name}: ${plural(p.colors, "colour")} changed`);
    b.onclick = async () => {
      const first = moveButtons.get("idle/stand") || moveButtons.values().next().value;
      if (first) await first.open();
      $("palSel").value = p.name;
      await selectPalette(p.name);
    };
    pals.append(b);
  }
}

function paintEditedSelection() {
  for (const c of $("editedGrid").children) c.setAttribute("aria-selected", String(sel.edited.has(c.dataset.name)));
  const n = sel.edited.size;
  $("edSelInfo").textContent = n ? `${n} selected` : "";
  $("edRevertBtn").hidden = !n;
  $("edRevertBtn").textContent = `Revert selected (${n})`;
}

async function jumpTo(category, move, frameName) {
  const btn = moveButtons.get(category + "/" + move);
  if (btn) await btn.open(frameName);
}

// ---------------------------------------------------------------- move tree

function buildTree(tree) {
  const root = $("tree");
  root.textContent = "";
  moveButtons.clear();
  const ordered = [...tree].sort((a, b) => (b.name === "supportchar") - (a.name === "supportchar"));
  for (const cat of ordered) {
    const d = el("details");
    const sum = el("summary", null, catLabel(cat.name));
    sum.append(el("span", "n", cat.count));
    d.append(sum);
    for (const mv of cat.moves) {
      const b = el("button", "mv");
      b.append(el("span", null, mv.name), el("span", "n", mv.count));
      b.dataset.search = (cat.name + " " + catLabel(cat.name) + " " + mv.name).toLowerCase();
      b.open = (frameName) => {
        d.open = true;
        selectTreeButton(b);
        b.scrollIntoView({ block: "nearest" });
        store.set("skin-editor-move-" + state.char, cat.name + "/" + mv.name);
        return openMove(cat.name, mv.name, frameName);
      };
      b.onclick = () => b.open();
      moveButtons.set(cat.name + "/" + mv.name, b);
      d.append(b);
    }
    root.append(d);
  }
  markTree();
  applyFilter();
}

function applyFilter() {
  const q = $("filter").value.trim().toLowerCase();
  for (const d of $("tree").children) {
    let any = false;
    for (const b of d.querySelectorAll(".mv")) {
      const hit = !q || b.dataset.search.includes(q);
      b.hidden = !hit;
      any = any || hit;
    }
    d.hidden = !any;
    if (q) d.open = any;
  }
}

// ---------------------------------------------------------------- move view

async function openMove(category, move, frameName) {
  stop();
  const gen = state.gen;
  const asked = current = { category, move };
  sel.strip.clear();
  $("empty").hidden = true;
  $("editedView").hidden = true;
  $("moveView").hidden = false;
  const title = $("moveTitle");
  title.textContent = move;
  title.append(el("small", null, `${state.char} · ${catLabel(category)}`));
  let groups;
  try {
    groups = await api(`/api/move?category=${encodeURIComponent(category)}&move=${encodeURIComponent(move)}`);
  } catch (e) {
    showMsg(e.message, "bad");
    return;
  }
  if (gen !== state.gen || current !== asked) return;
  player.groups = groups;
  const gsel = $("groupSel");
  gsel.textContent = "";
  let startGroup = -1;
  groups.forEach((g, gi) => {
    const shown = g.frames.filter((f) => !f.mask);
    const opt = el("option", null, `${g.name || "(none)"} (${shown.length})`);
    opt.value = gi;
    gsel.append(opt);
    if (frameName && g.frames.some((f) => f.name === frameName)) startGroup = gi;
  });
  if (startGroup < 0) startGroup = Math.max(0, groups.findIndex((g) => g.name.split("~").includes("frames")));
  gsel.value = startGroup;
  loadGroup(startGroup, frameName);
  loadMoveRows();
}

async function reloadMove() {
  if (!current) return;
  const name = player.frames[player.index] && player.frames[player.index].name;
  const keepSel = new Set(sel.strip);
  await openMove(current.category, current.move, name);
  for (const k of keepSel) sel.strip.add(k);
  paintStrip();
}

function loadGroup(gi, startName) {
  stop();
  player.gi = gi;
  const frames = player.groups[gi].frames.filter((f) => !f.mask && !f.error);
  player.frames = frames;
  player.index = Math.max(0, frames.findIndex((f) => f.name === startName));
  player.images = [];
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const f0 of frames) {
    const f = geo(f0);
    x0 = Math.min(x0, f.x); y0 = Math.min(y0, f.y);
    x1 = Math.max(x1, f.x + f.w * f.scale); y1 = Math.max(y1, f.y + f.h * f.scale);
  }
  player.box = frames.length ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
  view.panX = view.panY = 0;
  buildStrip();
  draw();
}

// where a frame sits on the game's screen canvas: an edit can have grown it
function geo(f) {
  const c = f.edit && f.edit.crop;
  return c ? { x: c[0], y: c[1], w: c[2], h: c[3], scale: 1 } : f;
}

// ---------------------------------------------------------------- timeline (frame strip)

function buildStrip() {
  const strip = $("strip");
  strip.textContent = "";
  const keys = player.frames.map((f) => f.name);
  player.frames.forEach((f, i) => {
    const b = el("button", "fr" + (f.edit ? " edited" : ""));
    b.setAttribute("role", "option");
    b.dataset.name = f.name;
    b.tabIndex = -1;
    const box = el("span", "img");
    const img = el("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = thumbUrl(f);
    box.append(img);
    b.append(box, el("span", "no", String(i + 1)));
    b.title = `${f.frame}${f.edit ? " · edited" : ""}\n${geo(f).w}×${geo(f).h}`;
    b.onclick = (e) => {
      pick(sel.strip, keys, i, e, "stripAnchor");
      stop();
      player.index = i;
      draw();
      paintStrip();
    };
    strip.append(b);
  });
  paintStrip();
}

function refreshStripThumbs() {
  for (const b of $("strip").children) {
    const f = player.frames.find((x) => x.name === b.dataset.name);
    const img = b.querySelector("img");
    if (f && img) { const url = thumbUrl(f); if (img.getAttribute("src") !== url) img.src = url; }
  }
}

function paintStrip() {
  const strip = $("strip");
  for (const b of strip.children) {
    const i = player.frames.findIndex((f) => f.name === b.dataset.name);
    b.setAttribute("aria-selected", String(sel.strip.has(b.dataset.name)));
    b.classList.toggle("cur", i === player.index);
    if (i === player.index) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
  }
  const chosen = player.frames.filter((f) => sel.strip.has(f.name));
  const editedChosen = chosen.filter((f) => f.edit);
  const editedAll = player.frames.filter((f) => f.edit);
  $("selInfo").textContent = chosen.length > 1 ? `${chosen.length} selected` : "";
  $("revertSelBtn").hidden = !editedChosen.length;
  $("revertSelBtn").textContent = `Revert selected (${editedChosen.length})`;
  $("revertMoveBtn").hidden = editedAll.length < 2;
  const long = document.createElement("span"), short = document.createElement("span");
  long.className = "long"; long.textContent = "Revert all in this move";
  short.className = "short"; short.textContent = "Revert move";
  $("revertMoveBtn").replaceChildren(long, short, ` (${editedAll.length})`);
  const f = player.frames[player.index];
  if (f) {
    const word = document.createElement("span");
    word.className = "long"; word.textContent = "Frame ";
    $("frameLabel").replaceChildren(word, `${player.index + 1} / ${player.frames.length}`);
  } else $("frameLabel").textContent = "";
  $("frameLabel").title = f ? f.frame : "";
  const cur = strip.querySelector(".fr.cur");
  if (cur && player.timer) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// ---------------------------------------------------------------- stage view

async function loadStages() {
  const asked = state.char;
  let data = { stages: [], home: null, anchor: null };
  try { data = await api("/api/stages"); } catch (e) { /* stages are optional */ }
  if (asked !== state.char) return;
  view.stages = data.stages;
  view.anchor = data.anchor;
  const s = $("backdropSel");
  s.textContent = "";
  for (const st of data.stages) { const o = el("option", null, st.name); o.value = st.id; s.append(o); }
  for (const [v, label] of [["checker", "Checkerboard"], ["dark", "Plain dark"]]) {
    const o = el("option", null, label); o.value = v; s.append(o);
  }
  const saved = store.get("skin-editor-backdrop-" + state.char);
  const ok = (v) => v && [...s.options].some((o) => o.value === v);
  const want = ok(saved) ? saved : (data.home || "checker");
  s.value = want;
  await setBackdrop(want);
}

async function setBackdrop(id) {
  view.backdrop = id;
  store.set("skin-editor-backdrop-" + state.char, id);
  view.stageImg = view.stageMeta = null;
  $("stageNote").textContent = "";
  if (id !== "checker" && id !== "dark") {
    $("stageNote").textContent = "Loading stage…";
    try {
      view.stageMeta = await api(`/api/stage?id=${encodeURIComponent(id)}&meta=1`);
      const img = new Image();
      await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = withChar(`/api/stage?id=${encodeURIComponent(id)}`); });
      if (view.backdrop !== id) return;
      view.stageImg = img;
      const name = (view.stages.find((s) => s.id === id) || {}).name || id;
      $("stageNote").textContent = `${name} · backgrounds only, in-game lighting not shown`;
    } catch (e) {
      $("stageNote").textContent = "";
      showMsg(`Could not load that stage: ${e.message || "image failed"}`, "bad");
      view.backdrop = "checker";
      $("backdropSel").value = "checker";
    }
  }
  draw();
}

let checker = null;
function checkerPattern(ctx) {
  if (!checker) {
    const c = document.createElement("canvas");
    c.width = c.height = 16;
    const x = c.getContext("2d");
    x.fillStyle = "#24272c"; x.fillRect(0, 0, 16, 16);
    x.fillStyle = "#2c2f35"; x.fillRect(0, 0, 8, 8); x.fillRect(8, 8, 8, 8);
    checker = c;
  }
  return ctx.createPattern(checker, "repeat");
}

function frameImage(i) {
  let img = player.images[i];
  if (!img) {
    const images = player.images;
    img = images[i] = new Image();
    img.onload = () => { if (player.images === images && player.index === i) draw(); };
    img.src = frameUrl(player.frames[i], false);
  }
  return img;
}

function loadingNote(ctx, cw, ch) {
  // edited frames are rebuilt on first view (a few seconds; up to a minute the first time per character)
  const text = "Preparing this frame…";
  ctx.font = "500 13px " + getComputedStyle(document.body).fontFamily;
  const w = ctx.measureText(text).width + 24;
  ctx.fillStyle = "rgba(23,24,27,.85)";
  ctx.fillRect((cw - w) / 2, ch / 2 - 15, w, 30);
  ctx.fillStyle = "#c9ccd1";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, cw / 2, ch / 2);
  ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
}

function draw() {
  const c = $("canvas"), area = $("stageArea");
  const dpr = window.devicePixelRatio || 1;
  const cw = area.clientWidth, ch = area.clientHeight;
  if (!cw || !ch) return;
  let resized = false;                                          // resizing a canvas wipes it
  if (c.width !== Math.round(cw * dpr) || c.height !== Math.round(ch * dpr)) {
    c.width = Math.round(cw * dpr); c.height = Math.round(ch * dpr);
    resized = true;
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const f = player.frames[player.index];
  const img = f ? frameImage(player.index) : null;
  const ready = img && img.complete && img.naturalWidth;
  if (img && !ready && !resized && c.dataset.frame === f.name) return;     // keep the old picture while loading
  c.dataset.frame = f ? f.name : "";
  ctx.clearRect(0, 0, cw, ch);
  paintStrip();
  const st = view.stageImg && view.stageMeta && view.anchor ? view.stageMeta : null;
  const box = player.box;
  // World = game pixels: the stage (character standing on it) or, without a stage, the
  // character's own sprite canvas. (cx, cy) is the world point shown at the centre of the view.
  let worldW = 0, worldH = 0, feetX = 0, feetY = 0, cx, cy, fitK;
  if (st) {
    worldW = st.width / st.scale; worldH = st.height / st.scale;
    feetX = st.center_x / st.scale; feetY = st.floor_y / st.scale;
  }
  const toWorld = (x, y) => (st ? [feetX + x - view.anchor.x, feetY + y - view.anchor.y] : [x, y]);
  if (st && (view.zoom === "stage" || !box)) {
    cx = worldW / 2; cy = worldH / 2;
    fitK = Math.min(cw / worldW, ch / worldH);
  } else if (box) {
    [cx, cy] = toWorld(box.x + box.w / 2, box.y + box.h / 2);
    fitK = st ? Math.min((cw * 0.9) / box.w, (ch * 0.88) / box.h, ZOOM_MAX)
      : Math.min((cw - 40) / box.w, (ch - 40) / box.h, ZOOM_MAX);
  } else {
    view.xf = null;
    if (f) loadingNote(ctx, cw, ch);
    return;
  }
  const k = typeof view.zoom === "number" ? view.zoom : fitK;
  cx += view.panX; cy += view.panY;
  const ox = cw / 2 - cx * k, oy = ch / 2 - cy * k;
  view.xf = { k, ox, oy };
  showZoom(k);
  if (st) {
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(view.stageImg, ox, oy, worldW * k, worldH * k);
  } else {
    ctx.fillStyle = view.backdrop === "dark" ? "#101114" : checkerPattern(ctx);
    ctx.fillRect(0, 0, cw, ch);
  }
  if (!ready) { if (f) loadingNote(ctx, cw, ch); return; }
  const g = geo(f);
  const [wx, wy] = toWorld(g.x, g.y);
  const dx = ox + wx * k, dy = oy + wy * k, dw = g.w * g.scale * k, dh = g.h * g.scale * k;
  ctx.imageSmoothingEnabled = k < 1;
  ctx.drawImage(img, dx, dy, dw, dh);
  drawRowHighlight(ctx, f, dx, dy, dw, dh);
}

// ---------------------------------------------------------------- zoom and pan
// The wheel zooms around the pointer, dragging moves the view, double-click goes back to Fit.

function showZoom(k) {
  const text = Math.round(k * 100) + "%";
  if ($("zoomVal").textContent !== text) $("zoomVal").textContent = text;
  $("zoomOut").disabled = k <= ZOOM_MIN + 1e-6;
  $("zoomIn").disabled = k >= ZOOM_MAX - 1e-6;
}

// an exact zoom, keeping the world point under screen point (sx, sy) where it is
function zoomTo(k, sx, sy) {
  const xf = view.xf, area = $("stageArea");
  const hw = area.clientWidth / 2, hh = area.clientHeight / 2;
  if (sx == null) { sx = hw; sy = hh; }
  k = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, k));
  if (xf) {
    const wx = (sx - xf.ox) / xf.k, wy = (sy - xf.oy) / xf.k;      // world point under the pointer
    // draw() puts the origin at hw - (c + pan) * k, so move pan by the change in that centre point
    view.panX += (hw - (sx - wx * k)) / k - (hw - xf.ox) / xf.k;
    view.panY += (hh - (sy - wy * k)) / k - (hh - xf.oy) / xf.k;
  }
  view.zoom = k;
  for (const b of document.querySelectorAll("[data-zoom]")) b.setAttribute("aria-pressed", "false");
  draw();
}

function zoomStep(dir) {
  const k = view.xf ? view.xf.k : 1;
  const next = dir > 0 ? ZOOM_STEPS.find((z) => z > k + 1e-3) : [...ZOOM_STEPS].reverse().find((z) => z < k - 1e-3);
  zoomTo(next == null ? (dir > 0 ? ZOOM_MAX : ZOOM_MIN) : next);
}

(function stageMouse() {
  const area = $("stageArea"), c = $("canvas");
  let drag = null;
  c.addEventListener("wheel", (e) => {
    if ($("moveView").hidden || !view.xf) return;
    e.preventDefault();
    const r = c.getBoundingClientRect();
    zoomTo(view.xf.k * Math.pow(1.0015, -e.deltaY), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  c.addEventListener("pointerdown", (e) => {
    if (picking) {                                   // the colour editor's eyedropper is waiting for a click
      e.preventDefault();
      if (e.button === 0) pickColor(stageColorAt(e));
      return;
    }
    if ((e.button !== 0 && e.button !== 1) || !view.xf) return;
    e.preventDefault();
    if (typeof view.zoom !== "number") view.zoom = view.xf.k;        // a dragged Fit view keeps its size
    for (const b of document.querySelectorAll("[data-zoom]")) b.setAttribute("aria-pressed", "false");
    drag = { x: e.clientX, y: e.clientY, panX: view.panX, panY: view.panY, k: view.xf.k };
    c.setPointerCapture(e.pointerId);
    area.classList.add("panning");
  });
  c.addEventListener("pointermove", (e) => {
    if (picking) { paintPickHint(stageColorAt(e)); return; }
    if (!drag) return;
    view.panX = drag.panX - (e.clientX - drag.x) / drag.k;
    view.panY = drag.panY - (e.clientY - drag.y) / drag.k;
    draw();
  });
  const end = () => { drag = null; area.classList.remove("panning"); };
  c.addEventListener("pointerup", end);
  c.addEventListener("pointercancel", end);
  c.addEventListener("dblclick", () => setZoom("fit"));
})();

// ---------------------------------------------------------------- "where is this colour?" highlight
// Pointing at a palette row (or one swatch) dims every pixel of the current frame that this
// row / shade does not paint, so the parts it recolours stand out in their real colours.

function rowMapUrl(f) {
  return withChar("/api/png?name=" + encodeURIComponent(f.name) + "&palette=" + encodeURIComponent(pal.slot) +
    "&rowmap=1&v=" + (f.edit ? f.edit.png_sha256.slice(0, 8) : ""));
}

function rowMap(f) {
  const url = rowMapUrl(f);
  const hit = rowmaps.get(url);
  if (hit) return hit === "loading" ? null : hit;
  if (rowmaps.size > 200) rowmaps.clear();
  rowmaps.set(url, "loading");
  const img = new Image();
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(img, 0, 0);
    rowmaps.set(url, { data: x.getImageData(0, 0, c.width, c.height) });
    if (activeRow()) draw();
  };
  img.onerror = () => rowmaps.delete(url);
  img.src = url;
  return null;
}

// Only while pointing at (or keyboard-focusing) a row or swatch, and never while the colour editor
// is open: then the stage must show true colours, for judging and for the eyedropper.
function activeRow() { return ed ? null : pal.hover; }

function drawRowHighlight(ctx, f, dx, dy, dw, dh) {
  const h = activeRow();
  if (!h || !pal.slot || f.type !== 3) { showRowNote(null); return; }
  const map = rowMap(f);
  if (!map) { showRowNote(h, null); return; }
  const w = map.data.width, hgt = map.data.height;
  const key = rowMapUrl(f) + "|" + h.row + "|" + h.col;
  if (!highlightCache || highlightCache.key !== key) {
    const src = map.data.data;
    const bit = h.col == null ? 7 : 1 << h.col;
    const c = document.createElement("canvas");
    c.width = w; c.height = hgt;
    const x = c.getContext("2d");
    const out = x.createImageData(w, hgt), o = out.data;
    let count = 0;
    for (let i = 0; i < src.length; i += 4) {
      const a = src[i + 3];
      if (!a) continue;
      if ((src[i] === h.row || src[i + 2] === h.row) && (src[i + 1] & bit)) { count++; continue; }   // the colour itself: shown as is
      o[i] = 16; o[i + 1] = 17; o[i + 2] = 20; o[i + 3] = Math.round(a * 0.85);                      // everything else darkened
    }
    x.putImageData(out, 0, 0);
    highlightCache = { key, canvas: c, count };
  }
  ctx.drawImage(highlightCache.canvas, dx, dy, dw, dh);
  showRowNote(h, highlightCache.count);
}

function showRowNote(h, count) {
  const note = $("palNote");
  if (!note) return;
  if (!h) {
    note.textContent = ed ? "The highlight is off while you edit, so the stage shows the true colours."
      : "Point at a row or swatch to see where it is on the character.";
    note.classList.remove("on");
    return;
  }
  const what = `Row ${h.row}` + (h.col == null ? "" : ` · ${SHADES[h.col]}`);
  note.classList.add("on");
  note.textContent = count == null ? `${what}: finding it…`
    : count ? `${what}: the parts not darkened (${count.toLocaleString()} px in this frame).`
    : `${what}: not in this frame. Step through the frames to find it.`;
}

function setRowHover(row, col) {
  const next = row == null ? null : { row, col: col == null ? null : col };
  const cur = pal.hover;
  if (cur === next || (cur && next && cur.row === next.row && cur.col === next.col)) return;
  pal.hover = next;
  markHoverRow();
  draw();
}

function markHoverRow() {
  const h = activeRow();
  for (const line of $("palGrid").querySelectorAll(".prow[data-row]")) {
    line.classList.toggle("lit", !!h && Number(line.dataset.row) === h.row);
  }
}

new ResizeObserver(() => draw()).observe(document.getElementById("stageArea"));

function setZoom(z) {
  view.panX = view.panY = 0;                 // Fit and Stage start centred
  view.zoom = z;
  for (const b of document.querySelectorAll("[data-zoom]")) b.setAttribute("aria-pressed", String(b.dataset.zoom === z));
  draw();
}

function play() {
  if (!player.frames.length) return;
  stop();
  $("playIcon").setAttribute("href", "#i-pause");
  $("playText").textContent = "Pause";
  $("playBtn").setAttribute("aria-label", "Pause");
  player.timer = setInterval(() => {
    player.index = (player.index + 1) % player.frames.length;
    draw();
  }, 1000 / Number($("fps").value));
}

function stop() {
  if (player.timer) clearInterval(player.timer);
  player.timer = null;
  $("playIcon").setAttribute("href", "#i-play");
  $("playText").textContent = "Play";
  $("playBtn").setAttribute("aria-label", "Play");
}

function step(d) {
  if (!player.frames.length) return;
  stop();
  player.index = (player.index + d + player.frames.length) % player.frames.length;
  sel.strip.clear();
  sel.strip.add(player.frames[player.index].name);
  sel.stripAnchor = player.index;
  draw();
  const cur = $("strip").querySelector(".fr.cur");
  if (cur) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// ---------------------------------------------------------------- palette editor

const hex = (c) => "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
const sameColor = (a, b) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
const SHADES = ["shadow", "base", "light"];

async function loadPaletteList() {
  const asked = state.char;
  const data = await api("/api/palettes");
  if (asked !== state.char) return;
  const s = $("palSel");
  const keep = pal.slot;
  s.textContent = "";
  const none = el("option", null, "Default look (no editing)");
  none.value = "";
  s.append(none);
  for (const p of data.palettes) {
    let label = p.name;
    if (p.name === data.default) label += " (default costume)";
    if (p.locked) label += ` · ${p.locked}`;
    if (p.colors) label += ` · ${p.colors} changed`;
    const opt = el("option", null, label);
    opt.value = p.name;
    s.append(opt);
  }
  s.value = keep;
}

async function selectPalette(slot) {
  pal.slot = slot;
  pal.rows = pal.original = null;
  closeSwatchEditor();
  if (slot) {
    try {
      const data = await api("/api/palette?name=" + encodeURIComponent(slot));
      pal.rows = data.rows;
      pal.original = data.original;
    } catch (e) {
      showMsg(e.message, "bad");
      pal.slot = "";
      $("palSel").value = "";
    }
  }
  pal.rev++;
  player.images = [];
  draw();
  refreshStripThumbs();
  renderPalette();
}

async function loadMoveRows() {
  pal.used = [];
  renderPalette();
  if (!current) return;
  const asked = current;
  try {
    const rows = await api(`/api/rows?category=${encodeURIComponent(asked.category)}&move=${encodeURIComponent(asked.move)}`);
    if (current === asked) { pal.used = rows; renderPalette(); }
  } catch (e) { /* the grid still works without the highlight */ }
}

function renderPalette() {
  const grid = $("palGrid");
  grid.textContent = "";
  $("palTools").hidden = !pal.rows;
  $("palHint").hidden = !!pal.rows;
  $("palNote").hidden = !pal.rows;
  paintHistoryButtons();
  if (!pal.rows) { pal.hover = null; return; }
  if (!activeRow()) showRowNote(null);
  const onlyUsed = $("palOnlyUsed").checked && pal.used.length;
  const head = el("div", "prow head");
  head.append(el("span", "num", "row"), ...SHADES.map((s) => el("span", null, s)), el("span"));
  grid.append(head);
  let prev = -1;
  pal.rows.forEach((cols, row) => {
    const used = pal.used.includes(row);
    if (onlyUsed && !used && !(ed && ed.row === row)) return;     // the row being edited always stays
    const skipped = onlyUsed && prev >= 0 && row - prev > 1;      // rows this move doesn't use are hidden here
    const line = el("div", "prow" + (used ? " used" : "") + (skipped ? " gap" : ""));
    prev = row;
    line.dataset.row = row;
    line.onmouseenter = () => setRowHover(row, null);
    line.append(el("span", "num", String(row)));
    cols.forEach((color, col) => {
      const sw = el("button", "sw");
      sw.dataset.cell = row + "." + col;
      sw.style.background = hex(color);
      sw.setAttribute("aria-label", `Row ${row} ${SHADES[col]} ${hex(color)}`);
      sw.title = `row ${row} · ${SHADES[col]} · ${hex(color)}`;
      if (!sameColor(color, pal.original[row][col])) sw.classList.add("changed");
      if (ed && ed.row === row && ed.col === col) sw.classList.add("editing");
      sw.onclick = () => (picking ? pickColor(pal.rows[row][col]) : openSwatchEditor(row, col));
      sw.onmouseenter = sw.onfocus = () => setRowHover(row, col);
      sw.onmouseleave = () => setRowHover(row, null);
      sw.onblur = () => setRowHover(null);
      line.append(sw);
    });
    const fx = el("button", "rowfx");
    fx.innerHTML = '<svg class="ico"><use href="#i-ramp"/></svg>';
    fx.title = "Set shadow and light from base (keeps this costume's shading steps)";
    fx.setAttribute("aria-label", `Row ${row}: set shadow and light from base`);
    fx.onclick = () => matchRow(row);
    line.append(fx);
    grid.append(line);
  });
  grid.onmouseleave = () => setRowHover(null);
  markHoverRow();
  if (ed && ed.dirty) paintDraftSwatches();
}

// ---------------------------------------------------------------- colour maths

const clamp01 = (v) => Math.max(0, Math.min(1, v));

function rgbToHsv([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d + 6) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
  }
  return [h * 60, max ? d / max : 0, max];
}

function hsvToRgb(h, s, v) {
  const f = (n) => {
    const k = (n + h / 60) % 6;
    return Math.round(255 * (v - v * s * Math.max(0, Math.min(k, 4 - k, 1))));
  };
  return [f(5), f(3), f(1)];
}

function rgbToHsl(c) {
  const [h, s, v] = rgbToHsv(c);
  const l = v * (1 - s / 2);
  return [h, l === 0 || l === 1 ? 0 : (v - l) / Math.min(l, 1 - l), l];
}

function hslToRgb(h, s, l) {
  const v = l + s * Math.min(l, 1 - l);
  return hsvToRgb(h, v ? 2 * (1 - l / v) : 0, v);
}

function parseHex(text) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(text).trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
}

// Shadow and light for a new base colour, with the SAME steps the original costume has on this row,
// so the character keeps its shading style. Each channel of the original shadow / light is the base
// pushed toward black (a multiply, like a shadow layer) or toward white (a screen, like a highlight);
// the same push is applied to the new base, which also carries over any tint the shading has.
// A flat original row (all three the same) gets fixed steps: 18% darker, 14% lighter.
function rampFrom(row, base) {
  const o = pal.original[row];
  if (sameColor(o[0], o[1]) && sameColor(o[2], o[1])) {
    const [h, sat, l] = rgbToHsl(base);
    return [hslToRgb(h, sat, clamp01(l - 0.18)), hslToRgb(h, sat, clamp01(l + 0.14))];
  }
  const sum = (c) => c[0] + c[1] + c[2];
  return [0, 2].map((col) => {
    const t = o[col];
    const overall = sum(o[1]) ? Math.min(1, sum(t) / sum(o[1])) : 1;     // for channels the old base had at 0
    return base.map((v, i) => {
      const was = o[1][i], now = t[i];
      if (now <= was) return Math.round(v * (was ? now / was : overall));                 // toward black
      return Math.round(255 - (255 - v) * (255 - now) / (255 - was));                     // toward white
    });
  });
}

// ---------------------------------------------------------------- colour changes, undo and redo
// Every colour change goes through changeColors(): one write to the server, one history entry
// { slot, label, cells: [{ row, col, before, after }] } per character. Undo / redo write the old /
// new values back. Writes run one after another (palQueue) so they cannot overtake each other.

const histories = new Map();     // character -> { undo: [], redo: [] }
let palQueue = Promise.resolve();
const queued = (fn) => (palQueue = palQueue.then(fn, fn));

function hist() {
  let h = histories.get(state.char);
  if (!h) { h = { undo: [], redo: [] }; histories.set(state.char, h); }
  return h;
}

function paintHistoryButtons() {
  const h = hist();
  $("palUndo").disabled = !h.undo.length;
  $("palRedo").disabled = !h.redo.length;
}

async function applyCells(slot, cells, key) {
  await api("/api/palette", { name: slot, cells: cells.map((c) => [c.row, c.col, c[key]]) });
  for (const c of cells) pal.rows[c.row][c.col] = c[key].slice();
  afterPaletteChange();
}

function afterPaletteChange() {
  pal.try = draftTry();
  pal.rev++;
  player.images = [];
  draw();
  syncEditor();
  renderPalette();
  // the stage comes first; thumbnails and counts follow once the changes pause (they are slower to
  // draw and would hold up the next change, e.g. several Ctrl+Z in a row)
  clearTimeout(afterTimer);
  afterTimer = setTimeout(() => { refreshStripThumbs(); refreshAll(); loadPaletteList(); }, 500);
}
let afterTimer = null;

// make(): the cells to change, worked out when the write actually runs. session: the colour editor
// this change belongs to (so Cancel can take it back).
function changeColors(make, label, session) {
  const char = state.char, slot = pal.slot;
  return queued(async () => {
    if (char !== state.char || slot !== pal.slot || !pal.rows) return false;
    const cells = make().filter((c) => !sameColor(c.before, c.after));
    if (!cells.length) {
      if (pal.try && !(ed && ed.dirty)) { pal.try = ""; player.images = []; draw(); renderPalette(); }
      return true;
    }
    try {
      await applyCells(slot, cells, "after");
    } catch (e) {
      showMsg(e.message, "bad");
      return false;
    }
    const h = hist();
    h.undo.push({ slot, cells, label });
    if (h.undo.length > 200) h.undo.shift();
    h.redo.length = 0;
    if (session && session.made != null) session.made++;
    paintHistoryButtons();
    return true;
  });
}

function undoRedo(redo) {
  return queued(async () => {
    const h = hist(), from = redo ? h.redo : h.undo, to = redo ? h.undo : h.redo;
    const entry = from[from.length - 1];
    if (!entry) { showMsg(redo ? "No colour change to redo." : "No colour change to undo."); return; }
    if (ed) { ed.dirty = false; ed.made = null; clearTimeout(previewTimer); previewTimer = null; }
    if (entry.slot !== pal.slot) {                       // the change was made in another costume slot
      $("palSel").value = entry.slot;
      await selectPalette(entry.slot);
      if (pal.slot !== entry.slot) return;
    }
    try {
      await applyCells(entry.slot, entry.cells, redo ? "after" : "before");
    } catch (e) {
      showMsg(e.message, "bad");
      return;
    }
    from.pop();
    to.push(entry);
    paintHistoryButtons();
    showMsg(`${redo ? "Redid" : "Undid"} ${entry.label}.`);
  });
}

function matchRow(row) {
  commitDraft();
  changeColors(() => {
    const ramp = rampFrom(row, pal.rows[row][1]);
    return [0, 2].map((col, i) => ({ row, col, before: pal.rows[row][col].slice(), after: ramp[i] }));
  }, `row ${row} shadow and light from base`, ed && ed.row === row ? ed : null)
    .then((ok) => { if (ok) showMsg(`Row ${row}: shadow and light set from the base colour. Ctrl+Z undoes it.`); });
}

async function resetPalette() {
  if (!pal.slot || !pal.rows) return;
  const cells = [];
  pal.rows.forEach((cols, row) => cols.forEach((c, col) => {
    const o = pal.original[row][col];
    if (!sameColor(c, o)) cells.push({ row, col, before: c.slice(), after: o.slice() });
  }));
  if (!cells.length) { showMsg("This slot already has its original colours."); return; }
  if (!await ask(`Put ${cells.length === 1 ? "the changed colour" : `all ${cells.length} changed colours`} of ${pal.slot} back to the original?\n\nCtrl+Z brings them back.`, "Reset slot")) return;
  closeSwatchEditor();
  changeColors(() => cells, `reset of ${pal.slot}`);
}

// ---------------------------------------------------------------- colour editor (docked in the palette panel)
// ed = the swatch being edited. Dragging / typing changes a draft that is previewed on the swatch
// at once and on the stage shortly after (the server draws the frame with "try" colours, nothing is
// saved). The draft is written on release, Enter, leaving a control, or Done: one undo entry each.
// Cancel / Esc takes back everything since the editor opened.

let ed = null;           // { row, col, start, rgb, h, s, v, dirty, made }
let previewTimer = null;
const keepMatched = () => $("swKeep").checked;

function cellsFor(row, col, rgb, keep) {
  const cells = [{ row, col, before: pal.rows[row][col].slice(), after: rgb.slice() }];
  if (keep && col === 1) {
    rampFrom(row, rgb).forEach((c, i) => cells.push({ row, col: i * 2, before: pal.rows[row][i * 2].slice(), after: c }));
  }
  return cells;
}

function draftTry() {
  if (!ed || !ed.dirty) return "";
  return cellsFor(ed.row, ed.col, ed.rgb, keepMatched()).filter((c) => !sameColor(c.before, c.after))
    .map((c) => `${c.row}.${c.col}.${hex(c.after).slice(1)}`).join(",");
}

function paintDraftSwatches() {
  if (!ed) return;
  for (const c of cellsFor(ed.row, ed.col, ed.rgb, keepMatched())) {
    const sw = $("palGrid").querySelector(`.sw[data-cell="${c.row}.${c.col}"]`);
    if (sw) sw.style.background = hex(c.after);
  }
}

function showPreview() {
  const t = draftTry();
  if (t === pal.try) return;
  pal.try = t;
  player.images = [];
  draw();
}

function schedulePreview() {
  paintDraftSwatches();
  if (!previewTimer) previewTimer = setTimeout(() => { previewTimer = null; showPreview(); }, 150);
}

function setEditorColor(rgb) {
  ed.rgb = rgb.slice();
  const [h, s, v] = rgbToHsv(rgb);
  if (s > 0 && v > 0) ed.h = h;          // greys keep the hue the slider had
  ed.s = s; ed.v = v;
}

function setDraft(rgb, hsv) {
  if (!ed) return;
  if (hsv) { [ed.h, ed.s, ed.v] = hsv; ed.rgb = hsvToRgb(...hsv); } else setEditorColor(rgb);
  ed.dirty = true;
  paintEditor();
  schedulePreview();
}

function commitDraft() {
  if (!ed || !ed.dirty) return;
  clearTimeout(previewTimer); previewTimer = null;
  showPreview();
  ed.dirty = false;
  const e = ed, rgb = ed.rgb.slice(), keep = keepMatched();
  changeColors(() => cellsFor(e.row, e.col, rgb, keep),
    `row ${e.row} ${SHADES[e.col]}` + (keep && e.col === 1 ? " with shadow and light" : ""), e);
}

function paintEditor() {
  if (!ed) return;
  $("swTitle").textContent = `Row ${ed.row} · ${SHADES[ed.col]}`;
  $("swWas").style.background = hex(ed.start);
  $("swNow").style.background = hex(ed.rgb);
  const sv = $("swSV"), hue = $("swHue");
  sv.style.setProperty("--hue", hex(hsvToRgb(ed.h, 1, 1)));
  sv.firstElementChild.style.left = ed.s * 100 + "%";
  sv.firstElementChild.style.top = (1 - ed.v) * 100 + "%";
  sv.firstElementChild.style.background = hex(ed.rgb);
  sv.setAttribute("aria-valuetext", `saturation ${Math.round(ed.s * 100)}%, brightness ${Math.round(ed.v * 100)}%`);
  hue.firstElementChild.style.left = (ed.h / 360) * 100 + "%";
  hue.firstElementChild.style.background = hex(hsvToRgb(ed.h, 1, 1));
  hue.setAttribute("aria-valuenow", String(Math.round(ed.h)));
  const fields = [["swHex", hex(ed.rgb)], ["swR", ed.rgb[0]], ["swG", ed.rgb[1]], ["swB", ed.rgb[2]]];
  for (const [id, value] of fields) if (document.activeElement !== $(id)) $(id).value = value;
  $("swReset").disabled = sameColor(ed.rgb, pal.original[ed.row][ed.col]);
  $("swMatch").hidden = ed.col !== 1;
}

// after any colour change: the editor follows what is saved (unless a new draft is being dragged)
function syncEditor() {
  if (!ed) return;
  if (!pal.rows) { closeSwatchEditor(); return; }
  if (!ed.dirty && !sameColor(ed.rgb, pal.rows[ed.row][ed.col])) setEditorColor(pal.rows[ed.row][ed.col]);
  paintEditor();
}

function revealEditedSwatch() {
  const panel = $("palPanel"), sw = $("palGrid").querySelector(".sw.editing");
  if (!sw) return;
  const e = $("swEdit").getBoundingClientRect(), r = sw.getBoundingClientRect(), p = panel.getBoundingClientRect();
  if (r.top < e.bottom + 8) panel.scrollTop -= e.bottom + 8 - r.top;
  else if (r.bottom > p.bottom - 8) panel.scrollTop += r.bottom - p.bottom + 8;
}

function openSwatchEditor(row, col) {
  if (ed && ed.row === row && ed.col === col) { $("swSV").focus(); return; }
  commitDraft();
  closePopovers();
  const panel = $("palPanel"), wasOpen = !!ed, scrolled = panel.scrollTop;
  const color = pal.rows[row][col].slice();
  ed = { row, col, start: color, rgb: color.slice(), h: 0, s: 0, v: 0, dirty: false, made: 0 };
  [ed.h, ed.s, ed.v] = rgbToHsv(color);
  $("swKeep").checked = store.get("skin-editor-keep-matched") === "1";
  $("swEdit").hidden = false;
  paintEditor();
  renderPalette();
  if (!wasOpen && scrolled > 0) panel.scrollTop = scrolled + $("swEdit").offsetHeight + 8;   // rows stay where they were
  revealEditedSwatch();
  draw();
  $("swSV").focus({ preventScroll: true });
}

// keepPreview: a write for the last draft is on its way, so leave the stage as it is until it lands
function closeSwatchEditor(keepPreview) {
  $("swEdit").hidden = true;
  setPicking(false);
  if (!ed) return;
  clearTimeout(previewTimer); previewTimer = null;
  const cell = ed.row + "." + ed.col, hadFocus = $("swEdit").contains(document.activeElement);
  ed = null;
  if (pal.try && !keepPreview) { pal.try = ""; player.images = []; }
  renderPalette();
  draw();
  const sw = hadFocus && $("palGrid").querySelector(`.sw[data-cell="${cell}"]`);
  if (sw) sw.focus({ preventScroll: true });
}

function cancelSwatchEditor() {
  if (!ed) return;
  const e = ed, char = state.char, slot = pal.slot;
  e.dirty = false;
  closeSwatchEditor();
  queued(async () => {
    if (char !== state.char || slot !== pal.slot || !pal.rows || !e.made) return;    // made = null: undo was used meanwhile
    const h = hist();
    const mine = h.undo.splice(Math.max(0, h.undo.length - e.made), e.made);
    const back = new Map();                              // the oldest "before" of every cell wins
    for (const entry of mine.reverse()) for (const c of entry.cells) back.set(c.row + "." + c.col, c);
    const cells = [...back.values()].map((c) => ({ row: c.row, col: c.col, after: c.before }));
    try {
      await applyCells(slot, cells, "after");
      showMsg(`Row ${e.row} ${SHADES[e.col]} is back to the colour it had.`);
    } catch (err) {
      showMsg(err.message, "bad");
    }
    paintHistoryButtons();
  });
}

function dragControl(node, onMove) {
  const move = (e) => {
    if (!ed) return;
    const r = node.getBoundingClientRect();
    onMove(clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height));
  };
  node.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !ed) return;
    e.preventDefault();
    node.focus({ preventScroll: true });
    node.setPointerCapture(e.pointerId);
    node.onpointermove = move;
    move(e);
  });
  const end = () => { if (node.onpointermove) { node.onpointermove = null; commitDraft(); } };   // one undo entry per drag
  node.addEventListener("pointerup", end);
  node.addEventListener("pointercancel", end);
  node.addEventListener("blur", () => commitDraft());                                           // after keyboard nudges
}

dragControl($("swSV"), (x, y) => setDraft(null, [ed.h, x, 1 - y]));
dragControl($("swHue"), (x) => setDraft(null, [Math.min(359, x * 360), ed.s, ed.v]));

$("swSV").onkeydown = (e) => {
  if (!ed) return;
  const step = e.shiftKey ? 0.1 : 0.01;
  const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
  if (d) { e.preventDefault(); e.stopPropagation(); setDraft(null, [ed.h, clamp01(ed.s + d[0]), clamp01(ed.v + d[1])]); }
  else if (e.key === "Enter") { e.preventDefault(); commitDraft(); }
};
$("swHue").onkeydown = (e) => {
  if (!ed) return;
  const step = e.shiftKey ? 10 : 1;
  const d = { ArrowLeft: -step, ArrowDown: -step, ArrowRight: step, ArrowUp: step }[e.key];
  if (d) { e.preventDefault(); e.stopPropagation(); setDraft(null, [Math.max(0, Math.min(359, ed.h + d)), ed.s, ed.v]); }
  else if (e.key === "Enter") { e.preventDefault(); commitDraft(); }
};

$("swHex").oninput = () => { const c = parseHex($("swHex").value); if (c) setDraft(c); };
$("swHex").onchange = () => {
  if (!ed) return;
  if (parseHex($("swHex").value)) commitDraft(); else showMsg("Type a colour like #e0a040.", "bad");
};
$("swHex").onblur = paintEditor;
for (const id of ["swR", "swG", "swB"]) {
  $(id).oninput = () => {
    const c = ["swR", "swG", "swB"].map((k) => (/^\d{1,3}$/.test($(k).value.trim()) ? Number($(k).value) : -1));
    if (c.every((v) => v >= 0 && v <= 255)) setDraft(c);
  };
  $(id).onchange = () => commitDraft();
  $(id).onblur = paintEditor;
}
$("swApply").onclick = () => { commitDraft(); closeSwatchEditor(true); };
$("swCancel").onclick = cancelSwatchEditor;
$("swReset").onclick = () => { if (ed) { setDraft(pal.original[ed.row][ed.col]); commitDraft(); } };
$("swRamp").onclick = () => { if (ed) matchRow(ed.row); };
$("swKeep").onchange = () => {
  store.set("skin-editor-keep-matched", keepMatched() ? "1" : "0");
  renderPalette();
  if (ed && ed.dirty) schedulePreview();
};
// The eyedropper is our own: the browser's EyeDropper does nothing inside the Windows app's window.
// While it is on, a click on the stage (or on any swatch) takes that colour for the swatch being edited.
let picking = false;

function setPicking(on) {
  picking = !!on && !!ed;
  $("swPick").setAttribute("aria-pressed", String(picking));
  $("stageArea").classList.toggle("picking", picking);
  $("palGrid").classList.toggle("picking", picking);
  $("pickHint").hidden = !picking;
  if (picking) paintPickHint(null);
}

function stageColorAt(e) {
  const c = $("canvas"), r = c.getBoundingClientRect();
  const x = Math.floor((e.clientX - r.left) * c.width / r.width), y = Math.floor((e.clientY - r.top) * c.height / r.height);
  if (x < 0 || y < 0 || x >= c.width || y >= c.height) return null;
  try {
    const d = c.getContext("2d").getImageData(x, y, 1, 1).data;
    return d[3] ? [d[0], d[1], d[2]] : null;
  } catch (err) {
    return null;
  }
}

function paintPickHint(rgb) {
  $("pickChip").style.background = rgb ? hex(rgb) : "transparent";
  $("pickText").textContent = rgb ? `${hex(rgb)} · click to use it`
    : "Click the character, the stage or a swatch to take its colour · Esc cancels";
}

function pickColor(rgb) {
  setPicking(false);
  if (ed && rgb) { setDraft(rgb); commitDraft(); }
}

$("swPick").onclick = () => setPicking(!picking);

// ---------------------------------------------------------------- keyboard

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (document.querySelector("dialog[open]")) return;               // the dialog closes itself
    const anyOpen = !$("charPop").hidden || !$("gameMenu").hidden || !$("exportPop").hidden;
    if (anyOpen) { closePopovers(); return; }
    if (picking) { setPicking(false); return; }
    if (ed) { cancelSwatchEditor(); return; }
    if (!$("msg").hidden) { showMsg(""); return; }
    if (sel.strip.size || sel.edited.size) { sel.strip.clear(); sel.edited.clear(); paintStrip(); paintEditedSelection(); }
    return;
  }
  const key = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (key === "z" || key === "y")) {
    if (inTextField(e) || document.querySelector("dialog[open]")) return;    // a text box keeps its own undo
    e.preventDefault();
    undoRedo(key === "y" || e.shiftKey);
    return;
  }
  if (typing(e) || document.querySelector("dialog[open]")) return;
  const inMove = !$("moveView").hidden;
  const ownSpace = e.target.tagName !== "BUTTON" || e.target.closest(".mv, .fr");   // moves and frames don't use Space
  const plain = !e.ctrlKey && !e.metaKey && !e.altKey;
  if (inMove && e.key === " " && ownSpace) { e.preventDefault(); player.timer ? stop() : play(); }
  else if (inMove && plain && (e.key === "=" || e.key === "+")) { e.preventDefault(); zoomStep(1); }
  else if (inMove && plain && e.key === "-") { e.preventDefault(); zoomStep(-1); }
  else if (inMove && plain && e.key === "0") { e.preventDefault(); setZoom("fit"); }
  else if (inMove && e.key === "ArrowRight") { e.preventDefault(); step(1); }
  else if (inMove && e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    if (inMove) { for (const f of player.frames) sel.strip.add(f.name); paintStrip(); }
    else if (!$("editedView").hidden) { for (const f of edited.frames) sel.edited.add(f.name); paintEditedSelection(); }
  }
});

// a button activates on Space *release*; on moves and frames Space means play, so stop that click
document.addEventListener("keyup", (e) => {
  if (e.key === " " && e.target.closest && e.target.closest(".mv, .fr") && !$("moveView").hidden) e.preventDefault();
});

// ---------------------------------------------------------------- start

async function start() {
  const data = await loadCharacters();
  const ready = data.game_dir_ok && data.characters.length > 0;
  $("setup").hidden = ready;
  for (const id of ["charPick", "grpProject", "grpShare", "grpGame"]) $(id).hidden = !ready;
  if (!ready) {
    $("browser").hidden = true;
    $("gameDir").value = data.game_dir;
    return;
  }
  await loadSettings();
  updateNoticeMenu();
  let want = store.get("skin-editor-char") || data.current;
  if (!data.characters.some((c) => c.name === want)) want = data.characters[0].name;
  await selectChar(want);
}

$("setupBtn").onclick = saveGameDir;
$("charBtn").onclick = () => { if (togglePopover("charPop", "charBtn")) { $("charSearch").value = ""; renderCharList(); $("charSearch").focus(); } };
$("charSearch").oninput = renderCharList;
$("charSearch").onkeydown = (e) => {
  if (e.key === "Enter") { const first = $("charList").querySelector(".charitem"); if (first) first.click(); }
  if (e.key === "ArrowDown") { e.preventDefault(); const first = $("charList").querySelector(".charitem"); if (first) first.focus(); }
};
$("gameMenuBtn").onclick = () => togglePopover("gameMenu", "gameMenuBtn");
$("exportOptsBtn").onclick = () => togglePopover("exportPop", "exportOptsBtn");
$("shareBtn").onclick = openShare;
$("shareGo").onclick = createPack;
$("shareClose").onclick = () => $("shareDlg").close();
$("importBtn").onclick = () => $("importInput").click();
$("importInput").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importFile(f); };
$("importGo").onclick = applyImport;
$("importClose").onclick = () => $("importDlg").close();
$("scanBtn").onclick = openScan;
$("scanClose").onclick = () => $("scanDlg").close();
$("rebuildBtn").onclick = rebuildCheck;
$("noticeBtn").onclick = showNoticeAgain;
$("installBtn").onclick = installMod;
$("restoreBtn").onclick = restoreOriginal;
$("exportBtn").onclick = exportClicked;
$("exportHelpBtn").onclick = openExportDlg;
$("expGo").onclick = exportFromDlg;
$("expCancel").onclick = () => $("exportDlg").close();
$("expChoose").onclick = chooseExportFolder;
$("expDefault").onclick = () => setExportFolder("");
$("expFolderUse").onclick = () => setExportFolder($("expFolderText").value);
$("expFolderText").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); setExportFolder($("expFolderText").value); } };
$("palUndo").onclick = () => undoRedo(false);
$("palRedo").onclick = () => undoRedo(true);
$("editedBtn").onclick = showEdited;
$("editedNav").onclick = showEdited;
$("folderBtn").onclick = () => openFolder("edits");
for (const b of document.querySelectorAll("[data-folder]")) b.onclick = () => openFolder(b.dataset.folder);
for (const b of document.querySelectorAll("[data-zoom]")) b.onclick = () => setZoom(b.dataset.zoom);
$("zoomIn").onclick = () => zoomStep(1);
$("zoomOut").onclick = () => zoomStep(-1);
$("zoomVal").onclick = () => zoomTo(1);
$("backdropSel").onchange = () => setBackdrop($("backdropSel").value);
$("palSel").onchange = () => selectPalette($("palSel").value);
$("palOnlyUsed").onchange = renderPalette;
$("palReset").onclick = resetPalette;
$("filter").oninput = applyFilter;
$("playBtn").onclick = () => (player.timer ? stop() : play());
$("groupSel").onchange = () => { sel.strip.clear(); loadGroup(Number($("groupSel").value)); };
$("fps").oninput = () => { $("fpsVal").textContent = $("fps").value; if (player.timer) play(); };
$("revertSelBtn").onclick = () => revertNames(player.frames.filter((f) => f.edit && sel.strip.has(f.name)).map((f) => f.name));
$("revertMoveBtn").onclick = () => {
  const names = player.frames.filter((f) => f.edit).map((f) => f.name);
  revertNames(names, `all ${plural(names.length, "edited frame")} in this part of the move`);
};
$("edSelectAll").onclick = () => { for (const f of edited.frames) sel.edited.add(f.name); paintEditedSelection(); };
$("edRevertBtn").onclick = () => revertNames([...sel.edited]);
start().catch((e) => showMsg(e.message, "bad"));
