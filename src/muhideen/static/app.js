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
  var clockFmt = document.body ? document.body.getAttribute("data-clock-format") : null;
  function fmt(epoch) {
    var use12 = clockFmt === "12h";
    var withSeconds = clockFmt !== "24h" && clockFmt !== "12h";
    var locale = use12 ? "en-US" : "en-GB";
    var base = { hour12: use12, hour: "2-digit", minute: "2-digit" };
    if (withSeconds) base.second = "2-digit";
    if (tzName) {
      try {
        var opts = { hour12: use12, hour: "2-digit", minute: "2-digit", timeZone: tzName };
        if (withSeconds) opts.second = "2-digit";
        return new Date(epoch).toLocaleTimeString(locale, opts);
      } catch (err) { tzName = null; }
    }
    if (tzOffset === tzOffset) {
      var shifted = { hour12: use12, hour: "2-digit", minute: "2-digit", timeZone: "UTC" };
      if (withSeconds) shifted.second = "2-digit";
      return new Date(epoch + tzOffset * 60000).toLocaleTimeString(locale, shifted);
    }
    return new Date(epoch).toLocaleTimeString(locale, base);
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
    var hms = document.querySelectorAll("[data-cd-target]");
    for (var m = 0; m < hms.length; m++) {
      var hmsTarget = new Date(hms[m].getAttribute("data-cd-target")).getTime();
      var hEls = hms[m].querySelectorAll("[data-cd-h]");
      var mEls = hms[m].querySelectorAll("[data-cd-m]");
      var sEls = hms[m].querySelectorAll("[data-cd-s]");
      if (hmsTarget !== hmsTarget) {
        setHms(hEls, "--"); setHms(mEls, "--"); setHms(sEls, "--");
        continue;
      }
      var rem2 = Math.max(0, hmsTarget - epoch);
      var s2 = Math.floor(rem2 / 1000);
      setHms(hEls, pad(Math.floor(s2 / 3600)));
      setHms(mEls, pad(Math.floor((s2 % 3600) / 60)));
      setHms(sEls, pad(s2 % 60));
    }
  }
  function setHms(els, val) {
    for (var q = 0; q < els.length; q++) els[q].textContent = val;
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
  var lastStage = null;
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
      var tick = null;
      try { tick = JSON.parse(e.data); } catch (err) { tick = null; }
      if (tick === null) return;
      if (tick.now) anchor(tick.now);
      if (typeof tick.stage !== "string") return;
      if (lastStage !== null && tick.stage !== lastStage) {
        window.location.reload();
        return;
      }
      lastStage = tick.stage;
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
    var skipKey = "muhideen-dim-skip";
    try {
      if (localStorage.getItem(skipKey) === dimEl.getAttribute("data-dim-until")) {
        dimEl.style.display = "none";
      }
    } catch (err) { /* storage unavailable: overlay stays */ }
    var pressTimer = null;
    function cancelPress() {
      if (pressTimer !== null) { clearTimeout(pressTimer); pressTimer = null; }
    }
    dimEl.addEventListener("pointerdown", function () {
      cancelPress();
      pressTimer = setTimeout(function () {
        try { localStorage.setItem(skipKey, dimEl.getAttribute("data-dim-until") || ""); } catch (err) { /* fall through to hide */ }
        window.location.reload();
      }, 3000);
    });
    dimEl.addEventListener("pointerup", cancelPress);
    dimEl.addEventListener("pointerleave", cancelPress);
  }
  var adhanEl = document.getElementById("adhan-audio");
  if (adhanEl) {
    adhanEl.volume = Math.min(1, Math.max(0, (parseInt(adhanEl.getAttribute("data-volume") || "70", 10)) / 100));
    var p = adhanEl.play();
    if (p && p.catch) p.catch(function () {});
  }
})();
