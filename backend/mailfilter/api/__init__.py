"""All API routers. Each feature adds its own module and one line here."""

from fastapi import APIRouter

from . import auth, dns, gateway, quarantine, system, trace, users

router = APIRouter(prefix="/api")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(gateway.router)
router.include_router(system.router)
router.include_router(trace.router)
router.include_router(quarantine.router)
router.include_router(dns.router)
