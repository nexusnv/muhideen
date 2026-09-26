(function () {
  "use strict";
  function setLang(lang) {
    document.documentElement.lang = lang;
    var els = document.querySelectorAll("[data-en]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (!el.getAttribute("data-bm")) el.setAttribute("data-bm", el.textContent);
      el.textContent = lang === "en" ? el.getAttribute("data-en") : el.getAttribute("data-bm");
    }
    var btn = document.getElementById("lang-toggle");
    if (btn) btn.textContent = lang === "en" ? "BM" : "EN";
  }
  var toggle = document.getElementById("lang-toggle");
  if (toggle) toggle.addEventListener("click", function () {
    setLang(document.documentElement.lang === "en" ? "bm" : "en");
  });
  function msg(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  var DEFAULTS = {
    "masjid_name": "",
    "zone": "",
    "hijri_offset": 0,
    "adhan_duration_s": 180,
    "dim_minutes_default": 20,
    "dim_minutes_jumuah": 45,
    "iqamah_rules": [
      {"prayer": "fajr", "mode": "delay", "delay_minutes": 15, "fixed_time": null},
      {"prayer": "dhuhr", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
      {"prayer": "asr", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
      {"prayer": "maghrib", "mode": "delay", "delay_minutes": 10, "fixed_time": null},
      {"prayer": "isha", "mode": "delay", "delay_minutes": 15, "fixed_time": null},
      {"prayer": "jumuah", "mode": "delay", "delay_minutes": 10, "fixed_time": null}
    ],
    "lat": null,
    "lon": null,
    "method": "MABIMS",
    "boundary_countdown": false,
    "calc_only": false,
    "imsak_offset_min": 10,
    "dhuha_offset_min": 28
  };
  function num(id) {
    var v = document.getElementById(id).value;
    return v === "" ? null : Number(v);
  }
  var loginGo = document.getElementById("login-go");
  if (loginGo) loginGo.addEventListener("click", function () {
    fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.getElementById("password").value }),
    }).then(function (r) {
      if (r.status === 200) { window.location.href = "/admin/settings"; return; }
      if (r.status === 429) { msg("login-msg", "Terlalu banyak cubaan, tunggu sebentar"); return; }
      msg("login-msg", "Kata laluan salah");
    }).catch(function () { msg("login-msg", "Ralat rangkaian"); });
  });
  var wizard = document.getElementById("wizard");
  if (wizard) {
    var step = 1;
    function show(n) {
      step = n;
      var secs = wizard.querySelectorAll("[data-step]");
      for (var i = 0; i < secs.length; i++) secs[i].hidden = Number(secs[i].getAttribute("data-step")) !== n;
      document.getElementById("w-back").hidden = n === 1;
      document.getElementById("w-next").textContent = n === 5 ? "Selesai" : "Seterusnya";
      if (n === 5) {
        document.getElementById("w-review").textContent =
          document.getElementById("w-name").value + " / " + document.getElementById("w-zone").value;
      }
    }
    function valid(n) {
      if (n === 1) return document.getElementById("w-name").value !== "" && document.getElementById("w-zone").value !== "";
      if (n === 3) {
        var p = document.getElementById("w-pass").value;
        return p.length >= 8 && p === document.getElementById("w-pass2").value;
      }
      return true;
    }
    document.getElementById("w-back").addEventListener("click", function () { show(step - 1); });
    document.getElementById("w-next").addEventListener("click", function () {
      msg("w-msg", "");
      if (step < 5) { if (valid(step)) show(step + 1); else msg("w-msg", "Semak input"); return; }
      var body = JSON.parse(JSON.stringify(DEFAULTS));
      body.masjid_name = document.getElementById("w-name").value;
      body.zone = document.getElementById("w-zone").value;
      body.calc_only = document.getElementById("w-calc").checked;
      body.lat = num("w-lat");
      body.lon = num("w-lon");
      body.hijri_offset = Number(document.getElementById("w-hijri").value);
      fetch("/api/auth/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: document.getElementById("w-pass").value }),
      }).then(function (r) {
        if (r.status !== 200) { msg("w-msg", "Persediaan gagal"); return null; }
        return fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }).then(function (r) {
        if (r === null) return;
        if (r.status === 200) window.location.href = "/admin/settings";
        else msg("w-msg", "Tetapan tidak sah (422)");
      }).catch(function () { msg("w-msg", "Ralat rangkaian"); });
    });
    show(1);
  }
  var save = document.getElementById("s-save");
  if (save) save.addEventListener("click", function () {
    var rules = JSON.parse(document.getElementById("iqamah-rules").getAttribute("data-rules"));
    var body = {
      masjid_name: document.getElementById("s-name").value,
      zone: document.getElementById("s-zone").value,
      hijri_offset: Number(document.getElementById("s-hijri").value),
      adhan_duration_s: Number(document.getElementById("s-adhan").value),
      dim_minutes_default: Number(document.getElementById("s-dim").value),
      dim_minutes_jumuah: Number(document.getElementById("s-dimj").value),
      iqamah_rules: rules,
      lat: num("s-lat"),
      lon: num("s-lon"),
      method: document.getElementById("s-method").value,
      boundary_countdown: document.getElementById("s-boundary").checked,
      calc_only: document.getElementById("s-calc").checked,
      imsak_offset_min: Number(document.getElementById("s-imsak").value),
      dhuha_offset_min: Number(document.getElementById("s-dhuha").value),
    };
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.status === 200) { msg("s-msg", "Disimpan — dimuat semula"); return; }
      msg("s-msg", "Tetapan tidak sah (422)");
    }).catch(function () { msg("s-msg", "Ralat rangkaian"); });
  });
  var qrToggle = document.getElementById("qr-toggle");
  if (qrToggle) qrToggle.addEventListener("click", function () {
    var body = document.getElementById("qr-body");
    body.hidden = !body.hidden;
  });
})();
