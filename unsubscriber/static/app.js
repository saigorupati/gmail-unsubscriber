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

function selected() {
  return $$("#review .pick:checked").map((cb) => cb.closest("tr").dataset.address);
}

function refreshButtons() {
  const n = selected().length;
  if (unsubBtn) {
    unsubBtn.disabled = n === 0;
    unsubBtn.textContent = n ? `Unsubscribe selected (${n})` : "Unsubscribe selected";
  }
  if (keepBtn) keepBtn.disabled = n === 0;
}

$$("#review .pick").forEach((cb) => cb.addEventListener("change", refreshButtons));
const selectAll = $("#select-all");
if (selectAll) {
  selectAll.addEventListener("change", () => {
    $$("#review .pick").forEach((cb) => (cb.checked = selectAll.checked));
    refreshButtons();
  });
}

/* ---------- results & finish list ---------- */
const MANUAL_LABEL = { page: "Open unsubscribe page", email: "Open email draft", none: "Open in Gmail" };

function rowFor(address) {
  return $$("tr[data-address]").find((tr) => tr.dataset.address === address);
}

function markRow(address, text, cls) {
  const tr = rowFor(address);
  if (!tr) return;
  const cb = $(".pick", tr);
  if (cb) cb.remove();
  const res = $(".result", tr);
  if (res) {
    res.textContent = text;
    res.className = `result ${cls}`;
  }
  if (cls === "ok") tr.classList.add("done");
}

function addFinish(r) {
  const list = $("#finish-list");
  $("#finish").hidden = false;
  const li = document.createElement("li");
  const label = document.createElement("span");
  label.textContent = r.address + (r.error ? ` (one-click failed: ${r.error})` : "");
  const btn = document.createElement("button");
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
  list.append(li);
}

function handleResult(r) {
  if (r.outcome === "done") markRow(r.address, "Unsubscribed ✓", "ok");
  else if (r.outcome === "skipped") markRow(r.address, "Skipped (marked keep or gone)", "muted");
  else if (r.outcome === "failed") {
    markRow(r.address, `Failed: ${r.error}`, "bad");
    if (r.action_url) addFinish(r);
  } else if (r.outcome === "manual") {
    markRow(r.address, "Needs you, see “Finish these” above", "muted");
    addFinish(r);
  }
}

if (unsubBtn) {
  unsubBtn.addEventListener("click", async () => {
    const addrs = selected();
    if (!addrs.length) return;
    unsubBtn.disabled = keepBtn.disabled = true;
    unsubBtn.textContent = "Unsubscribing…";
    try {
      const results = await api("/api/unsubscribe", { addresses: addrs });
      results.forEach(handleResult);
      if (!$("#finish").hidden) $("#finish").scrollIntoView({ behavior: "smooth" });
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
      addrs.forEach((a) => markRow(a, "Kept", "muted"));
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
        else if (r.action_url) addFinish(r);
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
