from fastapi import FastAPI

from app.api import investigations_router
from app.core.config import get_settings


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="AI-powered business due diligence and risk assessment platform.",
    version=settings.app_version,
)

app.include_router(investigations_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "BizRisk AI Agent API is running"}


@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "environment": settings.environment,
    }
