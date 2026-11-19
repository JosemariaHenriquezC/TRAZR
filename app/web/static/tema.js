// Botón de tema del panel: Automático (como el dispositivo) → Claro → Oscuro.
// La elección se guarda en este navegador; el <head> ya la aplicó antes de pintar.
(function () {
  var KEY = "trazr-tema";
  var ORDER = ["auto", "light", "dark"];
  var LABEL = { auto: "Automático", light: "Claro", dark: "Oscuro" };
  var root = document.documentElement;

  function current() { return root.dataset.theme || "auto"; }

  function paint() {
    var t = current();
    document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
      b.dataset.state = t;
      var next = ORDER[(ORDER.indexOf(t) + 1) % ORDER.length];
      b.setAttribute("aria-label", "Tema: " + LABEL[t] + ". Cambiar a " + LABEL[next]);
      b.title = "Tema: " + LABEL[t];
    });
  }

  function cycle() {
    var t = ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length];
    if (t === "auto") { delete root.dataset.theme; } else { root.dataset.theme = t; }
    try { if (t === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch (err) {}
    paint();
  }

  document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
    b.addEventListener("click", function (e) { e.preventDefault(); cycle(); });
  });
  paint();
})();
