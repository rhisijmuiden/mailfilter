"""Usage per customer for invoices: counting, prices, freezing months, who sees what, CSV."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from mailfilter import monthly, usage
from mailfilter.db import session
from mailfilter.models import Domain, TraceMessage, TraceRecipient, UsageDaily, UsageMonth

from conftest import PASSWORD, Client

NOW = datetime.now(timezone.utc)
THIS = monthly.month_of(NOW)
LAST = monthly.add_months(THIS, -1)


def at(m: date, day: int = 10) -> datetime:
    return monthly.bounds(m)[0] + timedelta(days=day - 1, hours=12)


def add(db, qid, status, when, rcpt, direction="inbound", sender="a@partner.nl", rstatus="sent"):
    m = TraceMessage(queue_id=qid, first_seen=when, last_seen=when, status=status, sender=sender, direction=direction, symbols={})
    m.recipients = [TraceRecipient(queue_id=qid, address=r, status=rstatus) for r in (rcpt if isinstance(rcpt, list) else [rcpt])]
    db.add(m)


@pytest.fixture
def setup(admin):
    """klant.nl belongs to Klant BV (through reseller Partner, 20% off), rhis.nl to RHIS (direct)."""
    rid = admin.post("/api/billing/resellers", json={"name": "Partner", "ref": "R-1", "discount_percent": 20}).json()["resellers"][0]["id"]
    admin.post("/api/billing/customers", json={"name": "Klant BV", "ref": "D-1011", "reseller_id": rid, "domains": ["klant.nl"]})
    admin.post("/api/billing/customers", json={"name": "RHIS", "ref": "intern", "domains": ["rhis.nl"]})
    assert admin.put("/api/billing/tariffs", json={"per_domain": 1, "per_mailbox": 0.75, "per_1000_out": 0.10,
                                                   "minimum": 5}).status_code == 200
    with session() as db:
        w = at(LAST)
        add(db, "A1", "delivered", w, ["a@klant.nl", "B@klant.nl"])
        add(db, "A2", "delivered", w, "a@klant.nl")  # same address: one mailbox
        add(db, "S1", "quarantined", w, "c@klant.nl", rstatus="quarantined")  # only spam: not a mailbox
        add(db, "R1", "rejected", w, "d@klant.nl", rstatus="rejected")
        add(db, "O1", "delivered", w, "x@gmail.com", direction="outbound", sender="shop@klant.nl")
        add(db, "O2", "delivered", w, "y@gmail.com", direction="outbound", sender="shop@klant.nl")
        add(db, "J1", "delivered", w, "jan@rhis.nl")
        add(db, "P1", "delivered", at(monthly.add_months(LAST, -1)), "old@klant.nl")  # month before
        db.commit()
    return {"reseller": rid}


def by_name(r):
    return {c["name"]: c for c in r["customers"]}




def test_admin_sees_usage_and_amounts(admin, setup):
    r = admin.get(f"/api/usage?month={LAST:%Y-%m}").json()
    assert r["prices"] is True and r["stored"] is False and r["currency"] == "EUR"
    k, h = by_name(r)["Klant BV"], by_name(r)["RHIS"]
    assert (k["mailboxes"], k["mail_in"], k["mail_out"]) == (3, 4, 2)  # a, b, shop
    assert [d["domain"] for d in k["domains"]] == ["klant.nl"]
    # 1 domain x 1.00 + 3 mailboxes x 0.75 + 0.002 x 0.10 = 3.25 -> minimum 5, then 20% reseller discount
    codes = [ln["code"] for ln in k["lines"]]
    assert codes == ["domains", "mailboxes", "mail_out", "minimum", "discount"]
    assert k["amount"] == 4.0 and k["reseller"] == "Partner"
    assert h["mailboxes"] == 1 and h["amount"] == 5.0
    assert r["unassigned"] == []


def test_customer_tariffs_and_fixed_price(admin, setup):
    cid = by_name(admin.get("/api/usage").json())["RHIS"]["id"]
    admin.put(f"/api/billing/customers/{cid}", json={"name": "RHIS", "domains": ["rhis.nl"], "tariffs": {"fixed": 12.5}})
    h = by_name(admin.get(f"/api/usage?month={LAST:%Y-%m}").json())["RHIS"]
    assert [ln["code"] for ln in h["lines"]] == ["fixed"] and h["amount"] == 12.5


def test_freeze_month_survives_moves_and_trace_pruning(admin, setup):
    with session() as db:
        assert LAST in usage.store_finished(db, NOW)
        db.commit()
        assert usage.store_finished(db, NOW) == []
    # Move klant.nl to RHIS and drop the trace: the frozen month does not change.
    cid = by_name(admin.get("/api/usage").json())["RHIS"]["id"]
    admin.put(f"/api/billing/customers/{cid}", json={"name": "RHIS", "domains": ["rhis.nl", "klant.nl"]})
    with session() as db:
        db.query(TraceMessage).delete()
        db.commit()
    r = admin.get(f"/api/usage?month={LAST:%Y-%m}").json()
    assert r["stored"] is True and r["can_recount"] is True
    assert by_name(r)["Klant BV"]["mailboxes"] == 3 and by_name(r)["Klant BV"]["mail_in"] == 4
    # Recalculate: today's customers; mailboxes counted again only while the trace covers the month
    r = admin.post("/api/usage/recalculate", json={"month": f"{LAST:%Y-%m}"}).json()
    assert set(by_name(r)) == {"RHIS"}
    assert admin.post("/api/usage/recalculate", json={"month": f"{THIS:%Y-%m}"}).status_code == 409


def test_snapshot_records_domains_and_archive(admin, setup, monkeypatch):
    monkeypatch.setattr(usage, "archive_usage", lambda db: {"klant.nl": (3_500_000_000, 1200)})
    with session() as db:
        usage.snapshot(db, NOW.date())
        usage.snapshot(db, NOW.date())  # twice a day: still one row
        db.commit()
        rows = db.execute(select(UsageDaily).where(UsageDaily.domain == "klant.nl")).scalars().all()
        assert len(rows) == 1 and rows[0].archive_bytes == 3_500_000_000 and rows[0].customer_id is not None
    admin.put("/api/billing/tariffs", json={"per_gb_archive": 0.25})
    k = by_name(admin.get(f"/api/usage?month={THIS:%Y-%m}").json())["Klant BV"]
    assert k["archive_bytes"] == 3_500_000_000
    assert [ln for ln in k["lines"] if ln["code"] == "archive"][0]["quantity"] == 3.5


def test_reseller_sees_only_its_customers(admin, setup, client):
    r = admin.post("/api/users", json={"email": "p@partner.nl", "role": "reseller", "reseller_id": setup["reseller"],
                                       "password": PASSWORD, "domains": ["rhis.nl"]})
    assert r.status_code == 201 and r.json()["domains"] == [] and r.json()["reseller_id"] == setup["reseller"]
    assert admin.post("/api/users", json={"email": "q@partner.nl", "role": "reseller"}).status_code == 400
    p = Client().login("p@partner.nl")
    assert p.get("/api/auth/me").json()["reseller"] == "Partner"
    r = p.get(f"/api/usage?month={LAST:%Y-%m}").json()
    assert list(by_name(r)) == ["Klant BV"] and by_name(r)["Klant BV"]["amount"] == 4.0
    assert [d["domain"] for d in r["domains"]] == ["klant.nl"] and r["unassigned"] == []
    assert p.get("/api/billing").status_code == 403
    assert p.get("/api/trace").json().get("items", []) == []  # no domains of its own


def test_customer_sees_quantities_without_prices(customer, setup):
    r = customer.get(f"/api/usage?month={LAST:%Y-%m}").json()
    assert r["prices"] is False and list(by_name(r)) == ["Klant BV"]
    k = by_name(r)["Klant BV"]
    assert k["mailboxes"] == 3 and k["amount"] is None and k["lines"] == []
    assert customer.get(f"/api/usage/export?month={LAST:%Y-%m}&kind=lines").status_code == 403
    csv = customer.get(f"/api/usage/export?month={LAST:%Y-%m}").text
    assert "bedrag" not in csv and "Klant BV" in csv
    assert customer.post("/api/usage/recalculate", json={"month": f"{LAST:%Y-%m}"}).status_code == 403


def test_csv_export(admin, setup):
    r = admin.get(f"/api/usage/export?month={LAST:%Y-%m}&kind=customers&lang=nl")
    assert r.headers["content-type"].startswith("text/csv")
    assert f"usage-{LAST:%Y-%m}-customers.csv" in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("maand;debiteurnummer;klant;reseller")
    assert f"{LAST:%Y-%m};D-1011;Klant BV;Partner;R-1;1;3;4;1;2;0,00;4,00;EUR" in lines
    r = admin.get(f"/api/usage/export?month={LAST:%Y-%m}&kind=lines&sep=,&lang=en&reseller=direct")
    rows = r.text.lstrip("﻿").splitlines()
    assert rows[0].startswith("month,customer_number,customer") and all("Klant BV" not in x for x in rows)
    assert any("Top-up to minimum" in x for x in rows)
    r = admin.get(f"/api/usage/export?month={LAST:%Y-%m}&kind=domains")
    assert "klant.nl" in r.text and "rhis.nl" in r.text


def test_billing_customers_and_resellers(admin, setup):
    b = admin.get("/api/billing").json()
    assert b["unassigned"] == [] and float(b["tariffs"]["per_mailbox"]) == 0.75
    klant = [c for c in b["customers"] if c["name"] == "Klant BV"][0]
    assert klant["logins"] == 1 and b["resellers"][0]["customers"] == 1
    assert admin.post("/api/billing/customers", json={"name": "klant bv"}).status_code == 409
    assert admin.post("/api/billing/customers", json={"name": "X", "domains": ["nope.nl"]}).status_code == 400
    # A domain has one payer: giving it to another customer takes it away from the first.
    b = admin.post("/api/billing/customers", json={"name": "Nieuw", "domains": ["klant.nl"]}).json()
    assert [c["domains"] for c in b["customers"] if c["name"] == "Klant BV"] == [[]]
    # Deleting a reseller makes its customers direct; deleting a customer frees its domains.
    b = admin.delete(f"/api/billing/resellers/{setup['reseller']}").json()
    assert all(c["reseller_id"] is None for c in b["customers"])
    nid = [c["id"] for c in b["customers"] if c["name"] == "Nieuw"][0]
    assert "klant.nl" in admin.delete(f"/api/billing/customers/{nid}").json()["unassigned"]


def test_unassigned_domain_shows_in_totals_not_on_invoice(admin, setup):
    with session() as db:
        db.add(Domain(name="los.nl"))
        db.commit()
    r = admin.get(f"/api/usage?month={THIS:%Y-%m}").json()
    assert "los.nl" in r["unassigned"] and "los.nl" in [d["domain"] for d in r["domains"]]


def test_hourly_run_freezes_last_month(admin, setup):
    with session() as db:
        assert f"{LAST:%Y-%m}" in usage.run(db, NOW)
        assert db.execute(select(UsageMonth).where(UsageMonth.month == LAST)).scalars().all()
