// The add-on's button: remember which Dealflow to use, read the current page when clicked, and open
// Dealflow's "Check before saving" screen with what was read (in the part after #, which browsers
// never send to any server).
const api = typeof browser !== "undefined" ? browser : chrome;
const $ = (id) => document.getElementById(id);

function validBase(raw) {
  try {
    const u = new URL(raw.trim());
    const local = u.hostname === "localhost" || u.hostname === "127.0.0.1";
    if (u.protocol !== "https:" && !(local && u.protocol === "http:")) return null;
    return u.origin;
  } catch { return null; }
}

function toFragment(data) {
  const bytes = new TextEncoder().encode(JSON.stringify(data));
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function show(id) {
  for (const s of ["setup", "capture"]) $(s).hidden = s !== id;
}

async function getBase() {
  const got = await api.storage.local.get("base");
  return got.base || null;
}

async function setup(current) {
  show("setup");
  $("base").value = current || "";
  $("save").onclick = async () => {
    const base = validBase($("base").value);
    if (!base) {
      $("setup-error").textContent = "Enter the full address, starting with https://";
      $("setup-error").hidden = false;
      return;
    }
    await api.storage.local.set({ base });
    capture(base);
  };
}

function row(dl, label, value) {
  if (!value) return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.append(dt, dd);
}

async function capture(base) {
  show("capture");
  $("where").textContent = base;
  $("change").onclick = (e) => { e.preventDefault(); setup(base); };
  let result;
  try {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    const [{ result: r }] = await api.scripting.executeScript({ target: { tabId: tab.id }, func: dealflowExtract });
    result = r;
  } catch {
    result = { error: "This page can't be read. Open someone's LinkedIn profile or an email, then try again." };
  }
  const found = $("found");
  found.textContent = "";
  if (!result || result.error) {
    $("capture-error").textContent = (result && result.error) || "Nothing to add on this page.";
    $("capture-error").hidden = false;
    return;
  }
  const dl = document.createElement("dl");
  const v = result.values || {};
  row(dl, "Name", v.full_name);
  row(dl, "Title", v.designation);
  row(dl, "Firm", v.company);
  row(dl, "Email", v.work_email);
  row(dl, "From", { linkedin: "LinkedIn profile", gmail: "Gmail", outlook: "Outlook", selection: "Selected text" }[result.source]);
  found.append(dl);
  $("open").hidden = false;
  $("open").onclick = async () => {
    await api.tabs.create({ url: `${base}/capture#d=${toFragment(result)}` });
    window.close();
  };
}

(async () => {
  const base = await getBase();
  if (base) capture(base); else setup(null);
})();
