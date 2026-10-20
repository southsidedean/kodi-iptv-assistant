"""What's on: "On now" and "Next" in a playlist page's channel list, and a timeline of the
next few hours. Reads guide-preview.json (kia.xmltv.write_preview) and works out "now"
from the browser's clock, so it stays right between guide refreshes."""

STYLE = """
/* What's on */
#channels td.now { min-width: 9rem; }
#channels .when { color: var(--muted); font-size: var(--step--1); white-space: nowrap; }
#channels .none { color: var(--muted); }
.view { display: flex; flex-wrap: wrap; gap: var(--space-1); margin: var(--space-1) 0; }
.view button[aria-pressed=true] { background: var(--info-bg); border-color: var(--info); }
.guide-age { margin: var(--space-1) 0 0; }
.tl-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-top: var(--space-2); }
.tl { min-width: 46rem; position: relative; }
.tl-head, .tl-row { display: grid; grid-template-columns: 11rem 1fr; align-items: stretch; }
.tl-head { position: sticky; top: 0; background: var(--card); z-index: 2; border-bottom: 1px solid var(--line); }
.tl-hours { position: relative; height: 1.8rem; }
.tl-hours span { position: absolute; top: .3rem; font-size: var(--step--1); color: var(--muted); transform: translateX(-50%); white-space: nowrap; }
.tl-row { border-bottom: 1px solid var(--line); min-height: 2.6rem; }
.tl-row[hidden], .tl-group[hidden] { display: none; }
.tl-group { font-weight: 600; padding: .6rem 0 .3rem; border-bottom: 1px solid var(--line); }
.tl-name { padding: .45rem .5rem .45rem 0; font-size: var(--step--1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-name b { font-variant-numeric: tabular-nums; color: var(--muted); font-weight: 500; margin-right: .35rem; }
.tl-track { position: relative; }
.tl-block { position: absolute; top: .25rem; bottom: .25rem; background: var(--info-bg); border-radius: 6px; padding: .2rem .4rem;
  font-size: var(--step--1); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; border-left: 2px solid var(--card); }
.tl-block.on { background: var(--warn-bg); }
.tl-empty { position: absolute; top: .7rem; left: .4rem; font-size: var(--step--1); color: var(--muted); }
.tl-now { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--bar-dead); z-index: 1; pointer-events: none; }
"""

SCRIPT = r"""
<script>
(function () {
  var table = document.getElementById("channels");
  var card = document.getElementById("whats-on");
  if (!table || !card) return;
  var root = card.dataset.root || "";
  var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr:not(.group-head)"));
  var guide = null, timeline = null;
  var HOURS = 6;

  function time(t) { return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function slots(row) { return (guide && guide.channels[row.dataset.id]) || []; }
  function current(list, now) {
    for (var i = 0; i < list.length; i++) if (list[i][1] > now) return i;
    return -1;
  }

  function fill() {
    var now = Date.now() / 1000;
    rows.forEach(function (row) {
      var list = slots(row), i = current(list, now);
      var on = row.querySelector("td.now"), next = row.querySelector("td.next");
      on.textContent = ""; next.textContent = "";
      if (i < 0) { on.appendChild(el("span", "none", list.length ? "" : "No guide data")); return; }
      var p = list[i];
      if (p[0] <= now) {
        on.appendChild(document.createTextNode(p[2] + " "));
        on.appendChild(el("span", "when", "until " + time(p[1])));
        p = list[i + 1];
      } else {
        on.appendChild(el("span", "none", "Nothing listed"));
      }
      if (p) { next.appendChild(document.createTextNode(p[2] + " ")); next.appendChild(el("span", "when", time(p[0]))); }
    });
    var age = (now - Date.parse(guide.generated) / 1000) / 3600;
    var note = card.querySelector(".guide-age");
    note.hidden = age < 8;
    note.textContent = "This is from the guide kia built " + Math.round(age) + " hours ago, so later shows may be missing. "
      + "The status page says when the guide was last updated.";
  }

  function buildTimeline() {
    var now = Date.now() / 1000;
    var start = Math.floor(now / 1800) * 1800, end = start + HOURS * 3600;
    var pct = function (t) { return (Math.min(Math.max(t, start), end) - start) / (end - start) * 100; };
    var wrap = el("div", "tl-wrap"), tl = el("div", "tl"), head = el("div", "tl-head"), hours = el("div", "tl-hours");
    head.appendChild(el("div", "tl-name", "Channel"));
    // Labels on the whole hours of the viewer's own clock.
    var tick = new Date(start * 1000); tick.setMinutes(0, 0, 0);
    if (tick.getTime() / 1000 < start) tick.setHours(tick.getHours() + 1);
    for (var t = tick.getTime() / 1000; t < end; t += 3600) {
      var h = el("span", null, time(t)); h.style.left = pct(t) + "%";
      hours.appendChild(h);
    }
    head.appendChild(hours); tl.appendChild(head);
    var lastBody = null;
    rows.forEach(function (row) {
      var head = row.parentNode.querySelector("tr.group-head");
      if (row.parentNode !== lastBody && head) {
        var g = el("div", "tl-group", head.textContent); g._head = head; g.hidden = head.hidden; tl.appendChild(g);
      }
      lastBody = row.parentNode;
      var line = el("div", "tl-row"), name = el("div", "tl-name"), track = el("div", "tl-track");
      var label = row.querySelector("td.name").textContent;
      name.appendChild(el("b", null, row.querySelector("td.num").textContent));
      name.appendChild(document.createTextNode(label));
      name.title = label;
      var shown = 0;
      slots(row).forEach(function (p) {
        if (p[1] <= start || p[0] >= end) return;
        var b = el("div", "tl-block" + (p[0] <= now && now < p[1] ? " on" : ""), p[2]);
        b.style.left = pct(p[0]) + "%"; b.style.width = (pct(p[1]) - pct(p[0])) + "%";
        b.title = p[2] + " (" + time(p[0]) + "–" + time(p[1]) + ")";
        track.appendChild(b); shown++;
      });
      if (!shown) track.appendChild(el("span", "tl-empty", "No guide data"));
      line.appendChild(name); line.appendChild(track);
      line.hidden = row.hidden;
      line._row = row;
      tl.appendChild(line);
    });
    var mark = el("div", "tl-now"); mark.style.left = "calc(11rem + (100% - 11rem) * " + (pct(now) / 100) + ")";
    tl.appendChild(mark);
    wrap.appendChild(tl);
    return wrap;
  }

  function show(which) {
    var list = which === "list";
    card.querySelectorAll(".view button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.view === which)); });
    table.parentNode.hidden = !list;
    if (timeline) timeline.remove();
    timeline = null;
    if (!list) { timeline = buildTimeline(); table.parentNode.after(timeline); }
  }

  fetch(root + "guide-preview.json", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
    if (!data || !data.channels) return;
    guide = data;
    card.querySelectorAll("th.now, th.next, td.now, td.next").forEach(function (c) { c.hidden = false; });
    var bar = card.querySelector(".view");
    bar.hidden = false;
    bar.querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.view); }); });
    fill();
    setInterval(function () { fill(); if (timeline) show("timeline"); }, 60000);
    // The filter box hides rows; keep the timeline in step.
    var box = card.querySelector("input[data-filter]");
    if (box) box.addEventListener("input", function () {
      if (timeline) {
        timeline.querySelectorAll(".tl-row").forEach(function (line) { line.hidden = line._row.hidden; });
        timeline.querySelectorAll(".tl-group").forEach(function (g) { g.hidden = g._head.hidden; });
      }
    });
  }).catch(function () {});
})();
</script>
"""
