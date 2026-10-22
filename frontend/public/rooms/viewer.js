// Data room viewer: tells the server which page is being read, every 15 seconds while the tab is
// visible, so the deal team sees how long each document was read. No other tracking.
(function () {
  var ping = document.querySelector('meta[name="df-ping"]');
  var csrf = document.querySelector('meta[name="df-csrf"]');
  if (!ping || !csrf) return;
  var current = 1;
  var label = document.querySelector("[data-pageno]");
  var field = document.querySelector("[data-pagefield]");
  var sheets = Array.prototype.slice.call(document.querySelectorAll(".sheet"));
  var total = sheets.length;
  function show() {
    if (label) label.textContent = "Page " + current + " of " + total;
    if (field && !field.matches(":focus")) field.placeholder = String(current);
  }
  if ("IntersectionObserver" in window) {
    var seen = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { seen[e.target.getAttribute("data-page")] = e.intersectionRatio; });
      var best = current, ratio = 0;
      Object.keys(seen).forEach(function (k) { if (seen[k] > ratio) { ratio = seen[k]; best = parseInt(k, 10); } });
      if (ratio > 0) { current = best; show(); }
    }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
    sheets.forEach(function (s) { io.observe(s); });
  }
  function send() {
    if (document.visibilityState !== "visible") return;
    fetch(ping.content, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.content },
      body: JSON.stringify({ page: current })
    }).then(function (r) { if (r.status === 401) window.location.reload(); }).catch(function () {});
  }
  document.addEventListener("contextmenu", function (e) { if (e.target.tagName === "IMG") e.preventDefault(); });
  send();
  setInterval(send, 15000);
  document.addEventListener("visibilitychange", send);
})();
