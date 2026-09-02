from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import investigations_router, test_router
from app.core.config import get_settings


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="AI-powered business due diligence and risk assessment platform.",
    version=settings.app_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(investigations_router, prefix="/api/v1")
app.include_router(test_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "BizRisk AI Agent API is running"}


@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "environment": settings.environment,
    }
