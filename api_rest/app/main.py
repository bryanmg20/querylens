from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import diagnostics

app = FastAPI(title="QueryLens API REST", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(diagnostics.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
