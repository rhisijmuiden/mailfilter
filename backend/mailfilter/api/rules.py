"""Custom rules (Configuration > Custom rules): newsletter filter, phishing
protection, attachment policy, ARC and other switchable Rspamd rules,
globally and per domain.

Admins change everything. Customers see the rules and choose on, off or
"follow the default" for their own domains. Every change goes through
mailfilter.gateway.apply_changes, so it is checked by Rspamd and rolled back
when Rspamd does not accept it.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from mailfilter_config.rules import BY_ID, RULES, CustomRule, effective, problems

from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser, visible_domains
from ..gateway import RULES_KEY, apply_changes, custom_rules, ensure_imported
from ..models import Domain, DomainRoute, User
from ..settings_store import set_setting

router = APIRouter(prefix="/rules", tags=["rules"])


class RuleIn(BaseModel):
    enabled: bool
    options: dict[str, Any] = Field(default_factory=dict)
    domains: dict[str, bool] = Field(default_factory=dict)


class DomainChoice(BaseModel):
    # default: follow the global switch
    state: Literal["default", "on", "off"]


def _domains(db, user: User) -> list[str]:
    """Domains that mail is filtered for (configured in Domains) that this user may see."""
    names = list(db.execute(select(Domain.name).join(DomainRoute, DomainRoute.domain_id == Domain.id)
                            .where(DomainRoute.kind == "transport").order_by(Domain.name)).scalars())
    allowed = visible_domains(user)
    return names if allowed is None else [n for n in names if n in allowed]


def _out(db, user: User) -> dict:
    domains = _domains(db, user)
    chosen = effective(custom_rules(db))
    rules = []
    for spec in RULES:
        r = chosen[spec["id"]]
        rules.append({
            "id": spec["id"], "group": spec["group"], "direction": spec["direction"], "symbol": spec["symbol"],
            "title": spec["title"], "summary": spec["summary"], "details": spec["details"],
            "default": spec["default"], "enabled": r.enabled, "per_domain": spec["per_domain"],
            # Lists (protected names, extra domains) are the admin's business only.
            "options": [{**o, "value": r.options.get(o["id"], o["default"])} for o in spec["options"]
                        if user.is_admin or o["type"] != "list"],
            "domains": {d: r.domains[d] for d in domains if d in r.domains},
        })
    return {"rules": rules, "domains": domains, "can_edit_global": user.is_admin}


def _save(db, request: Request, user: User, chosen: dict[str, CustomRule], rule_id: str, **detail) -> None:
    # Normalise lists and texts (".EXE" -> "exe") so the page shows what is applied;
    # mistakes are reported by apply_changes.
    problems(chosen, set(db.execute(select(Domain.name)).scalars()))
    set_setting(db, RULES_KEY, {k: v.__dict__ for k, v in chosen.items()})
    apply_changes(db)
    audit(db, request, user, "custom_rule_updated", rule_id, **detail)
    db.commit()


def _rule(rule_id: str) -> None:
    if rule_id not in BY_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


@router.get("")
def list_rules(user: CurrentUser, db: DB) -> dict:
    ensure_imported(db)
    return _out(db, user)


@router.put("/{rule_id}")
def put_rule(rule_id: str, body: RuleIn, request: Request, admin: AdminUser, db: DB) -> dict:
    _rule(rule_id)
    ensure_imported(db)
    known = set(_domains(db, admin))
    unknown = sorted(d for d in body.domains if d.strip().lower() not in known)
    if unknown:
        raise HTTPException(422, {"code": "invalid_config", "problems": [
            {"field": "domains", "code": "rule_domain_unknown", "en": f"{d} is not a domain of this Mailfilter.",
             "nl": f"{d} is geen domein van deze Mailfilter."} for d in unknown]})
    chosen = custom_rules(db)
    chosen[rule_id] = CustomRule(enabled=body.enabled, options=dict(body.options),
                                 domains={d.strip().lower(): v for d, v in body.domains.items()})
    _save(db, request, admin, chosen, rule_id, enabled=body.enabled, options=body.options, domains=body.domains)
    return _out(db, admin)


@router.put("/{rule_id}/domains/{domain}")
def put_domain_choice(rule_id: str, domain: str, body: DomainChoice, request: Request, user: CurrentUser,
                      db: DB) -> dict:
    """On, off or default for one domain; customers may do this for their own domains."""
    _rule(rule_id)
    ensure_imported(db)
    if not BY_ID[rule_id]["per_domain"]:
        raise HTTPException(422, {"code": "invalid_config", "problems": [
            {"field": "domains", "code": "rule_not_per_domain", "en": "This rule can only be switched for everyone.",
             "nl": "Deze regel kan alleen voor iedereen tegelijk aan of uit."}]})
    domain = domain.strip().lower()
    if domain not in _domains(db, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    chosen = effective(custom_rules(db))
    rule = chosen[rule_id]
    if body.state == "default":
        rule.domains.pop(domain, None)
    else:
        rule.domains[domain] = body.state == "on"
    _save(db, request, user, {**custom_rules(db), rule_id: rule}, rule_id, domain=domain, state=body.state)
    return _out(db, user)
