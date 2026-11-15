// Applies the saved light/dark choice and own branding colours before the app
// renders (no flash). See src/components/theme.ts and src/lib/branding.ts.
try { var t = localStorage.getItem("mf-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
try {
  var c = localStorage.getItem("mf-brand-css");
  if (c) { var s = document.createElement("style"); s.id = "mf-brand"; s.textContent = c; document.head.appendChild(s); }
} catch (e) {}
