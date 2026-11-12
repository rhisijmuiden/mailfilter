"""Link protection: the click page (public, at /l/<token>) and the list of
clicks with the "block this domain" action (/api/links).

The click page needs no login: whoever has the mail may open its links. A
link only works with a valid signature (see mailfilter.links)."""

from __future__ import annotations

import html
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from mailfilter_config.rules import effective

from .. import links as lp
from ..audit import audit
from ..db import session
from ..deps import DB, AdminUser, CurrentUser, client_ip, visible_domains
from ..gateway import RULES_KEY, apply_changes, custom_rules
from ..models import Domain, DomainRoute, LinkClick
from ..settings_store import set_setting

public = APIRouter(tags=["links"])
router = APIRouter(prefix="/links", tags=["links"])

RULE = "link_protection"
KEEP_DAYS = 180
SCANNER_UA = ("bot", "crawler", "spider", "preview", "scanner", "curl", "wget", "python-requests", "python-httpx",
              "go-http-client", "java/", "okhttp", "libwww", "headless", "barracuda", "mimecast", "proofpoint",
              "safelinks", "microsoft office", "ms-office", "skypeuripreview", "facebookexternalhit", "slackbot")
LOGO = """<svg viewBox="0 0 3074 1092" role="img" aria-label="RHIS"><path fill="currentColor" d="M0 1080V392H572Q647 392 696.0 423.0Q745 454 770.0 504.5Q795 555 795 615Q795 680 764.0 733.5Q733 787 671 818L811 1080H581L467 851H203V1080ZM203 701H505Q542 701 564.0 679.0Q586 657 586 620Q586 596 576.0 578.5Q566 561 548.0 552.0Q530 543 505 543H203ZM942 1080V392H1145V648H1558V392H1761V1080H1558V812H1145V1080ZM2703 1092Q2619 1092 2546.0 1082.0Q2473 1072 2418.0 1047.0Q2363 1022 2332.0 977.0Q2301 932 2301 863Q2301 861 2301.0 858.0Q2301 855 2302 852H2502Q2501 856 2500.5 860.0Q2500 864 2500 869Q2500 900 2523.5 917.5Q2547 935 2589.0 942.0Q2631 949 2688 949Q2711 949 2735.5 947.5Q2760 946 2783.0 942.5Q2806 939 2824.5 932.0Q2843 925 2853.5 914.5Q2864 904 2864 888Q2864 867 2841.5 854.5Q2819 842 2779.5 834.5Q2740 827 2691.5 821.0Q2643 815 2591.0 806.5Q2539 798 2490.0 783.5Q2441 769 2402.0 745.0Q2363 721 2340.0 683.0Q2317 645 2317 591Q2317 536 2344.5 496.5Q2372 457 2422.0 431.0Q2472 405 2540.0 392.5Q2608 380 2688 380Q2770 380 2836.5 393.0Q2903 406 2950.5 431.5Q2998 457 3023.5 495.5Q3049 534 3049 586V600H2852V592Q2852 571 2834.0 554.5Q2816 538 2781.5 528.5Q2747 519 2698 519Q2639 519 2601.0 526.5Q2563 534 2545.0 546.5Q2527 559 2527 575Q2527 595 2550.0 606.5Q2573 618 2612.0 625.0Q2651 632 2699.5 638.0Q2748 644 2800.5 652.0Q2853 660 2901.5 674.0Q2950 688 2989.0 711.5Q3028 735 3051.0 771.5Q3074 808 3074 861Q3074 944 3026.0 995.0Q2978 1046 2894.5 1069.0Q2811 1092 2703 1092Z"/><rect x="1941" y="392" width="215" height="688" rx="40" fill="#FF6B57"/><path d="M1942.4 215.9A150 150 0 0 1 2154.6 215.9 M1850.5 124.0A280 280 0 0 1 2246.5 124.0" fill="none" stroke="#FF6B57" stroke-width="75" stroke-linecap="round"/></svg>"""


# ---- settings -----------------------------------------------------------------------------
def _domains(db: Session) -> list[str]:
    return list(db.execute(select(Domain.name).join(DomainRoute, DomainRoute.domain_id == Domain.id)
                           .where(DomainRoute.kind == "transport")).scalars())


