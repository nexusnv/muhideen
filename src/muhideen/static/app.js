(function () {
  "use strict";
  var clockEl = document.getElementById("hero-clock");
  var params = new URLSearchParams(window.location.search);
  var displayId = params.get("id") || "UNKNOWN";
  var domState = clockEl ? clockEl.getAttribute("data-state") : null;
  var domNext = clockEl ? clockEl.getAttribute("data-next") : null;
  var domAdhan = clockEl ? clockEl.getAttribute("data-adhan") : null;
  var tzName = clockEl ? clockEl.getAttribute("data-tz") : null;
  var serverNow = null;
  var baseMono = null;
  function anchor(nowIso) {
    if (!nowIso) return;
    serverNow = new Date(nowIso).getTime();
    baseMono = performance.now();
  }
  function serverEpoch() {
    if (serverNow === null) return null;
    return serverNow + (performance.now() - baseMono);
  }
  if (clockEl && clockEl.getAttribute("data-now")) anchor(clockEl.getAttribute("data-now"));
  function fmt(epoch) {
    var base = { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" };
    if (tzName) {
      try {
        var opts = { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: tzName };
        return new Date(epoch).toLocaleTimeString("en-GB", opts);
      } catch (err) { tzName = null; }
    }
    return new Date(epoch).toLocaleTimeString("en-GB", base);
  }
  function tick() {
    var epoch = serverEpoch();
    if (epoch === null || !clockEl) return;
    clockEl.textContent = fmt(epoch);
  }
  setInterval(tick, 1000);
  function sameAsDom(data) {
    return data && data.state === domState && (data.next_prayer || "") === (domNext || "") && (data.adhan_at || "") === (domAdhan || "");
  }
  function poll() {
    var epoch = serverEpoch();
    var qs = epoch === null ? "" : "?now=" + encodeURIComponent(new Date(epoch).toISOString());
    fetch("/api/next-event" + qs)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.now) anchor(data.now);
        if (!sameAsDom(data)) window.location.reload();
      })
      .catch(function () { /* offline: retry in 60s */ });
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
  var firstStateSeen = false;
  try {
    var src = new EventSource("/api/events");
    src.addEventListener("state", function (e) {
      var data = null;
      try { data = JSON.parse(e.data); } catch (err) { data = null; }
      if (!firstStateSeen) {
        firstStateSeen = true;
        if (data && data.now) anchor(data.now);
        if (!sameAsDom(data)) window.location.reload();
        return;
      }
      window.location.reload();
    });
    src.addEventListener("tick", function (e) {
      try { anchor(JSON.parse(e.data).now); } catch (err) { /* keep baseline */ }
    });
    src.addEventListener("config-update", function () { window.location.reload(); });
    var pollStarted = false;
    function startPoll() {
      if (pollStarted) return;
      pollStarted = true;
      setInterval(poll, 60000);
    }
    src.onerror = function () { src.close(); startPoll(); };
  } catch (err) { setInterval(poll, 60000); }
})();
