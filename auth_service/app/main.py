import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.health_monitor import run_health_monitor
from app.routers import databases

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    monitor = asyncio.create_task(run_health_monitor())
    yield
    monitor.cancel()


app = FastAPI(title="QueryLens Auth Service", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(databases.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
