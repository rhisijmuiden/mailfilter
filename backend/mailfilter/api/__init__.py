"""All API routers. Each feature adds its own module and one line here."""

from fastapi import APIRouter

from . import abuse, auth, backup, dashboard, dmarc, dns, gateway, pmg_import, quarantine, rules, smtp, system, trace, users

router = APIRouter(prefix="/api")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(gateway.router)
router.include_router(abuse.router)
router.include_router(system.router)
router.include_router(trace.router)
router.include_router(dashboard.router)
router.include_router(quarantine.router)
router.include_router(dns.router)
router.include_router(dns.public)
router.include_router(dmarc.router)
router.include_router(backup.router)
router.include_router(smtp.router)
router.include_router(pmg_import.router)
router.include_router(rules.router)
