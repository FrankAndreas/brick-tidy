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
  [/^#\/project\/(\d+)(?:\/(capture|review|find|missing|inventory))?$/, renderProject],
];

async function router() {
  const hash = location.hash || "#/";
  window.scrollTo(0, 0);
  for (const [pattern, view] of routes) {
    const match = hash.match(pattern);
    if (match) {
      try {
        await view(...match.slice(1));
      } catch (error) {
        // fetch() rejects with a TypeError when the server cannot be reached.
        app.innerHTML = error instanceof TypeError
          ? `<div class="card">Cannot reach the Brick Tidy server. Check that you are on the same
              network, then <a href="javascript:location.reload()">try again</a>.</div>`
          : `<div class="card">Error: ${esc(error.message)}</div>`;
      }
      return;
    }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", router);

// Re-render the current view after an action, keeping the scroll position.
async function refresh() {
  const y = window.scrollY;
  await router();
  window.scrollTo(0, y);
}
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
    <div class="card">
      <div class="row">
        <label class="button">📷 Identify set from photo<input type="file" accept="image/*" capture="environment" data-identify hidden></label>
        <label class="button">🖼 From gallery<input type="file" accept="image/*" multiple data-identify hidden></label>
      </div>
      <p class="muted">Photograph the box, the instructions or the partly built model (up to 3 photos).</p>
      <div id="identify-result"></div>
    </div>
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
    await startProject(input.value);
  });

  document.querySelectorAll("input[data-identify]").forEach((fileInput) => {
    fileInput.addEventListener("change", () => identifySet([...fileInput.files].slice(0, 3)));
  });
}

async function startProject(setNum) {
  try {
    const project = await api("/projects", { method: "POST", json: { set_num: setNum } });
    location.hash = `#/project/${project.id}/capture`;
  } catch (error) {
    toast(error.message);
  }
}

async function identifySet(files) {
  if (!files.length) return;
  const target = document.getElementById("identify-result");
  target.innerHTML = `<p class="muted">Looking for the set…</p>`;
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  let result;
  try {
    result = await api("/sets/identify", { method: "POST", body });
  } catch (error) {
    target.innerHTML = `<p class="muted">Identification failed: ${esc(error.message)}</p>`;
    return;
  }
  const notes = result.errors.map((e) => `<p class="muted">⚠ ${esc(e)}</p>`).join("");
  if (!result.candidates.length) {
    target.innerHTML = `${notes}<p class="muted">No set recognized. Try a photo of the box or the
      instruction cover, or type the set number above.</p>`;
    return;
  }
  target.innerHTML = `${notes}<h2>Is it one of these?</h2>${result.candidates.map((c) => `
    <div class="card part">
      ${partImage(c.img_url)}
      <div class="info">
        <div class="name">${esc(c.name)}</div>
        <div class="muted">${esc(c.set_num)} · ${esc(c.year)} · ${esc(c.num_parts)} parts ·
          ${Math.round(c.score * 100)}% <span class="tag">${c.sources.map(esc).join(" + ")}</span></div>
        ${c.evidence ? `<div class="muted">${esc(c.evidence)}</div>` : ""}
      </div>
      <button class="small primary" data-set="${esc(c.set_num)}">Start</button>
    </div>`).join("")}`;
  target.querySelectorAll("button[data-set]").forEach((button) => {
    button.addEventListener("click", () => startProject(button.dataset.set));
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
    ["find", "Find"],
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
  const views = {
    capture: renderCapture, review: renderReview, find: renderFind, missing: renderMissing, inventory: renderInventory,
  };
  await views[tab](section, project);
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
      // Loose bricks are looked for in the photo; build photos go straight to review.
      const next = input.dataset.kind === "pile" ? "find" : "review";
      if (failed < files.length) location.hash = `#/project/${project.id}/${next}`;
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
    bindReviewForm(form, detection, inventory, allColors);
  });
}

