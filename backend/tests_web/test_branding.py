import base64

from mailfilter import branding, digest
from mailfilter.api import links

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


def test_defaults_are_the_house_style(client):
    r = client.get("/api/branding")
    assert r.status_code == 200
    b = r.json()
    assert (b["name"], b["accent"], b["primary"]) == ("Mailfilter", "#FF6B57", "#14213D")
    assert b["logo"] is None and b["custom"] is False


def test_admin_sets_branding_and_logo(admin, customer, client):
    body = {"name": "  Acme   Mail ", "accent": "#00aa88", "primary": "#112233"}
    assert customer.put("/api/branding", json=body).status_code == 403
    r = admin.put("/api/branding", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Acme Mail" and r.json()["accent"] == "#00AA88" and r.json()["custom"] is True
    assert admin.put("/api/branding", json={**body, "accent": "red"}).status_code == 422

    r = admin.put("/api/branding/logo", json={"data": "data:image/png;base64," + base64.b64encode(PNG).decode()})
    assert r.status_code == 200, r.text
    url = r.json()["logo"]
    assert url.startswith("/api/branding/logo?v=")
    img = client.get(url)  # public, also without login
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"

    r = admin.put("/api/branding/logo_dark", json={"data": base64.b64encode(SVG).decode()})
    img = client.get(r.json()["logo_dark"])
    assert img.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in img.headers["content-security-policy"]

    # Not an image, or too big
    assert admin.put("/api/branding/favicon", json={"data": base64.b64encode(b"<html>hi</html>").decode()}).json()["detail"] == "bad_image"
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * branding.MAX_IMAGE).decode()
    assert admin.put("/api/branding/favicon", json={"data": big}).json()["detail"] == "too_large"
    assert admin.put("/api/branding/other", json={"data": base64.b64encode(PNG).decode()}).status_code == 422

    assert admin.delete("/api/branding/logo").json()["logo"] is None
    r = admin.post("/api/branding/reset")
    assert r.json()["custom"] is False and r.json()["logo_dark"] is None


def test_branding_in_mails_and_link_pages(admin):
    admin.put("/api/branding", json={"name": "Acme Mail", "accent": "#00AA88", "primary": "#FFFFFF"})
    rep = digest.Report("a@klant.nl")
    _, _, html = digest.render(rep, "en", brand=branding.current())
    assert "Acme Mail" in html and "#00AA88" in html
    # White header gets dark text
    assert "background:#FFFFFF;color:#14213D" in html

    page = links.page("en", "warn", url="https://example.com").body.decode()
    assert "Acme Mail" in page and "Powered by <b>RHIS</b>" in page and "--koraal:#00AA88" in page


def test_powered_by_is_not_a_setting(admin):
    # There is no way to switch the mark off: unknown fields are ignored.
    r = admin.put("/api/branding", json={"name": "X", "accent": "#000000", "primary": "#000000", "powered_by": False})
    assert r.status_code == 200 and "powered_by" not in r.json()
    assert "Powered by" in links.page("nl", "block").body.decode()
