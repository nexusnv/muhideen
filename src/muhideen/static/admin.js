(function () {
  "use strict";
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
    "dhuha_offset_min": 28,
    "countdown_before_adhan_min": 5,
    "countdown_before_adhan_overrides": {},
    "theme": {
      "palette": "classic-green",
      "font": "outfit",
      "countdown_style": "boxes",
      "clock_format": "24h-seconds",
      "hijri_form": "long",
      "boundary_strip": "show",
      "density": "comfortable"
    }
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
      if (r.status === 429) { msg("login-msg", "Too many attempts, wait a minute"); return; }
      msg("login-msg", "Wrong password");
    }).catch(function () { msg("login-msg", "Network error"); });
  });
  var wizard = document.getElementById("wizard");
  if (wizard) {
    var step = 1;
    function show(n) {
      step = n;
      var secs = wizard.querySelectorAll("[data-step]");
      for (var i = 0; i < secs.length; i++) secs[i].hidden = Number(secs[i].getAttribute("data-step")) !== n;
      document.getElementById("w-back").hidden = n === 1;
      document.getElementById("w-next").textContent = n === 5 ? "Done" : "Next";
      if (n === 5) {
        document.getElementById("w-review").textContent =
          document.getElementById("w-name").value + " / " + document.getElementById("w-zone").value;
      }
    }
    function valid(n) {
      if (n === 1) return document.getElementById("w-name").value !== "" && document.getElementById("w-zone").value !== "";
      if (n === 2) {
        var lat = document.getElementById("w-lat").value;
        var lon = document.getElementById("w-lon").value;
        if (lat !== "" && (Number(lat) < -90 || Number(lat) > 90)) return false;
        if (lon !== "" && (Number(lon) < -180 || Number(lon) > 180)) return false;
        return true;
      }
      if (n === 3) {
        var p = document.getElementById("w-pass").value;
        return p.length >= 8 && p === document.getElementById("w-pass2").value;
      }
      if (n === 4) {
        var h = Number(document.getElementById("w-hijri").value);
        return h >= -2 && h <= 2;
      }
      return true;
    }
    document.getElementById("w-back").addEventListener("click", function () { show(step - 1); });
    var setupDone = false;
    document.getElementById("w-next").addEventListener("click", function () {
      msg("w-msg", "");
      document.getElementById("w-next").disabled = true;
      if (step < 5) {
        if (valid(step)) show(step + 1); else msg("w-msg", "Check input");
        document.getElementById("w-next").disabled = false;
        return;
      }
      var body = JSON.parse(JSON.stringify(DEFAULTS));
      body.masjid_name = document.getElementById("w-name").value;
      body.zone = document.getElementById("w-zone").value;
      body.calc_only = document.getElementById("w-calc").checked;
      body.lat = num("w-lat");
      body.lon = num("w-lon");
      body.hijri_offset = Number(document.getElementById("w-hijri").value);
      function putSettings() {
        fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }).then(function (r) {
          if (r.status === 200) { window.location.href = "/admin/settings"; return; }
          msg("w-msg", "Invalid settings (422)");
          document.getElementById("w-next").disabled = false;
        }).catch(function () {
          msg("w-msg", "Network error");
          document.getElementById("w-next").disabled = false;
        });
      }
      var needSetup = !setupDone;
      if (!needSetup) { putSettings(); return; }
      fetch("/api/auth/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: document.getElementById("w-pass").value }),
      }).then(function (r) {
        if (r.status === 200 || r.status === 409) { setupDone = true; putSettings(); return; }
        msg("w-msg", "Setup failed");
        document.getElementById("w-next").disabled = false;
      }).catch(function () {
        msg("w-msg", "Network error");
        document.getElementById("w-next").disabled = false;
      });
    });
    show(1);
  }
  var save = document.getElementById("s-save");
  if (save) save.addEventListener("click", function () {
    var rules = JSON.parse(document.getElementById("iqamah-rules").getAttribute("data-rules"));
    var cdDefaultEl = document.getElementById("s-countdown-default");
    var overrides = {};
    var prayers = ["fajr", "dhuhr", "asr", "maghrib", "isha", "jumuah"];
    for (var i = 0; i < prayers.length; i++) {
      var field = document.getElementById("s-cd-" + prayers[i]);
      if (field && field.value !== "") overrides[prayers[i]] = Number(field.value);
    }
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
      countdown_before_adhan_min: cdDefaultEl ? Number(cdDefaultEl.value) : 5,
      countdown_before_adhan_overrides: overrides,
      theme: {
        palette: document.getElementById("s-theme-palette").value,
        font: document.getElementById("s-theme-font").value,
        countdown_style: document.getElementById("s-theme-countdown").value,
        clock_format: document.getElementById("s-theme-clock").value,
        hijri_form: document.getElementById("s-theme-hijri").value,
        boundary_strip: document.getElementById("s-theme-boundary").value,
        density: document.getElementById("s-theme-density").value
      }
    };
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.status === 200) { msg("s-msg", "Saved — live reload"); return; }
      msg("s-msg", "Invalid settings (422)");
    }).catch(function () { msg("s-msg", "Network error"); });
  });
  var qrToggle = document.getElementById("qr-toggle");
  if (qrToggle) qrToggle.addEventListener("click", function () {
    var body = document.getElementById("qr-body");
    body.hidden = !body.hidden;
  });
  function esc(text) {
    return String(text).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function apiError(r, okMsg, el) {
    if (r.status === 200 || r.status === 201) { if (okMsg) msg(el, okMsg); return true; }
    if (r.status === 401) { msg(el, "Login required"); return false; }
    if (r.status === 409) { msg(el, "Already exists"); return false; }
    if (r.status === 413) { msg(el, "File too large (5MB limit)"); return false; }
    if (r.status === 422) { msg(el, "Invalid values (422)"); return false; }
    msg(el, "Request failed (" + r.status + ")");
    return false;
  }
  var overridesBox = document.getElementById("display-overrides");
  if (overridesBox) {
    fetch("/api/displays").then(function (r) {
      if (r.status !== 200) return;
      return r.json();
    }).then(function (data) {
      if (!data) return;
      var html = "";
      for (var i = 0; i < data.displays.length; i++) {
        (function (d) {
          html += '<div class="display-row" data-display="' + esc(d.id) + '">'
            + "<strong>" + esc(d.name) + "</strong>"
            + '<label>Theme <input data-theme-for="' + esc(d.id) + '" type="text" value="' + esc(d.current_theme) + '"></label>'
            + '<label>Group <select data-group-for="' + esc(d.id) + '">';
          for (var g = 0; g < data.groups.length; g++) {
            html += '<option value="' + esc(data.groups[g].name) + '"'
              + (data.groups[g].name === d.group_name ? " selected" : "") + ">"
              + esc(data.groups[g].name) + "</option>";
          }
          html += "</select></label>"
            + "<span>Dim " + esc(d.effective_dim_minutes) + " min</span>"
            + '<button class="btn" type="button" data-save-display="' + esc(d.id) + '">Apply</button>'
            + "</div>";
        })(data.displays[i]);
      }
      html += "<h3>Group dim overrides (minutes, blank for global default)</h3>";
      for (var j = 0; j < data.groups.length; j++) {
        (function (gr) {
          html += '<div class="display-row" data-dimgroup="' + esc(gr.name) + '">'
            + "<strong>" + esc(gr.name) + "</strong>"
            + '<label>Dim override <input data-dim-for="' + esc(gr.name) + '" type="number" min="5" max="60" value="'
            + (gr.dim_minutes_override === null ? "" : esc(gr.dim_minutes_override)) + '"></label>'
            + '<button class="btn" type="button" data-save-dim="' + esc(gr.name) + '">Apply</button>'
            + "</div>";
        })(data.groups[j]);
      }
      overridesBox.innerHTML = html;
      overridesBox.addEventListener("click", function (ev) {
        var saveId = ev.target.getAttribute && ev.target.getAttribute("data-save-display");
        if (saveId) {
          var themeEl = overridesBox.querySelector('[data-theme-for="' + saveId + '"]');
          var groupEl = overridesBox.querySelector('[data-group-for="' + saveId + '"]');
          fetch("/api/displays/" + encodeURIComponent(saveId), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ current_theme: themeEl.value, group_name: groupEl.value }),
          }).then(function (r) {
            if (r.status === 200) window.location.reload();
          }).catch(function () { msg("s-msg", "Network error"); });
          return;
        }
        var dimName = ev.target.getAttribute && ev.target.getAttribute("data-save-dim");
        if (dimName) {
          var dimEl = overridesBox.querySelector('[data-dim-for="' + dimName + '"]');
          fetch("/api/display-groups/" + encodeURIComponent(dimName), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ dim_minutes_override: dimEl.value === "" ? null : Number(dimEl.value) }),
          }).then(function (r) {
            if (r.status === 200) window.location.reload();
          }).catch(function () { msg("s-msg", "Network error"); });
        }
      });
    }).catch(function () { /* registry renders on next load */ });
  }
  var summaryBox = document.getElementById("playlist-summary");
  if (summaryBox) {
    fetch("/api/playlists").then(function (r) {
      if (r.status !== 200) return;
      return r.json();
    }).then(function (data) {
      if (!data) return;
      var names = [];
      for (var i = 0; i < data.playlists.length; i++) names.push(esc(data.playlists[i].title));
      summaryBox.textContent = data.playlists.length + " playlists: " + names.join(", ");
    }).catch(function () { /* summary renders on next load */ });
  }
  var editor = document.getElementById("playlist-editor");
  if (editor) {
    var currentId = null;
    var currentItems = [];
    function editorMsg(text) { msg("pl-msg", text); }
    function readEditor() {
      var cycles = document.getElementById("pl-cycles").value;
      var start = document.getElementById("pl-start").value;
      var end = document.getElementById("pl-end").value;
      var anchor = document.getElementById("pl-anchor").value;
      return {
        title: document.getElementById("pl-title").value,
        active: document.getElementById("pl-active").checked,
        window_start: start === "" ? null : start,
        window_end: end === "" ? null : end,
        anchor_marker: anchor === "" ? null : anchor,
        anchor_start_offset_min: Number(document.getElementById("pl-anchor-start").value) || 0,
        anchor_stop_offset_min: Number(document.getElementById("pl-anchor-stop").value) || 0,
        cycle_mode: "indefinite",
        max_cycles: cycles === "" ? null : Number(cycles),
        items: currentItems,
      };
    }
    function loadIntoForm(p) {
      currentId = p.id;
      currentItems = p.items || [];
      document.getElementById("pl-title").value = p.title;
      document.getElementById("pl-active").checked = p.active;
      document.getElementById("pl-start").value = p.window_start || "";
      document.getElementById("pl-end").value = p.window_end || "";
      document.getElementById("pl-anchor").value = p.anchor_marker || "";
      document.getElementById("pl-anchor-start").value = p.anchor_start_offset_min;
      document.getElementById("pl-anchor-stop").value = p.anchor_stop_offset_min;
      document.getElementById("pl-cycles").value = p.max_cycles === null ? "" : p.max_cycles;
      renderItems();
    }
    function renderItems() {
      var box = document.getElementById("pl-items");
      var html = "";
      for (var i = 0; i < currentItems.length; i++) {
        html += '<div class="pl-item"><span>' + esc(currentItems[i].image_path)
          + " (" + esc(currentItems[i].duration_s) + "s)</span>"
          + '<button class="btn" type="button" data-remove-item="' + currentItems[i].sort_order + '">Remove</button></div>';
      }
      box.innerHTML = html;
    }
    function renderList(playlists) {
      var box = document.getElementById("playlist-list");
      var html = "";
      for (var i = 0; i < playlists.length; i++) {
        (function (p) {
          html += '<div class="playlist-row" data-playlist="' + esc(p.id) + '">'
            + "<strong>" + esc(p.title) + "</strong>"
            + "<span>" + (p.active ? "active" : "paused") + ", " + p.items.length + " items</span>"
            + '<button class="btn" type="button" data-edit="' + esc(p.id) + '">Edit</button>'
            + '<button class="btn" type="button" data-toggle="' + esc(p.id) + '" data-active="' + (!p.active) + '">'
            + (p.active ? "Pause" : "Activate") + "</button></div>";
        })(playlists[i]);
      }
      box.innerHTML = html;
    }
    function refreshList() {
      fetch("/api/playlists").then(function (r) {
        if (r.status !== 200) return;
        return r.json();
      }).then(function (data) {
        if (data) renderList(data.playlists);
      }).catch(function () { editorMsg("Network error"); });
    }
    function renderPreview() {
      var holder = document.getElementById("occupancy-preview");
      var previewBody = document.getElementById("preview-body");
      if (!holder || !previewBody) return;
      var preview = JSON.parse(holder.getAttribute("data-preview") || "null");
      if (!preview) { previewBody.textContent = "Schedule unavailable"; return; }
      var html = '<p>Stage: <span class="badge">' + esc(preview.stage) + "</span></p>";
      for (var i = 0; i < preview.playlists.length; i++) {
        (function (p) {
          var state = p.on_stage_now
            ? '<span class="badge badge-on">on Stage now</span>'
            : (p.next_at ? "next at " + esc(p.next_at) : "no window in 24h");
          html += '<div class="preview-row"><strong>' + esc(p.title) + "</strong> " + state + "</div>";
        })(preview.playlists[i]);
      }
      previewBody.innerHTML = html;
    }
    var seed = document.getElementById("playlist-list").getAttribute("data-playlists");
    renderList(JSON.parse(seed || "[]"));
    renderPreview();
    document.getElementById("pl-new").addEventListener("click", function () {
      currentId = null;
      currentItems = [];
      document.getElementById("pl-title").value = "";
      renderItems();
      editorMsg("");
    });
    document.getElementById("playlist-list").addEventListener("click", function (ev) {
      var editId = ev.target.getAttribute && ev.target.getAttribute("data-edit");
      if (editId) {
        fetch("/api/playlists/" + encodeURIComponent(editId)).then(function (r) {
          if (r.status !== 200) { editorMsg("Playlist missing"); return; }
          return r.json();
        }).then(function (p) { if (p) { loadIntoForm(p); editorMsg(""); } })
          .catch(function () { editorMsg("Network error"); });
        return;
      }
      var toggleId = ev.target.getAttribute && ev.target.getAttribute("data-toggle");
      if (toggleId) {
        var want = ev.target.getAttribute("data-active") === "true";
        fetch("/api/playlists/" + encodeURIComponent(toggleId), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ active: want }),
        }).then(function (r) {
          if (apiError(r, "", "pl-msg")) { refreshList(); renderPreview(); }
        }).catch(function () { editorMsg("Network error"); });
      }
    });
    document.getElementById("pl-save").addEventListener("click", function () {
      var payload = readEditor();
      if (!payload.title) { editorMsg("Title is required"); return; }
      var method = currentId ? "PUT" : "POST";
      var url = currentId ? "/api/playlists/" + encodeURIComponent(currentId) : "/api/playlists";
      if (currentId) payload.id = currentId;
      fetch(url, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (r) {
        return r.json().then(function (saved) { return { status: r.status, saved: saved }; });
      }).then(function (out) {
        if (out.status === 200 || out.status === 201) {
          loadIntoForm(out.saved);
          refreshList();
          editorMsg("Saved");
        } else if (out.status === 422) {
          editorMsg("Invalid values (422)");
        } else {
          editorMsg("Request failed (" + out.status + ")");
        }
      }).catch(function () { editorMsg("Network error"); });
    });
    document.getElementById("pl-delete").addEventListener("click", function () {
      if (!currentId) { editorMsg("Nothing to delete"); return; }
      fetch("/api/playlists/" + encodeURIComponent(currentId), { method: "DELETE" })
        .then(function (r) {
          if (r.status === 200) {
            currentId = null;
            currentItems = [];
            refreshList();
            editorMsg("Deleted");
          } else {
            editorMsg("Request failed (" + r.status + ")");
          }
        }).catch(function () { editorMsg("Network error"); });
    });
    document.getElementById("pl-upload-go").addEventListener("click", function () {
      if (!currentId) { editorMsg("Save the playlist first"); return; }
      var picker = document.getElementById("pl-upload");
      if (!picker.files || !picker.files[0]) { editorMsg("Choose an image first"); return; }
      var reader = new FileReader();
      reader.onload = function () {
        var image_base64 = String(reader.result).split(",", 2)[1] || "";
        var duration_s = Number(document.getElementById("pl-duration").value) || 10;
        fetch("/api/playlists/" + encodeURIComponent(currentId) + "/items", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image_base64: image_base64, duration_s: duration_s }),
        }).then(function (r) {
          return r.json().then(function (saved) { return { status: r.status, saved: saved }; });
        }).then(function (out) {
          if (out.status === 201) {
            currentItems.push(out.saved);
            renderItems();
            refreshList();
            editorMsg("Uploaded");
          } else if (out.status === 413) {
            editorMsg("File too large (5MB limit)");
          } else if (out.status === 400) {
            editorMsg("Unreadable image (JPG, PNG, or WebP only)");
          } else if (out.status === 422) {
            editorMsg("Playlist is full (50 items)");
          } else {
            editorMsg("Request failed (" + out.status + ")");
          }
        }).catch(function () { editorMsg("Network error"); });
      };
      reader.onerror = function () { editorMsg("Could not read file"); };
      reader.readAsDataURL(picker.files[0]);
    });
    document.getElementById("pl-items").addEventListener("click", function (ev) {
      var order = ev.target.getAttribute && ev.target.getAttribute("data-remove-item");
      if (!order || !currentId) return;
      fetch("/api/playlists/" + encodeURIComponent(currentId) + "/items/" + encodeURIComponent(order), {
        method: "DELETE",
      }).then(function (r) {
        if (r.status !== 200) { editorMsg("Request failed (" + r.status + ")"); return; }
        var keep = [];
        for (var i = 0; i < currentItems.length; i++) {
          if (String(currentItems[i].sort_order) !== order) keep.push(currentItems[i]);
        }
        currentItems = keep;
        renderItems();
        refreshList();
      }).catch(function () { editorMsg("Network error"); });
    });
  }
})();