// Wires up a reviewCard() form: color choices per candidate, confirm and reject.
function bindReviewForm(form, detection, inventory, allColors) {
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
        const label = `${c.color_name}${c.part_num !== partNum ? ` (as ${c.part_num})` : ""} – ${c.missing} missing`
          + (c.matches_photo ? " · matches photo" : "");
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
      refresh();
    } catch (error) {
      toast(error.message);
    }
  });
  form.querySelector("[data-action=reject]").addEventListener("click", async () => {
    await api(`/detections/${detection.id}/reject`, { method: "POST" });
    refresh();
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
        ${d.color ? ` · ${swatch(d.color)}color in the photo` : ""}
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
              ${c.source === "claude" ? `<span class="tag">Claude</span>` : ""}
              ${c.reason ? `<br><span class="muted">${esc(c.reason)}</span>` : ""}
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

// Find: pile photos with a box per brick; bricks the set still needs are highlighted.
async function renderFind(section, project) {
  const [photos, inventory, allColors] = await Promise.all([
    api(`/projects/${project.id}/photos?kind=pile`),
    api(`/projects/${project.id}/inventory`),
    colors(),
  ]);
  if (!photos.length) {
    section.innerHTML = `<div class="empty">No photos of loose bricks yet.
      <a href="#/project/${project.id}/capture">Photograph a batch</a>.</div>`;
    return;
  }
  section.innerHTML = `
    <p class="muted"><span class="legend wanted"></span> part needed (check the color) ·
      <span class="legend found"></span> found · <span class="legend other"></span> not needed or unsure.
      Tap a box to check the brick.</p>
    ${photos.map((photo) => `
      <div class="card" data-photo="${photo.id}">
        <div class="muted">${esc(photo.created_at)} · ${photo.detections.length} bricks ·
          ${photo.wanted ? `<strong>${photo.wanted} needed</strong>` : "none needed"}</div>
        <div class="photo-wrap"><img src="${esc(photo.url)}" alt="Loose bricks"><div class="boxes"></div></div>
        <div class="selected-brick"></div>
      </div>`).join("")}`;

  for (const photo of photos) {
    const card = section.querySelector(`[data-photo="${photo.id}"]`);
    const img = card.querySelector("img");
    const place = () => drawBoxes(card, photo, img, inventory, allColors);
    if (img.complete && img.naturalWidth) place(); else img.addEventListener("load", place);
  }
}

// Boxes are positioned in % of the photo, so they follow the image at any width.
function drawBoxes(card, photo, img, inventory, allColors) {
  const width = img.naturalWidth;
  const height = img.naturalHeight;
  const boxes = card.querySelector(".boxes");
  boxes.innerHTML = photo.detections
    .filter((d) => d.status !== "rejected")
    .map((d) => {
      const [x, y, w, h] = d.bbox ?? [0, 0, width, height];
      const state = d.status === "confirmed" ? "found" : d.wanted ? "wanted" : "other";
      const style = `left:${(100 * x) / width}%;top:${(100 * y) / height}%;`
        + `width:${(100 * w) / width}%;height:${(100 * h) / height}%`;
      return `<button class="box ${state}" style="${style}" data-detection="${d.id}"
        aria-label="Brick ${d.id}">${state === "found" ? "✓" : ""}</button>`;
    }).join("");
  boxes.querySelectorAll(".box").forEach((box) => {
    box.addEventListener("click", () => {
      boxes.querySelectorAll(".box").forEach((b) => b.classList.toggle("selected", b === box));
      const detection = photo.detections.find((d) => String(d.id) === box.dataset.detection);
      showBrick(card.querySelector(".selected-brick"), detection, inventory, allColors);
    });
  });
}

// Brickognize does not report color, so "found" is one tap per color still missing.
function showBrick(target, d, inventory, allColors) {
  const top = d.candidates.find((c) => c.in_set) ?? d.candidates[0];
  const suggested = top?.set_colors.find((c) =>
    c.part_num === d.suggestion?.part_num && c.color_id === d.suggestion?.color_id);
  const name = (suggested && inventory.find((i) => i.part_num === suggested.part_num)?.part_name) ?? top?.name;
  // Closest to the measured color first (the server orders them); else most missing first.
  const missingColors = (top?.set_colors ?? []).filter((c) => c.missing > 0);
  if (!d.color) missingColors.sort((a, b) => b.missing - a.missing);
  // Common parts come in many colors: show the ones matching the photo, the rest on request.
  const unmatched = missingColors.filter((c) => !c.matches_photo).length;
  const hideUnmatched = unmatched > 0 && unmatched < missingColors.length;
  target.innerHTML = `
    <div class="part brick-summary">
      <img src="${esc(d.crop_url)}" alt="">
      <div class="info">
        <div class="name">${esc(name ?? "Unknown brick")}</div>
        <div class="muted">${d.status === "confirmed" ? `<span class="tag ok">recorded as found</span>`
          : missingColors.length ? "Which color is it?" : top?.in_set ? "all of these are present"
          : top ? "not in this set" : "not recognized"}</div>
        ${d.color ? `<div class="muted">${swatch(d.color)}color in the photo</div>` : ""}
      </div>
    </div>
    ${d.status === "pending" && missingColors.length ? `<div class="row">${missingColors.map((c, i) => `
      <button class="small color-choice ${c.matches_photo ? "matches" : ""}" data-index="${i}"
        ${hideUnmatched && !c.matches_photo ? "hidden" : ""}>
        ${swatch(c.color_rgb)}${esc(c.color_name)} <span class="muted">${c.missing}×</span></button>`).join("")}
      ${hideUnmatched ? `<button class="small" data-action="more">More colors (${unmatched})</button>` : ""}</div>`
      : ""}
    <div class="row"><button class="small" data-action="details">Other part or color…</button></div>
    <div class="details"></div>`;
  target.querySelector("[data-action=more]")?.addEventListener("click", (event) => {
    target.querySelectorAll(".color-choice[hidden]").forEach((b) => { b.hidden = false; });
    event.currentTarget.remove();
  });
  target.querySelectorAll(".color-choice").forEach((button) => {
    button.addEventListener("click", async () => {
      const c = missingColors[Number(button.dataset.index)];
      try {
        await api(`/detections/${d.id}/confirm`, {
          method: "POST", json: { part_num: c.part_num, color_id: c.color_id },
        });
        refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  });
  target.querySelector("[data-action=details]").addEventListener("click", () => {
    const details = target.querySelector(".details");
    details.innerHTML = reviewCard(d, inventory);
    bindReviewForm(details.querySelector("form"), d, inventory, allColors);
  });
}

async function renderMissing(section, project) {
  const missing = await api(`/projects/${project.id}/missing`);
  if (!missing.length) {
    section.innerHTML = `<div class="empty">🎉 Nothing missing. The set is complete.</div>`;
    return;
  }
  section.innerHTML = `
    <div class="row" style="justify-content:flex-end;margin-bottom:12px">
      <button class="small" data-export="details">Export CSV</button>
      <button class="small" data-export="rebrickable">Rebrickable CSV</button>
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
      refresh();
    });
  });
  // "rebrickable" is Rebrickable's parts list import format (Part, Color, Quantity with
  // Rebrickable IDs); Rebrickable can turn an imported list into a BrickLink wanted list.
  const formats = {
    details: { name: "missing", header: ["part_num", "color_id", "part_name", "color_name", "quantity"],
      row: (m) => [m.part_num, m.color_id, m.part_name, m.color_name, m.missing] },
    rebrickable: { name: "rebrickable", header: ["Part", "Color", "Quantity"],
      row: (m) => [m.part_num, m.color_id, m.missing] },
  };
  section.querySelectorAll("button[data-export]").forEach((button) => {
    button.addEventListener("click", () => {
      const format = formats[button.dataset.export];
      const csv = (values) => values.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",");
      const lines = [format.header.join(","), ...missing.map((m) => csv(format.row(m)))];
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
      link.download = `${project.set_num}-${format.name}.csv`;
      link.click();
    });
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
      refresh();
    });
  });
  section.querySelectorAll("button[data-undo]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/projects/${project.id}/parts/${button.dataset.undo}`, { method: "DELETE" });
      refresh();
    });
  });
}
