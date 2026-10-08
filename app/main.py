import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.routes import router
from app.config import Settings, get_settings
from app.container import build_container

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = build_container(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        async def evictor():
            while True:
                await asyncio.sleep(settings.eviction_interval_seconds)
                await asyncio.to_thread(container.idempotency.evict_expired)

        task = asyncio.create_task(evictor())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="UPI Offline Mesh", version="1.0.0", lifespan=lifespan)
    app.state.container = container
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC / "dashboard.html")

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def get_app() -> FastAPI:  # uvicorn --factory app.main:get_app
    return create_app()
