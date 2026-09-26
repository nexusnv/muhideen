(function () {
  "use strict";
  var params = new URLSearchParams(window.location.search);
  var displayId = params.get("id") || "UNKNOWN";
  var serverNow = null;
  var baseMono = null;
  function anchor(nowIso) {
    serverNow = new Date(nowIso).getTime();
    baseMono = performance.now();
  }
  function tick() {
    if (serverNow === null) return;
    var now = new Date(serverNow + (performance.now() - baseMono));
    var el = document.getElementById("hero-clock");
    if (el) el.textContent = now.toLocaleTimeString("en-GB", { hour12: false });
  }
  setInterval(tick, 1000);
  function poll() {
    fetch("/api/next-event?now=" + encodeURIComponent(new Date().toISOString()))
      .then(function (r) { return r.json(); })
      .then(function (data) { if (data.now) anchor(data.now); });
  }
  function heartbeat() {
    fetch("/api/displays/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: displayId }),
    }).catch(function () { /* offline: next beat retries */ });
  }
  heartbeat();
  setInterval(heartbeat, 30000);
  try {
    var src = new EventSource("/api/events");
    src.addEventListener("state", function () { window.location.reload(); });
    src.addEventListener("tick", function (e) {
      try { anchor(JSON.parse(e.data).now); } catch (err) { /* keep baseline */ }
    });
    src.addEventListener("config-update", function () { window.location.reload(); });
    src.onerror = function () { src.close(); setInterval(poll, 60000); };
  } catch (err) { setInterval(poll, 60000); }
})();
