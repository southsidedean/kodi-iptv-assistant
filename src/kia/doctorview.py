"""kia doctor on the Dashboard: "Ask kia what's wrong" by the banner, and Why? by each channel.

Both ask kia's API (GET /api/doctor, /api/doctor/channel?q=) and show the answers under the
button. The answers quote third-party text (channel names, a guide site's errors), so every
string goes into the page with textContent, never as HTML, and a "what to do" becomes a
link only when it's one of kia's own docs pages.
"""

from kia.htmlpage import DOCS_URL

STYLE = """
/* kia doctor's answers */
.why-answer { margin: var(--space-2) 0 0; }
.why-answer ul { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
.why-answer li { border-left: 4px solid var(--muted); padding-left: var(--space-2); }
.why-answer li.why-problem { border-color: var(--bad); }
.why-answer li.why-note { border-color: var(--warn); }
.why-answer li.why-ok { border-color: var(--good); }
.why-answer p { margin: .2rem 0 0; }
.why-answer .why-subject { font-weight: 600; margin-top: var(--space-2); }
"""

SCRIPT = r"""
<script>
window.kiaWhy = (function () {
  var DOCS = "__DOCS_URL__";
  var LABEL = { problem: "Problem", note: "Note", ok: "OK" };
  // Only kia's own docs pages become links; anything else stays text.
  var DOC_PATH = /^docs\/[\w\/.-]+\.md(#[\w-]+)?$/;
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function show(reports, into) {
    into.textContent = "";
    reports.forEach(function (r) {
      if (reports.length > 1) into.appendChild(el("p", "why-subject", r.subject));
      var list = el("ul");
      r.findings.forEach(function (f) {
        var level = LABEL[f.level] ? f.level : "note";
        var li = el("li", "why-" + level);
        li.appendChild(el("strong", null, LABEL[level] + ": " + f.title));
        if (f.detail) li.appendChild(el("p", null, f.detail));
        if (f.fix) {
          var p = el("p", null, "What to do: ");
          if (DOC_PATH.test(f.fix)) {
            var a = el("a", null, "how to fix it");
            a.href = DOCS + f.fix.slice(4); a.target = "_blank"; a.rel = "noopener";
            p.appendChild(a);
          } else p.appendChild(document.createTextNode(f.fix));
          li.appendChild(p);
        }
        if (f.evidence && f.evidence.length) li.appendChild(el("p", "muted", "From: " + f.evidence.join(", ")));
        list.appendChild(li);
      });
      into.appendChild(list);
    });
  }
  // query: null for the overview, or a channel's ID or name. into: where the answer goes.
  return function (root, query, into) {
    into.hidden = false;
    into.textContent = "Asking kia…";
    var path = root + "api/doctor" + (query == null ? "" : "/channel?q=" + encodeURIComponent(query));
    return fetch(path).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok || !j.reports) { into.textContent = j.error || "kia couldn't answer."; return; }
        show(j.reports, into);
      });
    }, function () { into.textContent = "kia didn't answer. Is it running?"; });
  };
})();
</script>
""".replace("__DOCS_URL__", DOCS_URL)
