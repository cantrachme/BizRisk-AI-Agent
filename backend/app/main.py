from fastapi import FastAPI


app = FastAPI(
    title="BizRisk AI Agent",
    description="AI-powered business due diligence and risk assessment platform.",
    version="0.1.0",
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "BizRisk AI Agent API is running"}


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy"}
