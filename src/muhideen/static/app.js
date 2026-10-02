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
  function parts(epoch) {
    var str;
    if (tzName) {
      try {
        str = new Date(epoch).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", timeZone: tzName });
      } catch (err) { tzName = null; str = null; }
    }
    if (!str) {
      var shifted = tzOffset === tzOffset ? epoch + tzOffset * 60000 : epoch;
      var d = new Date(shifted);
      var base = tzOffset === tzOffset ? d.getUTCHours() : d.getHours();
      var mins = tzOffset === tzOffset ? d.getUTCMinutes() : d.getMinutes();
      return { h24: base, m: mins };
    }
    var hm = str.split(":");
    return { h24: parseInt(hm[0], 10), m: parseInt(hm[1], 10) };
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tick() {
    var epoch = serverEpoch();
    if (epoch === null) return;
    var p = parts(epoch);
    var h12 = p.h24 % 12 || 12;
    var period = p.h24 < 12 ? "AM" : "PM";
    var hmEls = document.querySelectorAll(".clock-hm");
    for (var i = 0; i < hmEls.length; i++) hmEls[i].innerHTML = h12 + '<span class="colon">:</span>' + pad(p.m);
    var perEls = document.querySelectorAll(".clock .period");
    for (var q = 0; q < perEls.length; q++) perEls[q].textContent = period;
    var cd = document.getElementById("countdown");
    if (cd) {
      var target = new Date(cd.getAttribute("data-target")).getTime();
      if (target !== target) { cd.textContent = "--:--:--"; return; }
      var remain = Math.max(0, target - epoch);
      var s = Math.floor(remain / 1000);
      cd.textContent = pad(Math.floor(s / 3600)) + ":" + pad(Math.floor((s % 3600) / 60)) + ":" + pad(s % 60);
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
  var dimEl = document.querySelector("[data-dim-until]");
  if (dimEl && dimEl.getAttribute("data-dim-until")) {
    var skipKey = "muhideen-dim-skip";
    try {
      if (localStorage.getItem(skipKey) === dimEl.getAttribute("data-dim-until")) {
        document.body.style.display = "none";
      }
    } catch (err) { /* storage unavailable: overlay stays */ }
    var pressTimer = null;
    function cancelPress() {
      if (pressTimer !== null) { clearTimeout(pressTimer); pressTimer = null; }
    }
    document.body.addEventListener("pointerdown", function () {
      cancelPress();
      pressTimer = setTimeout(function () {
        try { localStorage.setItem(skipKey, dimEl.getAttribute("data-dim-until") || ""); } catch (err) { /* fall through to hide */ }
        window.location.reload();
      }, 3000);
    });
    document.body.addEventListener("pointerup", cancelPress);
    document.body.addEventListener("pointerleave", cancelPress);
  }
  var adhanEl = document.getElementById("adhan-audio");
  if (adhanEl) {
    adhanEl.volume = Math.min(1, Math.max(0, (parseInt(adhanEl.getAttribute("data-volume") || "70", 10)) / 100));
    var p = adhanEl.play();
    if (p && p.catch) p.catch(function () {});
  }
})();
