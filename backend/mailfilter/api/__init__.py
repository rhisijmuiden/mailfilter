"""All API routers. Each feature adds its own module and one line here."""

from fastapi import APIRouter

from . import auth, system, users

router = APIRouter(prefix="/api")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(system.router)
