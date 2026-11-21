/* Shared light/dark preference for the lab dashboard and the world map.
 *
 * Load it in <head> (before the stylesheet paints) so the right theme shows at once:
 *   <script src="/static/theme.js" data-default="dark"></script>
 *
 * Preference: "system" | "light" | "dark", kept in this browser only (localStorage).
 * "light"/"dark" set <html data-theme="…">; "system" removes it so the CSS
 * prefers-color-scheme rules decide. A page with no saved preference uses its own
 * data-default (the map is dark by default, the lab follows the system).
 * Changes fire a "chonkvid-theme" event on window, also across open tabs.
 */
(function () {
  var KEY = "chonkvid-theme";
  var script = document.currentScript;
  var fallback = (script && script.getAttribute("data-default")) || "system";

  function read() {
    try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; }
  }
  function apply(pref) {
    var p = pref || fallback;
    var root = document.documentElement;
    if (p === "light" || p === "dark") root.setAttribute("data-theme", p);
    else root.removeAttribute("data-theme");
  }
  function notify() {
    try { window.dispatchEvent(new CustomEvent("chonkvid-theme", { detail: { pref: current() } })); } catch (e) { /* old browser */ }
  }
  function current() { return read() || fallback; }

  window.ChonkTheme = {
    current: current,
    effective: function () {
      var t = document.documentElement.getAttribute("data-theme");
      if (t) return t;
      return window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    },
    set: function (pref) {
      try { localStorage.setItem(KEY, pref); } catch (e) { /* private mode: applies to this page only */ }
      apply(pref);
      notify();
    },
  };

  apply(read());
  window.addEventListener("storage", function (e) { if (e.key === KEY) { apply(e.newValue); notify(); } });
  if (window.matchMedia) {
    var mq = matchMedia("(prefers-color-scheme: light)");
    var onSystem = function () { if (current() === "system") notify(); };
    if (mq.addEventListener) mq.addEventListener("change", onSystem); else if (mq.addListener) mq.addListener(onSystem);
  }
})();