def check_settings(db: Session) -> tuple[lp.CheckSettings, bool]:
    """(check settings, may continue after a warning)"""
    rules = effective(custom_rules(db))
    opts = rules[RULE].options
    own = sorted(set(_domains(db)))
    look = rules["phishing_lookalike"]
    protected = own + list(look.options.get("extra") or []) if look.enabled or any(look.domains.values()) else []
    cfg = lp.CheckSettings(block=list(opts.get("block") or []), own=own, protected=protected,
                           other_tld=bool(look.options.get("other_tld", True)),
                           shorteners=bool(opts.get("shorteners", True)))
    return cfg, bool(opts.get("allow_continue", True))


# ---- notifications ------------------------------------------------------------------------
_sent: dict[tuple[str, str], float] = {}


def _notify(click_id: int, what: str) -> None:
    """Announce a blocked click or a click through a warning (once an hour per site)."""
    from ..notify import Alert, event

    db = session()
    try:
        c = db.get(LinkClick, click_id)
        if c is None or (c.host, what) in _sent and time.time() - _sent[(c.host, what)] < 3600:
            return
        _sent[(c.host, what)] = time.time()
        reasons_en = " ".join(lp.reason_text(r, "en") for r in c.reasons if r.get("level") != "safe")
        reasons_nl = " ".join(lp.reason_text(r, "nl") for r in c.reasons if r.get("level") != "safe")
        words = {"blocked": ("Dangerous link blocked", "Gevaarlijke link geblokkeerd"),
                 "continued": ("Warning ignored, link opened", "Waarschuwing genegeerd, link geopend")}[what]
        event(db, Alert("link_click", "link_click", f"link_click:{what}:{c.host}", "warning" if what == "blocked" else "critical",
                        {"host": c.host, "url": c.url[:300], "recipient": c.recipient or ", ".join(c.domains) or "?",
                         "what_en": words[0], "what_nl": words[1], "reasons_en": reasons_en, "reasons_nl": reasons_nl}))
        db.commit()
    except Exception:  # noqa: BLE001 - an alert must never break a click
        lp.log.exception("link click notification failed")
    finally:
        db.close()


