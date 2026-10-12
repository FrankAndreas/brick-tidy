"use strict";

const app = document.getElementById("app");
let colorsCache = null;

// ---------- helpers ----------

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(path, options = {}) {
  const init = { ...options };
  if (init.json !== undefined) {
    init.body = JSON.stringify(init.json);
    init.headers = { "Content-Type": "application/json" };
    delete init.json;
  }
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3500);
}

async function colors() {
  colorsCache ??= await api("/colors");
  return colorsCache;
}

const swatch = (rgb) => (rgb ? `<span class="swatch" style="background:#${esc(rgb)}"></span>` : "");
const partImage = (url) => (url ? `<img src="${esc(url)}" alt="" loading="lazy">` : `<div class="noimg"></div>`);

function partRow(item, right) {
  return `
    <div class="part">
      ${partImage(item.img_url)}
      <div class="info">
        <div class="name">${esc(item.part_name)}</div>
        <div class="muted">${swatch(item.color_rgb)}${esc(item.color_name)} · ${esc(item.part_num)}</div>
      </div>
      ${right}
    </div>`;
}

// ---------- routing ----------

const routes = [
  [/^#?\/?$/, renderHome],
  [/^#\/project\/(\d+)(?:\/(capture|review|missing|inventory))?$/, renderProject],
];

async function router() {
  const hash = location.hash || "#/";
  for (const [pattern, view] of routes) {
    const match = hash.match(pattern);
    if (match) {
      try {
        await view(...match.slice(1));
      } catch (error) {
        app.innerHTML = `<div class="card">Error: ${esc(error.message)}</div>`;
      }
      return;
    }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", router);
router();

// ---------- home: projects ----------

async function renderHome() {
  const [status, projects] = await Promise.all([api("/status"), api("/projects")]);
  app.innerHTML = `
    <h1>Projects</h1>
    <p class="muted">One project per set you are restoring.</p>
    ${status.catalog_loaded ? "" : `
      <div class="card"><strong>Rebrickable catalog not imported.</strong><br>
      Run <code>python scripts/import_rebrickable.py --download</code> on the server.</div>`}
    <form id="new-project" class="card row" autocomplete="off">
      <input type="text" name="set_num" placeholder="Set number or name, e.g. 10696" list="set-options" required>
      <datalist id="set-options"></datalist>
      <button class="primary" type="submit">Start project</button>
    </form>
    ${projects.length ? projects.map(projectCard).join("") : `<div class="empty">No projects yet.</div>`}`;

  const form = document.getElementById("new-project");
  const input = form.elements.set_num;
  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) return;
    const sets = await api(`/sets?q=${encodeURIComponent(q)}`);
    document.getElementById("set-options").innerHTML = sets
      .map((s) => `<option value="${esc(s.set_num)}">${esc(s.name)} (${esc(s.year)})</option>`).join("");
  }, 250));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const project = await api("/projects", { method: "POST", json: { set_num: input.value } });
      location.hash = `#/project/${project.id}/capture`;
    } catch (error) {
      toast(error.message);
    }
  });
}

