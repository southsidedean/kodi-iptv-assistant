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
.tl-wrap { overflow: auto; max-height: 75vh; -webkit-overflow-scrolling: touch; margin-top: var(--space-2); overscroll-behavior: contain; }
.tl { --tl-name: 11rem; min-width: 46rem; position: relative; }
/* Phones: three hours that fit the screen, and a narrower name column. */
@media (max-width: 40rem) { .tl { --tl-name: 6.5rem; min-width: 0; } }
.tl-head, .tl-row { display: grid; grid-template-columns: var(--tl-name) 1fr; align-items: stretch; }
.tl-head { position: sticky; top: 0; background: var(--card); z-index: 3; border-bottom: 1px solid var(--line); }
.tl-hours { position: relative; height: 1.8rem; }
.tl-hours span { position: absolute; top: .3rem; font-size: var(--step--1); color: var(--muted); transform: translateX(-50%); white-space: nowrap; }
.tl-row { border-bottom: 1px solid var(--line); min-height: 2.6rem; }
.tl-row[hidden], .tl-group[hidden] { display: none; }
.tl-group { font-weight: 600; padding: .6rem 0 .3rem; border-bottom: 1px solid var(--line); }
.tl-name { position: sticky; left: 0; z-index: 2; background: var(--card); padding: .45rem .5rem .45rem 0; font-size: var(--step--1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-name b { font-variant-numeric: tabular-nums; color: var(--muted); font-weight: 500; margin-right: .35rem; }
.tl-track { position: relative; overflow: hidden; cursor: pointer; }  /* a show starting at the very end stays inside */
.tl-block { position: absolute; top: .25rem; bottom: .25rem; background: var(--info-bg); border-radius: 6px; padding: .2rem .4rem;
  font: inherit; font-size: var(--step--1); color: var(--fg); text-align: left; min-height: 0; cursor: pointer;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis; border: 0; border-left: 2px solid var(--card); }
.tl-block[aria-expanded=true] { outline: 2px solid var(--info); outline-offset: -2px; }
.tl-detail { position: relative; z-index: 2; background: var(--card); padding: .4rem 0 .5rem var(--tl-name); font-size: var(--step--1); border-bottom: 1px solid var(--line); }
.tl-detail[hidden] { display: none; }
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
  var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr[data-key]"));
  var guide = null, timeline = null;
  var phone = window.matchMedia("(max-width: 40rem)");

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
      + "The Dashboard says when the guide was last updated.";
  }

  // The timeline: rows for every channel, but show blocks only for the rows on screen or
  // about to be (a playlist can have 1,500 channels); the rest fill in as they scroll in.
  // Each minute only the "now" line and the shows on now change; the half-hour window
  // moves every 30 minutes, and then it's rebuilt with focus and scroll kept.
  function buildTimeline() {
    var now = Date.now() / 1000;
    var hoursShown = phone.matches ? 3 : 6;  // a phone shows three hours, without scrolling sideways
    var start = Math.floor(now / 1800) * 1800, end = start + hoursShown * 3600;
    var pct = function (t) { return (Math.min(Math.max(t, start), end) - start) / (end - start) * 100; };
    var wrap = el("div", "tl-wrap"), tl = el("div", "tl"), head = el("div", "tl-head"), hours = el("div", "tl-hours");
    head.appendChild(el("div", "tl-name", "Channel"));
    // Labels on the whole hours of the viewer's own clock.
    var tick = new Date(start * 1000); tick.setMinutes(0, 0, 0);
    if (tick.getTime() / 1000 < start) tick.setHours(tick.getHours() + 1);
    for (var t = tick.getTime() / 1000; t < end; t += 3600) {
      var h = el("span", null, new Date(t * 1000).toLocaleTimeString([], { hour: "numeric" })); h.style.left = pct(t) + "%";
      if (t === start) h.style.transform = "none";  // centred on the edge, half of it would hide behind the names
      hours.appendChild(h);
    }
    head.appendChild(hours); tl.appendChild(head);
    var lastBody = null;
    // A show's title and times, under its channel, when it's tapped or clicked (it's often cut short).
    var detail = el("div", "tl-detail"); detail.hidden = true; detail.setAttribute("role", "status");
    var state = { start: start, end: end, detail: detail, open: null, blocks: [] };
    var seen = "IntersectionObserver" in window ? new IntersectionObserver(function (list) {
      list.forEach(function (e) { if (e.isIntersecting) { fillTrack(e.target); seen.unobserve(e.target); } });
    }, { root: wrap, rootMargin: "400px 0px" }) : null;

    function fillTrack(line) {
      if (line._filled || line.hidden) return;
      line._filled = true;
      var row = line._row, track = line._track, label = line._label, shown = 0;
      slots(row).forEach(function (p) {
        if (p[1] <= start || p[0] >= end) return;
        var b = el("button", "tl-block" + (p[0] <= now && now < p[1] ? " on" : ""), p[2]);
        b.type = "button"; b.setAttribute("aria-expanded", "false");
        b.style.left = pct(p[0]) + "%"; b.style.width = (pct(p[1]) - pct(p[0])) + "%";
        b._p = p; b._key = row.dataset.key + "|" + p[0];
        b._about = p[2] + ", " + time(p[0]) + " to " + time(p[1]) + (p[2] === label ? "" : ", on " + label);
        b.title = b._about;
        b.addEventListener("click", function () { toggle(b, line); });
        track.appendChild(b); state.blocks.push(b); shown++;
      });
      if (!shown) track.appendChild(el("span", "tl-empty", "No guide data"));
    }
    function toggle(b, line) {
      if (state.open) state.open.setAttribute("aria-expanded", "false");
      if (state.open === b) { state.open = null; detail.hidden = true; return; }
      state.open = b; b.setAttribute("aria-expanded", "true");
      detail.textContent = b._about; detail.hidden = false; line.after(detail);
    }
    function markOn(at) {
      state.blocks.forEach(function (b) { b.classList.toggle("on", b._p[0] <= at && at < b._p[1]); });
    }

    rows.forEach(function (row) {
      var head = row.parentNode.querySelector("tr.group-head");
      if (row.parentNode !== lastBody && head) {
        var g = el("div", "tl-group", head.textContent); g._head = head; g.hidden = head.hidden; tl.appendChild(g);
      }
      lastBody = row.parentNode;
      var line = el("div", "tl-row"), name = el("div", "tl-name"), track = el("div", "tl-track");
      var label = row.querySelector("td.name").firstChild.textContent;
      name.appendChild(el("b", null, row.querySelector("td.num").textContent));
      name.appendChild(document.createTextNode(label));
      name.title = label;
      // A tap anywhere in the row's height opens the show at that time: blocks are small on phones.
      track.addEventListener("click", function (ev) {
        if (ev.target !== track) return;
        var r = track.getBoundingClientRect(), at = start + (ev.clientX - r.left) / r.width * (end - start);
        var b = Array.prototype.find.call(track.querySelectorAll(".tl-block"), function (x) { return x._p[0] <= at && at < x._p[1]; });
        if (b) { b.focus(); toggle(b, line); }
      });
      line.appendChild(name); line.appendChild(track);
      line.hidden = row.hidden;
      line._row = row; line._track = track; line._label = label;
      tl.appendChild(line);
      if (seen) seen.observe(line); else fillTrack(line);
    });
    var mark = el("div", "tl-now");
    tl.appendChild(mark);
    wrap.appendChild(tl);
    state.fillRow = function (key) {  // now, not when the observer gets to it
      tl.querySelectorAll(".tl-row").forEach(function (line) { if (line._row.dataset.key === key) fillTrack(line); });
    };
    state.fill = function () { if (!seen) tl.querySelectorAll(".tl-row").forEach(fillTrack); };  // else the observer does it
    state.update = function (at) {
      mark.style.left = "calc(var(--tl-name) + (100% - var(--tl-name)) * " + (pct(at) / 100) + ")";
      markOn(at);
    };
    state.update(now);
    wrap._state = state;
    return wrap;
  }

  // Rebuild the timeline (a new half hour, or a phone turned sideways), keeping where the
  // viewer was: the scroll position, the focused show, and the one that's open.
  function rebuild() {
    var old = timeline._state, focused = document.activeElement;
    var keep = { left: timeline.scrollLeft, top: timeline.scrollTop, focus: focused && focused._key, open: old.open && old.open._key };
    var fresh = buildTimeline();
    [keep.focus, keep.open].forEach(function (k) { if (k) fresh._state.fillRow(k.slice(0, k.lastIndexOf("|"))); });
    timeline.replaceWith(fresh); timeline = fresh;
    timeline.scrollLeft = keep.left; timeline.scrollTop = keep.top;
    timeline._state.blocks.forEach(function (b) {
      if (b._key === keep.open) b.click();
      if (b._key === keep.focus) b.focus({ preventScroll: true });
    });
  }

  function show(which) {
    var list = which === "list";
    card.querySelectorAll(".view button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.view === which)); });
    table.parentNode.hidden = !list;
    if (timeline) timeline.remove();
    timeline = null;
    if (!list) { timeline = buildTimeline(); table.parentNode.after(timeline); }
  }

  function everyMinute() {
    fill();
    if (!timeline) return;
    var now = Date.now() / 1000;
    if (now >= timeline._state.start + 1800) rebuild();
    else timeline._state.update(now);
  }

  fetch(root + "guide-preview.json", { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
    if (!data || !data.channels) {
      var note = card.querySelector(".guide-age");
      if (note && rows.length) { note.hidden = false; note.textContent = "What's on shows here once kia has built the guide, after its first refresh."; }
      return;
    }
    guide = data;
    card.querySelectorAll("th.now, th.next, td.now, td.next").forEach(function (c) { c.hidden = false; });
    var bar = card.querySelector(".view");
    bar.hidden = false;
    bar.querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.view); }); });
    fill();
    setInterval(everyMinute, 60000);
    phone.addEventListener("change", function () { if (timeline) rebuild(); });
    // The list's controls hide rows (the playlist page's LIST_SCRIPT); keep the timeline in step.
    card.addEventListener("rows-changed", function () {
      if (timeline) {
        timeline.querySelectorAll(".tl-row").forEach(function (line) { line.hidden = line._row.hidden; });
        timeline.querySelectorAll(".tl-group").forEach(function (g) { g.hidden = g._head.hidden; });
        timeline._state.fill();  // shows for the rows that just appeared
        var d = timeline.querySelector(".tl-detail");
        if (d && d.previousElementSibling && d.previousElementSibling.hidden) d.hidden = true;
      }
    });
  }).catch(function () {});
})();
</script>
"""