def _later(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


# ---- the click page -----------------------------------------------------------------------
T = {
    "en": {
        "warn_title": "Be careful with this link", "block_title": "This link has been blocked",
        "bad_title": "This link does not work", "goes_to": "Goes to",
        "warn_intro": "Mailfilter checked this link from your e-mail just now and found something suspicious.",
        "block_intro": "Mailfilter stopped this link from your e-mail to protect you.",
        "bad_intro": "This link was not made by this Mailfilter, or it was changed. Open the e-mail again, or ask "
                     "your administrator.",
        "back": "Don't open it, go back", "back_only": "Go back", "continue": "I trust it, continue anyway",
        "doubt": "In doubt? Ask your administrator before you continue.",
        "mistake": "Think this is a mistake? Send this page to your administrator.", "ref": "Reference",
        "other": "Nederlands", "close": "You can close this tab now.",
    },
    "nl": {
        "warn_title": "Pas op met deze link", "block_title": "Deze link is geblokkeerd",
        "bad_title": "Deze link werkt niet", "goes_to": "Gaat naar",
        "warn_intro": "Mailfilter heeft deze link uit je e-mail zojuist gecontroleerd en iets verdachts gevonden.",
        "block_intro": "Mailfilter heeft deze link uit je e-mail tegengehouden om je te beschermen.",
        "bad_intro": "Deze link is niet door deze Mailfilter gemaakt, of hij is veranderd. Open de e-mail opnieuw "
                     "of vraag het je beheerder.",
        "back": "Niet openen, terug", "back_only": "Terug", "continue": "Ik vertrouw het, toch doorgaan",
        "doubt": "Twijfel je? Vraag het je beheerder voordat je doorgaat.",
        "mistake": "Denk je dat dit een vergissing is? Stuur deze pagina naar je beheerder.", "ref": "Kenmerk",
        "other": "English", "close": "Je kunt dit tabblad nu sluiten.",
    },
}

CSS = """
@font-face{font-family:Archivo;src:url(/fonts/archivo-latin.woff2) format("woff2");font-weight:100 900;font-stretch:62% 125%}
:root{--nacht:#14213D;--koraal:#FF6B57;--bg:#F6F7FB;--card:#FFFFFF;--ink:#14213D;--muted:#4F5B73;--line:#DCE1EB;
--warn:#A86B00;--warn-soft:#FBF0DA;--bad:#C23434;--bad-soft:#FBE5E5;--btn:#14213D;--btn-ink:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#0E1626;--card:#16213A;--ink:#EEF1F7;--muted:#A3AEC4;--line:#26324D;
--warn:#F2B640;--warn-soft:#3A2F17;--bad:#FF7A7A;--bad-soft:#3D1F27;--btn:#EEF1F7;--btn-ink:#14213D;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;background:var(--bg);color:var(--ink);
font:15px/1.55 Archivo,system-ui,-apple-system,"Segoe UI",sans-serif;padding:32px 16px;-webkit-font-smoothing:antialiased}
.logo{width:96px;color:var(--ink);margin:8px 0 24px}
.card{width:100%;max-width:520px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:32px 28px;text-align:center;
box-shadow:0 1px 2px rgba(20,33,61,.06),0 8px 24px rgba(20,33,61,.06)}
.mark{width:60px;height:60px;border-radius:50%;display:grid;place-items:center;margin:0 auto}
.mark svg{width:30px;height:30px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.warn{background:var(--warn-soft);color:var(--warn)}.block,.bad{background:var(--bad-soft);color:var(--bad)}
h1{font-stretch:115%;font-weight:800;font-size:24px;line-height:1.2;margin:16px 0 8px;letter-spacing:-.01em}
p{margin:0 0 4px;color:var(--muted)}
.dest{margin:20px 0 12px;padding:12px 14px;border:1px solid var(--line);border-radius:10px;text-align:left;word-break:break-all}
.dest span{display:block;font-size:12.5px;color:var(--muted)}
.dest b{font:600 13.5px/1.45 ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace}
ul{list-style:none;padding:0;margin:0 0 4px;display:flex;flex-direction:column;gap:6px;text-align:left}
li{padding:8px 12px;border-radius:8px;font-size:14px}
.actions{display:flex;flex-direction:column;align-items:center;gap:10px;margin-top:22px}
.btn{display:inline-block;border:0;border-radius:8px;padding:11px 20px;font:600 15px Archivo,system-ui,sans-serif;cursor:pointer;
background:var(--btn);color:var(--btn-ink);text-decoration:none}
.ghost{background:transparent;color:var(--ink);text-decoration:underline;font-weight:500;font-size:14px;padding:6px}
.foot{font-size:12.5px;color:var(--muted);margin-top:22px}
.foot a{color:inherit}
.accent{height:4px;width:48px;background:var(--koraal);border-radius:4px;margin:20px auto 0}
"""
ICONS = {
    "warn": '<svg viewBox="0 0 24 24"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
            '<path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
    "block": '<svg viewBox="0 0 24 24"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 '
             '1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m14.5 9.5-5 5"/>'
             '<path d="m9.5 9.5 5 5"/></svg>',
}
ICONS["bad"] = ICONS["block"]


def _lang(request: Request) -> str:
    q = request.query_params.get("lang")
    if q in T:
        return q
    for part in request.headers.get("accept-language", "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in T:
            return code
    return "en"


def page(lang: str, kind: str, *, url: str = "", reasons: list[dict] | None = None, token: str = "",
         click_id: int | None = None, can_continue: bool = False, status_code: int = 200) -> HTMLResponse:
    t = T[lang]
    e = html.escape
    other = "nl" if lang == "en" else "en"
    body = [f'<div class="mark {kind}">{ICONS[kind]}</div>',
            f'<h1>{e(t[kind + "_title"])}</h1>', f'<p>{e(t[kind + "_intro"])}</p>']
    if url:
        body.append(f'<div class="dest"><span>{e(t["goes_to"])}</span><b>{e(url)}</b></div>')
    items = [r for r in reasons or [] if r.get("level") != "safe" or r.get("code") == "shortener"]
    if items:
        body.append("<ul>" + "".join(
            f'<li class="{"block" if r.get("level") == "block" else "warn" if r.get("level") == "warn" else ""}">'
            f'{e(lp.reason_text(r, lang))}</li>' for r in items) + "</ul>")
    body.append('<div class="actions">')
    if kind == "warn" and can_continue:
        body.append(f'<a class="btn" href="?lang={lang}&amp;back=1">{e(t["back"])}</a>')
        body.append(f'<form method="post" action="/l/{e(token)}"><input type="hidden" name="click" value="{click_id or ""}">'
                    f'<button class="btn ghost" type="submit">{e(t["continue"])}</button></form>')
    else:
        body.append(f'<a class="btn" href="?lang={lang}&amp;back=1">{e(t["back_only"])}</a>')
    body.append("</div>")
    foot = t["doubt"] if kind == "warn" else t["mistake"]
    ref = f' · {e(t["ref"])} {click_id}' if click_id else ""
    body.append(f'<p class="foot">{e(foot)}{ref} · <a href="?lang={other}">{e(t["other"])}</a></p>')
    doc = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
           f'<meta name="referrer" content="no-referrer"><title>{e(t[kind + "_title"])} · Mailfilter</title>'
           f'<style>{CSS}</style></head><body><div class="logo">{LOGO}</div>'
           f'<main class="card">{"".join(body)}<div class="accent"></div></main></body></html>')
    return HTMLResponse(doc, status_code=status_code, headers={"Referrer-Policy": "no-referrer",
                                                                "X-Robots-Tag": "noindex"})


def _back(lang: str) -> HTMLResponse:
    # "Back" from a page opened from a mail program: there is no page to go back to.
    t = T[lang]
    doc = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mailfilter</title>'
           f'<style>{CSS}</style></head><body><div class="logo">{LOGO}</div><main class="card">'
           f'<h1>{html.escape(t["close"])}</h1><div class="accent"></div></main></body></html>')
    return HTMLResponse(doc)


