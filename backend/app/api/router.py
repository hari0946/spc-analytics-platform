from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.routes import analysis, assistant, audit, dashboard, hierarchy, reports, auth, data, health, plants, roles, uploads, users

# All business routes live under /api. Later phases add their routers here.
api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(audit.router)
api_router.include_router(uploads.router)
api_router.include_router(data.router)
api_router.include_router(analysis.router)
api_router.include_router(dashboard.router)
api_router.include_router(assistant.router)
api_router.include_router(reports.router)
api_router.include_router(hierarchy.router, dependencies=[Depends(get_current_user)])
# data routes require an authenticated user (finer-grained permissions are added per module)
api_router.include_router(plants.router, dependencies=[Depends(get_current_user)])
