// Runs in the page the person is looking at, only when they click the add-on's button.
// Reads one person from a LinkedIn profile, the open email in Gmail or Outlook on the web, or the
// text they've selected. Returns plain values; nothing is sent from here.
function dealflowExtract() {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const host = location.hostname;
  const selection = String(window.getSelection() || "").trim();
  if (selection.length > 12) {
    return { source: "selection", values: {}, text: selection.slice(0, 6000) };
  }
  if (/(^|\.)linkedin\.com$/.test(host) && /^\/in\//.test(location.pathname)) {
    const main = document.querySelector("main") || document;
    const name = clean((main.querySelector("h1") || {}).textContent);
    const headline = clean((main.querySelector(".text-body-medium") || {}).textContent);
    const place = clean((main.querySelector(".text-body-small.inline") || {}).textContent);
    let company = "";
    const btn = main.querySelector('button[aria-label^="Current company"]');
    if (btn) company = clean(btn.getAttribute("aria-label").replace(/^Current company:\s*/i, "").replace(/\.\s*Click.*$/i, ""));
    let designation = headline;
    const m = headline.match(/^(.*?)\s+(?:at|@)\s+(.+)$/i);
    if (m) { designation = m[1]; company = company || m[2]; }
    const handle = location.pathname.split("/")[2] || "";
    return {
      source: "linkedin",
      values: { full_name: name, designation: designation.split("|")[0].trim().slice(0, 120), company: company.slice(0, 120),
        city: place.split(",")[0].slice(0, 80), linkedin: handle ? `https://www.linkedin.com/in/${handle}/` : "" },
    };
  }
  if (host === "mail.google.com") {
    const open = document.querySelectorAll("div.adn");
    const last = open[open.length - 1];
    if (last) {
      const from = last.querySelector("span.gD[email]");
      const body = last.querySelector("div.a3s");
      return { source: "gmail", values: { full_name: from ? from.getAttribute("name") || "" : "", work_email: from ? from.getAttribute("email") || "" : "" },
        text: body ? body.innerText.slice(-5000) : "" };
    }
  }
  if (/^outlook\.(office|office365|live)\.com$/.test(host)) {
    const pane = document.querySelector('[aria-label="Reading Pane"]') || document.querySelector('[role="main"]') || document;
    let name = "", email = "";
    for (const el of pane.querySelectorAll("[title], [aria-label]")) {
      const t = el.getAttribute("title") || el.getAttribute("aria-label") || "";
      const m = t.match(/^(?:From:\s*)?(.*?)\s*<([^<>\s]+@[^<>\s]+)>/);
      if (m) { name = clean(m[1]); email = m[2].toLowerCase(); break; }
    }
    const body = pane.querySelector('[aria-label="Message body"]') || pane.querySelector('div[role="document"]');
    if (email || body) return { source: "outlook", values: { full_name: name, work_email: email }, text: body ? body.innerText.slice(-5000) : "" };
  }
  return { source: "page", values: {}, error: "Open someone's LinkedIn profile or an email first, or select their signature on the page." };
}