def _is_scanner(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return request.method == "HEAD" or not ua or any(s in ua for s in SCANNER_UA)


def _go(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302, headers={"Referrer-Policy": "no-referrer"})


@public.api_route("/l/{token}", methods=["GET", "HEAD"], include_in_schema=False)
def click(token: str, request: Request, db: DB) -> Response:
    lang = _lang(request)
    if request.query_params.get("back"):
        return _back(lang)
    data = lp.read_token(token)
    if data is None:
        return page(lang, "bad", status_code=404)
    cfg, can_continue = check_settings(db)
    v = lp.check(data["u"], data["f"], cfg)
    delivered = data.get("t")
    row = LinkClick(url=data["u"][:4000], host=lp.host_of(data["u"]), final_url=v.url[:4000],
                    recipient=str(data.get("r") or "")[:320], domains=data["d"][:20],
                    message_id=str(data.get("m") or "")[:998], queue_id=str(data.get("q") or "")[:40],
                    delivered_at=datetime.fromtimestamp(delivered, timezone.utc) if isinstance(delivered, int) else None,
                    level=v.level, reasons=v.to_dict()["reasons"], scanner=_is_scanner(request),
                    ip=client_ip(request), user_agent=request.headers.get("user-agent", "")[:300])
    db.add(row)
    if random.random() < 0.01:
        db.execute(delete(LinkClick).where(LinkClick.at < datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)))
    db.commit()
    if v.level == "safe":
        return _go(v.url)
    if v.level == "block" and not row.scanner:
        _later(_notify, row.id, "blocked")
    return page(lang, v.level, url=v.url, reasons=v.to_dict()["reasons"], token=token, click_id=row.id,
                can_continue=can_continue and v.level == "warn")


@public.post("/l/{token}", include_in_schema=False)
async def click_continue(token: str, request: Request, db: DB) -> Response:
    """'Continue anyway' on the warning page."""
    lang = _lang(request)
    data = lp.read_token(token)
    if data is None:
        return page(lang, "bad", status_code=404)
    cfg, can_continue = check_settings(db)
    v = lp.check(data["u"], data["f"], cfg)
    if v.level == "block" or (v.level == "warn" and not can_continue):
        return page(lang, v.level, url=v.url, reasons=v.to_dict()["reasons"], token=token)
    form = parse_qs((await request.body()).decode("utf-8", "replace")[:200])
    try:
        click_id = int((form.get("click") or ["0"])[0])
    except ValueError:
        click_id = 0
    row = db.get(LinkClick, click_id) if click_id else None
    if row is not None and row.url == data["u"][:4000] and not row.continued:
        row.continued, row.continued_at = True, datetime.now(timezone.utc)
        db.commit()
        if v.level == "warn":
            _later(_notify, row.id, "continued")
    return _go(v.url)