function projectCard(p) {
  const pct = p.parts_needed ? Math.round((p.parts_present / p.parts_needed) * 100) : 0;
  return `
    <a class="card" href="#/project/${p.id}/missing">
      <div class="part">
        ${partImage(p.set?.img_url)}
        <div class="info">
          <div class="name">${esc(p.name)}</div>
          <div class="muted">${esc(p.set_num)} · ${p.parts_missing} of ${p.parts_needed} missing
            ${p.pending_reviews ? ` · <span class="badge">${p.pending_reviews}</span> to review` : ""}</div>
          <div class="progress"><div style="width:${pct}%"></div></div>
        </div>
      </div>
    </a>`;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// ---------- project ----------

async function renderProject(id, tab = "missing") {
  const project = await api(`/projects/${id}`);
  const pct = project.parts_needed ? Math.round((project.parts_present / project.parts_needed) * 100) : 0;
  const tabs = [
    ["capture", "Capture"],
    ["review", `Review ${project.pending_reviews ? `<span class="badge">${project.pending_reviews}</span>` : ""}`],
    ["missing", `Missing (${project.parts_missing})`],
    ["inventory", "Inventory"],
  ];
  app.innerHTML = `
    <h1>${esc(project.name)}</h1>
    <div class="muted">${esc(project.set_num)} · ${project.parts_present}/${project.parts_needed} parts present</div>
    <div class="progress"><div style="width:${pct}%"></div></div>
    <nav class="tabs">${tabs.map(([key, label]) =>
      `<a href="#/project/${id}/${key}" class="${key === tab ? "active" : ""}">${label}</a>`).join("")}</nav>
    <section id="tab"></section>`;
  const section = document.getElementById("tab");
  await { capture: renderCapture, review: renderReview, missing: renderMissing, inventory: renderInventory }[tab](
    section, project);
}

// Capture: the native camera via a file input works over plain HTTP.
function renderCapture(section, project) {
  section.innerHTML = `
    <div class="capture">
      <label class="button primary">📷 Partial build<input type="file" accept="image/*" capture="environment" data-kind="build"></label>
      <label class="button primary">📷 Loose bricks<input type="file" accept="image/*" capture="environment" data-kind="pile"></label>
      <label class="button">🖼 Build from gallery<input type="file" accept="image/*" multiple data-kind="build"></label>
      <label class="button">🖼 Bricks from gallery<input type="file" accept="image/*" multiple data-kind="pile"></label>
    </div>
    <p class="muted">Spread a <strong>small batch of bricks</strong> on a plain surface so they do not
      touch, and photograph them from above. Each brick is cropped, recognized and then appears under
      <em>Review</em>. A close-up of a single brick works too.</p>
    <p id="upload-status" class="muted"></p>`;

  section.querySelectorAll("input[type=file]").forEach((input) => {
    input.addEventListener("change", async () => {
      const files = [...input.files];
      const status = document.getElementById("upload-status");
      section.querySelectorAll("input").forEach((i) => { i.disabled = true; });
      let failed = 0;
      for (const [index, file] of files.entries()) {
        status.textContent = `Finding and recognizing bricks in photo ${index + 1} of ${files.length}…`;
        const body = new FormData();
        body.append("file", file);
        body.append("kind", input.dataset.kind);
        try {
          await api(`/projects/${project.id}/photos`, { method: "POST", body });
        } catch (error) {
          failed += 1;
          toast(`Upload failed: ${error.message}`);
        }
      }
      if (failed < files.length) location.hash = `#/project/${project.id}/review`;
      else router();
    });
  });
}

// Review: confirm or correct the recognizer's suggestion for each crop.
async function renderReview(section, project) {
  const [detections, inventory, allColors] = await Promise.all([
    api(`/projects/${project.id}/detections?status=pending`),
    api(`/projects/${project.id}/inventory`),
    colors(),
  ]);
  if (!detections.length) {
    section.innerHTML = `<div class="empty">Nothing to review. <a href="#/project/${project.id}/capture">Take a photo</a>.</div>`;
    return;
  }
  section.innerHTML = detections.map((d) => reviewCard(d, inventory)).join("");

  section.querySelectorAll("form.review").forEach((form) => {
    const detection = detections.find((d) => String(d.id) === form.dataset.id);
    const colorSelect = form.elements.color_id;
    const otherSelect = form.elements.other_part;

    // Option values are "part_num|color_id": a candidate may match the set under a
    // different (equivalent) part number, and that set part is what gets recorded.
    const fillColors = () => {
      const choice = form.elements.part.value;
      const candidate = detection.candidates.find((c) => c.part_num === choice);
      const partNum = choice === "__other" ? otherSelect.value : choice;
      const setLines = candidate ? candidate.set_colors : inventory.filter((i) => i.part_num === partNum);
      const setIds = new Set(setLines.map((c) => c.color_id));
      const suggested = detection.suggestion && candidate?.set_colors.some((c) =>
        c.part_num === detection.suggestion.part_num && c.color_id === detection.suggestion.color_id)
        ? `${detection.suggestion.part_num}|${detection.suggestion.color_id}` : null;
      colorSelect.innerHTML = `
        ${setLines.length ? `<optgroup label="In this set">${setLines.map((c) => {
          const value = `${c.part_num}|${c.color_id}`;
          const label = `${c.color_name}${c.part_num !== partNum ? ` (as ${c.part_num})` : ""} – ${c.missing} missing`;
          return `<option value="${esc(value)}" ${value === suggested ? "selected" : ""}>${esc(label)}</option>`;
        }).join("")}</optgroup>` : ""}
        <optgroup label="All colors">${allColors.filter((c) => !setIds.has(c.id)).map((c) =>
          `<option value="${esc(`${partNum}|${c.id}`)}">${esc(c.name)}</option>`).join("")}</optgroup>`;
      otherSelect.hidden = choice !== "__other";
    };
    form.querySelectorAll("input[name=part]").forEach((r) => r.addEventListener("change", fillColors));
    otherSelect.addEventListener("change", fillColors);
    fillColors();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const [partNum, colorId] = colorSelect.value.split("|");
      try {
        await api(`/detections/${detection.id}/confirm`, {
          method: "POST",
          json: { part_num: partNum, color_id: Number(colorId), quantity: Number(form.elements.quantity.value) },
        });
        router();
      } catch (error) {
        toast(error.message);
      }
    });
    form.querySelector("[data-action=reject]").addEventListener("click", async () => {
      await api(`/detections/${detection.id}/reject`, { method: "POST" });
      router();
    });
  });
}

