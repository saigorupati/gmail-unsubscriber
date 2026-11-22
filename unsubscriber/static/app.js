"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(path, data = {}) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
  return resp.json();
}

function showError(msg) {
  const el = $("#error");
  if (!el) return alert(msg);
  el.textContent = msg;
  el.hidden = !msg;
  if (msg) el.scrollIntoView({ behavior: "smooth", block: "center" });
}

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

/* ---------- "How this works" box ---------- */
const intro = $("#intro");
if (intro) {
  if (store.get("introHidden") === "1") intro.hidden = true;
  $("#intro-close").addEventListener("click", () => {
    intro.hidden = true;
    store.set("introHidden", "1");
  });
}

/* ---------- sign in ---------- */
const signinBtn = $("#signin");
if (signinBtn) {
  signinBtn.addEventListener("click", async () => {
    signinBtn.disabled = true;
    $("#signin-status").textContent = "A Google sign-in tab has opened. Finish signing in there…";
    try {
      await api("/api/signin");
      location.reload();
    } catch (e) {
      $("#signin-status").textContent = "Sign-in didn't complete. Try again.";
      signinBtn.disabled = false;
    }
  });
}

/* ---------- scanning ---------- */
const scanBtn = $("#scan-btn");

async function pollScan() {
  const box = $("#progress");
  box.hidden = false;
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning…";
  for (;;) {
    const s = await (await fetch("/api/scan")).json();
    const pct = s.total ? Math.round((s.done / s.total) * 100) : 0;
    $("#progress-fill").style.width = `${s.total ? pct : 5}%`;
    $("#progress-text").textContent = s.total
      ? `${s.done.toLocaleString()} of ${s.total.toLocaleString()} emails (${pct}%)`
      : "Listing your mail…";
    if (!s.running) {
      if (s.auth_required) return location.reload();
      if (s.error) {
        box.hidden = true;
        scanBtn.disabled = false;
        scanBtn.textContent = "Scan now";
        return showError(`Scan stopped: ${s.error}. Click Scan now to pick up where it left off.`);
      }
      return location.reload();
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

if (scanBtn) {
  scanBtn.addEventListener("click", async () => {
    showError("");
    try {
      await api("/api/scan");
    } catch (e) {
      /* 409 = already running; just follow it */
    }
    pollScan();
  });
  if (scanBtn.dataset.running) pollScan();
}

/* ---------- selection ---------- */
const unsubBtn = $("#unsub-btn");
const keepBtn = $("#keep-btn");
const selectAll = $("#select-all");
const reviewBody = $("#review tbody");

const visibleRows = () => $$("#review tbody tr").filter((tr) => !tr.hidden);
const selectedRows = () => $$("#review .pick:checked").map((cb) => cb.closest("tr"));
const selected = () => selectedRows().map((tr) => tr.dataset.address);

function refreshButtons() {
  const n = selected().length;
  if (unsubBtn) {
    unsubBtn.disabled = n === 0;
    unsubBtn.textContent = n ? `Unsubscribe ${n} selected` : "Unsubscribe selected";
  }
  if (keepBtn) keepBtn.disabled = n === 0;
  const count = $("#sel-count");
  if (count) count.textContent = n ? `${n} selected` : "Nothing selected";
  if (selectAll) {
    const picks = visibleRows().map((tr) => $(".pick", tr)).filter(Boolean);
    const checked = picks.filter((cb) => cb.checked).length;
    selectAll.checked = picks.length > 0 && checked === picks.length;
    selectAll.indeterminate = checked > 0 && checked < picks.length;
  }
}

if (reviewBody) {
  reviewBody.addEventListener("change", (ev) => {
    if (ev.target.matches(".pick")) refreshButtons();
  });
  // Clicking anywhere on a row toggles it (except links/buttons, or when selecting text).
  reviewBody.addEventListener("click", (ev) => {
    if (ev.target.closest("a, button, input, label")) return;
    if (String(window.getSelection())) return;
    const tr = ev.target.closest("tr");
    const cb = tr && $(".pick", tr);
    if (!cb) return;
    cb.checked = !cb.checked;
    refreshButtons();
  });
}

if (selectAll) {
  selectAll.addEventListener("change", () => {
    visibleRows().forEach((tr) => {
      const cb = $(".pick", tr);
      if (cb) cb.checked = selectAll.checked;
    });
    refreshButtons();
  });
}

/* ---------- search & sort ---------- */
const search = $("#search");
if (search) {
  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    $$("#review tbody tr").forEach((tr) => {
      tr.hidden = q !== "" && !tr.dataset.name.includes(q);
      if (!tr.hidden) shown++;
    });
    $("#no-matches").hidden = shown > 0;
    refreshButtons();
  });
}

$$("#review th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    const desc = key === "name" ? th.classList.contains("sorted-asc") : !th.classList.contains("sorted-desc");
    $$("#review th.sortable").forEach((h) => h.classList.remove("sorted-asc", "sorted-desc"));
    th.classList.add(desc ? "sorted-desc" : "sorted-asc");
    const rows = $$("#review tbody tr");
    rows.sort((a, b) => {
      const va = a.dataset[key], vb = b.dataset[key];
      const cmp = key === "name" ? va.localeCompare(vb) : Number(va) - Number(vb);
      return desc ? -cmp : cmp;
    });
    rows.forEach((tr) => reviewBody.append(tr));
  });
});

/* ---------- results & finish list ---------- */
const MANUAL_LABEL = {
  page: "Open their unsubscribe page",
  email: "Open the email draft",
  none: "Open their email in Gmail",
};

function rowFor(address) {
  return $$("tr[data-address]").find((tr) => tr.dataset.address === address);
}

