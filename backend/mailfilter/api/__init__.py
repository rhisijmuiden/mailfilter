"""All API routers. Each feature adds its own module and one line here."""

from fastapi import APIRouter

from . import auth, backup, dns, quarantine, smtp, system, trace, users

router = APIRouter(prefix="/api")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(system.router)
router.include_router(trace.router)
router.include_router(quarantine.router)
router.include_router(dns.router)
router.include_router(backup.router)
router.include_router(smtp.router)
