// Applies the saved light/dark choice before the app renders (no flash).
try { var t = localStorage.getItem("mf-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