function markRow(address, text, cls, undo) {
  const tr = rowFor(address);
  if (!tr) return;
  const cb = $(".pick", tr);
  if (cb) cb.remove();
  const res = $(".result", tr);
  if (res) {
    res.textContent = text;
    res.className = `result ${cls}`;
    if (undo) {
      const link = Object.assign(document.createElement("button"), { textContent: "Undo", className: "link" });
      link.addEventListener("click", undo);
      res.append(" · ", link);
    }
  }
  tr.classList.toggle("done", cls === "ok" || cls === "kept");
}

function restoreRow(address) {
  const tr = rowFor(address);
  if (!tr) return;
  tr.classList.remove("done");
  const res = $(".result", tr);
  if (res) { res.textContent = ""; res.className = "result"; }
  if (!$(".pick", tr)) {
    const cb = Object.assign(document.createElement("input"), { type: "checkbox", className: "pick" });
    cb.setAttribute("aria-label", `Select ${address}`);
    tr.cells[0].append(cb);
  }
  refreshButtons();
}

function addFinish(r) {
  const finish = $("#finish");
  finish.hidden = false;
  const li = document.createElement("li");
  const label = document.createElement("span");
  label.textContent = r.address + (r.error ? ` (automatic unsubscribe failed: ${r.error})` : "");
  const btn = document.createElement("button");
  btn.className = "primary";
  btn.textContent = MANUAL_LABEL[r.method] || "Open";
  btn.addEventListener("click", async () => {
    window.open(r.action_url, "_blank", "noopener");
    try {
      await api("/api/confirm", { address: r.address, method: r.method });
      btn.replaceWith(Object.assign(document.createElement("span"), { textContent: "✓ Done", className: "ok" }));
      markRow(r.address, "Unsubscribed", "ok");
    } catch (e) {
      showError(e.message);
    }
  });
  li.append(label, btn);
  $("#finish-list").append(li);
}

function handleResult(r) {
  if (r.outcome === "done") markRow(r.address, "Unsubscribed ✓", "ok");
  else if (r.outcome === "skipped") markRow(r.address, "Skipped (marked keep or gone)", "muted");
  else if (r.outcome === "failed") {
    markRow(r.address, `Couldn't unsubscribe automatically: ${r.error}`, "bad");
    if (r.action_url) addFinish(r);
  } else if (r.outcome === "manual") {
    markRow(r.address, "Needs one click from you, see “Finish these” below", "muted");
    addFinish(r);
  }
}

/* ---------- confirm dialog ---------- */
function confirmUnsubscribe(rows) {
  const dialog = $("#confirm-dialog");
  const auto = rows.filter((tr) => tr.dataset.method === "one_click").length;
  const manual = rows.length - auto;
  $("#confirm-title").textContent = `Unsubscribe from ${rows.length} sender${rows.length === 1 ? "" : "s"}?`;
  const body = $("#confirm-body");
  body.replaceChildren();
  const add = (text) => body.append(Object.assign(document.createElement("li"), { textContent: text }));
  if (auto) add(`${auto} will be unsubscribed automatically right away. This can't be undone from here.`);
  if (manual) add(`${manual} ${manual === 1 ? "needs" : "need"} one click from you afterwards (we'll list them).`);
  if (!dialog.showModal) return Promise.resolve(window.confirm($("#confirm-title").textContent));
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.returnValue = "";
    dialog.showModal();
    $("#confirm-ok").focus();
  });
}

if (unsubBtn) {
  unsubBtn.addEventListener("click", async () => {
    const rows = selectedRows();
    if (!rows.length || !(await confirmUnsubscribe(rows))) return;
    const addrs = rows.map((tr) => tr.dataset.address);
    unsubBtn.disabled = keepBtn.disabled = true;
    unsubBtn.textContent = "Unsubscribing…";
    try {
      const results = await api("/api/unsubscribe", { addresses: addrs });
      results.forEach(handleResult);
      if (!$("#finish").hidden) $("#finish").scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (e) {
      showError(e.message);
    }
    refreshButtons();
  });
}

if (keepBtn) {
  keepBtn.addEventListener("click", async () => {
    const addrs = selected();
    try {
      await api("/api/keep", { addresses: addrs });
      addrs.forEach((a) =>
        markRow(a, "Kept", "kept", async () => {
          try {
            await api("/api/undo", { address: a });
            restoreRow(a);
          } catch (e) {
            showError(e.message);
          }
        })
      );
    } catch (e) {
      showError(e.message);
    }
    refreshButtons();
  });
}

/* ---------- per-row actions ---------- */
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const address = btn.closest("tr").dataset.address;
  btn.disabled = true;
  try {
    switch (btn.dataset.action) {
      case "retry": {
        const r = await api("/api/retry", { address });
        if (r.outcome === "done") location.reload();
        else if (r.action_url) addRetryFinish(btn, r);
        else showError(r.error ? `Retry failed: ${r.error}` : "Nothing else to try for this sender.");
        break;
      }
      case "hide":
        await api("/api/hide", { address });
        location.reload();
        break;
      case "keep-one":
        await api("/api/keep", { addresses: [address] });
        location.reload();
        break;
      case "undo":
        await api("/api/undo", { address });
        location.reload();
        break;
    }
  } catch (e) {
    showError(e.message);
    btn.disabled = false;
  }
});

// Retry from "Still sending": the review list may not exist, so offer the link in place.
function addRetryFinish(btn, r) {
  const link = Object.assign(document.createElement("button"), {
    className: "primary",
    textContent: MANUAL_LABEL[r.method] || "Open",
  });
  link.addEventListener("click", async () => {
    window.open(r.action_url, "_blank", "noopener");
    await api("/api/confirm", { address: r.address, method: r.method });
    location.reload();
  });
  btn.replaceWith(link);
}
