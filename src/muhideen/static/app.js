(function () {
  "use strict";
  var clockEl = document.getElementById("hero-clock");
  var params = new URLSearchParams(window.location.search);
  var displayId = params.get("id") || "UNKNOWN";
  var domState = clockEl ? clockEl.getAttribute("data-state") : null;
  var domNext = clockEl ? clockEl.getAttribute("data-next") : null;
  var domAdhan = clockEl ? clockEl.getAttribute("data-adhan") : null;
  var tzName = clockEl ? clockEl.getAttribute("data-tz") : null;
  var tzOffsetAttr = clockEl ? clockEl.getAttribute("data-tzoffset") : null; var tzOffset = tzOffsetAttr === null ? NaN : parseInt(tzOffsetAttr, 10);
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
    if (tzOffset === tzOffset) {
      var shifted = { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" };
      return new Date(epoch + tzOffset * 60000).toLocaleTimeString("en-GB", shifted);
    }
    return new Date(epoch).toLocaleTimeString("en-GB", base);
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tick() {
    var epoch = serverEpoch();
    if (epoch === null) return;
    var clocks = document.querySelectorAll(".js-clock");
    for (var i = 0; i < clocks.length; i++) clocks[i].textContent = fmt(epoch);
    var downs = document.querySelectorAll("[data-countdown]");
    for (var j = 0; j < downs.length; j++) {
      var target = new Date(downs[j].getAttribute("data-countdown")).getTime();
      var remain = Math.max(0, target - epoch);
      var s = Math.floor(remain / 1000);
      downs[j].textContent = pad(Math.floor(s / 60)) + ":" + pad(s % 60);
    }
    var bars = document.querySelectorAll("[data-bar-start]");
    for (var k = 0; k < bars.length; k++) {
      var start = new Date(bars[k].getAttribute("data-bar-start")).getTime();
      var end = new Date(bars[k].getAttribute("data-bar-end")).getTime();
      var pct = end <= start ? 100 : Math.min(100, Math.max(0, (epoch - start) / (end - start) * 100));
      bars[k].style.width = pct + "%";
    }
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
  var dimEl = document.getElementById("dim");
  if (dimEl) {
    var pressTimer = null;
    function cancelPress() {
      if (pressTimer !== null) { clearTimeout(pressTimer); pressTimer = null; }
    }
    dimEl.addEventListener("pointerdown", function () {
      cancelPress();
      pressTimer = setTimeout(function () { dimEl.style.display = "none"; }, 3000);
    });
    dimEl.addEventListener("pointerup", cancelPress);
    dimEl.addEventListener("pointerleave", cancelPress);
  }
})();