function reviewCard(d, inventory) {
  // The suggestion may name an equivalent set part; preselect the candidate it came from.
  const selected = (d.candidates.find((c) => c.in_set) ?? d.candidates[0])?.part_num ?? "__other";
  const uniqueParts = [...new Map(inventory.map((i) => [i.part_num, i])).values()]
    .sort((a, b) => a.part_name.localeCompare(b.part_name));
  return `
    <form class="card review" data-id="${d.id}">
      <img class="crop" src="${esc(d.crop_url)}" alt="Photo to review">
      <div class="muted">${d.kind === "build" ? "Part of the build" : "Loose brick"}
        ${d.error ? ` · <span class="tag warn">recognition failed</span>`
          : d.low_confidence ? ` · <span class="tag warn">low confidence – please check</span>` : ""}</div>
      <ul class="candidates">
        ${d.candidates.map((c) => `
          <li><label>
            <input type="radio" name="part" value="${esc(c.part_num)}" ${c.part_num === selected ? "checked" : ""}>
            ${partImage(c.img_url)}
            <span class="info">
              <span class="name">${esc(c.name)}</span><br>
              <span class="muted">${esc(c.part_num)} · ${Math.round(c.score * 100)}%</span>
              ${c.in_set ? `<span class="tag ok">in set</span>` : `<span class="tag warn">not in set</span>`}
            </span>
          </label></li>`).join("")}
        <li><label>
          <input type="radio" name="part" value="__other" ${selected === "__other" ? "checked" : ""}>
          <span>Other part from this set</span>
        </label>
          <select name="other_part" hidden>${uniqueParts.map((p) =>
            `<option value="${esc(p.part_num)}">${esc(p.part_name)} (${esc(p.part_num)})</option>`).join("")}</select>
        </li>
      </ul>
      <div class="row">
        <select name="color_id" aria-label="Color"></select>
        <input type="number" name="quantity" value="1" min="1" aria-label="Quantity">
      </div>
      <div class="row" style="margin-top:10px">
        <button class="primary" type="submit">Confirm</button>
        <button type="button" data-action="reject">Not a brick / skip</button>
      </div>
    </form>`;
}

async function renderMissing(section, project) {
  const missing = await api(`/projects/${project.id}/missing`);
  if (!missing.length) {
    section.innerHTML = `<div class="empty">🎉 Nothing missing. The set is complete.</div>`;
    return;
  }
  section.innerHTML = `
    <div class="row" style="justify-content:flex-end;margin-bottom:12px">
      <button class="small" id="export">Export CSV</button>
    </div>
    ${missing.map((m, index) => `
      <div class="card">${partRow(m, `
        <span class="count">${m.missing}×</span>
        <button class="small" data-index="${index}">Found 1</button>`)}
      </div>`).join("")}`;

  section.querySelectorAll("button[data-index]").forEach((button) => {
    button.addEventListener("click", async () => {
      const m = missing[Number(button.dataset.index)];
      await api(`/projects/${project.id}/parts`, {
        method: "POST", json: { part_num: m.part_num, color_id: m.color_id, quantity: 1, source: "found" },
      });
      router();
    });
  });
  document.getElementById("export").addEventListener("click", () => {
    const lines = ["part_num,color_id,part_name,color_name,quantity",
      ...missing.map((m) => [m.part_num, m.color_id, m.part_name, m.color_name, m.missing]
        .map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","))];
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    link.download = `${project.set_num}-missing.csv`;
    link.click();
  });
}

// Inventory: list parts by hand, and undo recorded parts.
async function renderInventory(section, project) {
  const [inventory, recorded] = await Promise.all([
    api(`/projects/${project.id}/inventory`),
    api(`/projects/${project.id}/parts`),
  ]);
  const byKey = new Map(inventory.map((i) => [`${i.part_num}|${i.color_id}`, i]));
  section.innerHTML = `
    <p class="muted">Tap <strong>+1</strong> for each part you can see in the build.</p>
    ${inventory.map((item, index) => `
      <div class="card">${partRow(item, `
        <span class="count">${item.present}/${item.needed}</span>
        <button class="small" data-index="${index}" ${item.missing ? "" : "disabled"}>+1</button>`)}
      </div>`).join("")}
    <h2>Recorded parts</h2>
    ${recorded.length ? recorded.map((r) => {
      const item = byKey.get(`${r.part_num}|${r.color_id}`) ?? { part_num: r.part_num, part_name: r.part_num, color_name: `color ${r.color_id}` };
      return `<div class="card">${partRow(item, `
        <span class="muted">${r.quantity}× ${r.source}</span>
        <button class="small" data-undo="${r.id}">Undo</button>`)}</div>`;
    }).join("") : `<div class="empty">None yet.</div>`}`;

  section.querySelectorAll("button[data-index]").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = inventory[Number(button.dataset.index)];
      await api(`/projects/${project.id}/parts`, {
        method: "POST", json: { part_num: item.part_num, color_id: item.color_id, quantity: 1, source: "build" },
      });
      router();
    });
  });
  section.querySelectorAll("button[data-undo]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/projects/${project.id}/parts/${button.dataset.undo}`, { method: "DELETE" });
      router();
    });
  });
}
