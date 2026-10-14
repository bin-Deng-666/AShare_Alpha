"""Local strategy console with managed application startup and shutdown."""

from contextlib import asynccontextmanager, nullcontext
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ashare_alpha.paths import JOBS_ROOT
from ashare_alpha.storage import atomic_json, publication_lock
from ashare_alpha.web.api.routes import router
from ashare_alpha.web.core.job_manager import jm

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    pid_path = JOBS_ROOT.parent / "server.json"
    register = app.state.register_server
    lock = (
        publication_lock(pid_path.with_suffix(".lock")) if register else nullcontext()
    )
    with lock:
        if register:
            atomic_json(pid_path, {"pid": os.getpid(), "port": app.state.port})
        try:
            yield
        finally:
            await jm.shutdown()
            if register:
                pid_path.unlink(missing_ok=True)


def create_app(*, register_server=False, port=8787):
    application = FastAPI(
        title="AShare Alpha 策略操作台", version="0.1.0", lifespan=lifespan
    )
    application.state.register_server = register_server
    application.state.port = port
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(router)

    @application.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return application


app = create_app()


def main(argv=None):
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    uvicorn.run(
        create_app(register_server=True, port=args.port),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
