from fastapi import APIRouter

from app.api.endpoints import admin, auth, complaints, health, routes, tags, users

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(routes.router)
api_router.include_router(tags.router)
api_router.include_router(complaints.router)
api_router.include_router(admin.router)