# ---- the list of clicks -------------------------------------------------------------------
class ClickOut(BaseModel):
    id: int
    at: datetime
    url: str
    host: str
    org: str
    final_url: str
    recipient: str
    domains: list[str]
    message_id: str
    queue_id: str
    delivered_at: datetime | None
    level: str
    reasons: list[dict]
    continued: bool
    scanner: bool
    ip: str | None


class ClickPage(BaseModel):
    items: list[ClickOut]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]
    enabled: bool
    can_block: bool


def _scope(stmt, user):
    domains = visible_domains(user)
    if domains is None:
        return stmt
    return stmt.where(LinkClick.domains.overlap([d.lower() for d in domains] or ["-"]))


@router.get("", response_model=ClickPage)
def list_clicks(
    user: CurrentUser, db: DB,
    q: str | None = Query(default=None, max_length=200),
    level: Literal["all", "safe", "warn", "block", "continued"] = "all",
    scanners: bool = False,
    days: int = Query(default=30, ge=1, le=KEEP_DAYS),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> ClickPage:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = _scope(select(LinkClick).where(LinkClick.at >= since), user)
    if not scanners:
        base = base.where(LinkClick.scanner.is_(False))
    if q:
        like = f"%{q.strip()}%"
        base = base.where(or_(LinkClick.url.ilike(like), LinkClick.recipient.ilike(like), LinkClick.host.ilike(like),
                              LinkClick.message_id.ilike(like)))
    sub = base.subquery()
    counts = {k: n for k, n in db.execute(select(sub.c.level, func.count()).group_by(sub.c.level))}
    counts["continued"] = db.execute(select(func.count()).select_from(sub).where(sub.c.continued.is_(True))).scalar_one()
    stmt = base
    if level == "continued":
        stmt = stmt.where(LinkClick.continued.is_(True))
    elif level != "all":
        stmt = stmt.where(LinkClick.level == level)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.order_by(LinkClick.at.desc(), LinkClick.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars()
    rule = effective(custom_rules(db))[RULE]
    enabled = rule.enabled or any(rule.domains.values())
    return ClickPage(
        items=[ClickOut(id=r.id, at=r.at, url=r.url, host=r.host, org=lp.org_domain(r.host), final_url=r.final_url, recipient=r.recipient,
                        domains=r.domains or [], message_id=r.message_id, queue_id=r.queue_id,
                        delivered_at=r.delivered_at, level=r.level, reasons=r.reasons or [], continued=r.continued,
                        scanner=r.scanner, ip=str(r.ip) if r.ip and user.is_admin else None) for r in rows],
        total=total, page=page, page_size=page_size, counts={k: counts.get(k, 0) for k in ("safe", "warn", "block", "continued")},
        enabled=enabled, can_block=user.is_admin)


class BlockIn(BaseModel):
    domain: str = Field(max_length=253)


@router.post("/block")
def block_domain(body: BlockIn, request: Request, admin: AdminUser, db: DB) -> dict:
    """Add a domain to the block list of link protection; works for mail already delivered too."""
    domain = body.domain.strip().lower().rstrip(".")
    if not domain or lp.is_ip(domain):
        raise HTTPException(422, "invalid_domain")
    if domain in set(_domains(db)):
        raise HTTPException(422, "own_domain")
    chosen = custom_rules(db)
    rule = effective(chosen)[RULE]
    block = list(rule.options.get("block") or [])
    if domain not in block:
        block.append(domain)
    rule.options["block"] = block
    chosen[RULE] = rule
    from mailfilter_config.rules import problems
    problems(chosen, set(db.execute(select(Domain.name)).scalars()))
    set_setting(db, RULES_KEY, {k: v.__dict__ for k, v in chosen.items()})
    apply_changes(db)
    audit(db, request, admin, "link_domain_blocked", domain)
    db.commit()
    return {"block": rule.options["block"]}


class TestIn(BaseModel):
    url: str = Field(max_length=4000)


@router.post("/test")
def test_link(body: TestIn, _: AdminUser, db: DB) -> dict:
    """Check a link the way a click would, without logging it."""
    url = body.url.strip()
    if "://" not in url:
        url = "https://" + url
    if not lp.host_of(url) or not url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "invalid_url")
    cfg, _ = check_settings(db)
    v = lp.check(url, 0, cfg)
    out = v.to_dict()
    out["texts"] = {lang: [lp.reason_text(r, lang) for r in out["reasons"]] for lang in ("en", "nl")}
    return out
