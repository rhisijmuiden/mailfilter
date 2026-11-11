"""Build prototype/index.html: inlines the self-hosted Archivo font so the page works as one file."""
import base64, pathlib
root = pathlib.Path(__file__).resolve().parent
font = (root.parent / "huisstijl/fonts/archivo/archivo-latin.woff2").read_bytes()
src = (root / "src/index.template.html").read_text(encoding="utf-8")
out = src.replace("__ARCHIVO_WOFF2__", "data:font/woff2;base64," + base64.b64encode(font).decode())
(root / "index.html").write_text(out, encoding="utf-8")
print("wrote", root / "index.html", len(out), "bytes")
