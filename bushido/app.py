"""Web console for the Bushido T1981: `python -m bushido.app [--simulate]`."""

import argparse
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import constants as c
from .library import Library
from .link import AntLink, BrakeLink, SimulatedLink
from .trainer import Trainer

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
SIMULATION_DIR = ROOT / "sim-data"   # the simulator never touches real sessions or profiles
WORKOUTS_DIR = ROOT / "workouts"
ROUTES_DIR = ROOT / "routes"
MAX_UPLOAD_BYTES = 20_000_000


class OutdoorRide(BaseModel):
    """A ride done on the road, typed in by the rider."""

    date: date
    km: float = Field(gt=0, le=1000)
    minutes: float = Field(ge=0, le=3000)
    ascent_m: float = Field(default=0, ge=0, le=20000)
    note: str = Field(default="", max_length=120)


def create_app(link: BrakeLink, data_dir: Path = ROOT) -> FastAPI:
    """`data_dir` holds what riders produce: sessions, calibration traces, profiles."""
    sessions_dir = data_dir / "sessions"
    library = Library(WORKOUTS_DIR, ROUTES_DIR)
    trainer = Trainer(link, sessions_dir, data_dir / "captures", library,
                      data_dir / "profiles.json")
    clients: set[WebSocket] = set()

    async def control_loop() -> None:
        period = 1 / c.CONTROL_HZ
        last, tick = time.monotonic(), 0
        while True:
            await asyncio.sleep(period)
            now = time.monotonic()
            report = trainer.tick(now - last)
            last, tick = now, tick + 1
            if tick % c.BROADCAST_EVERY_TICKS == 0 and clients:
                text = json.dumps(report)
                for ws in list(clients):
                    try:
                        await ws.send_text(text)
                    except (WebSocketDisconnect, RuntimeError):
                        clients.discard(ws)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        link.start()
        task = asyncio.create_task(control_loop())
        yield
        task.cancel()
        await asyncio.to_thread(trainer.shutdown)

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        """The page must always match the running server: never serve it from cache."""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/library")
    def get_library() -> dict:
        return library.listing()

    @app.get("/api/profile/{name}")
    def get_profile(name: str) -> dict:
        try:
            return trainer.profile(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/stats")
    def get_stats() -> dict:
        return trainer.stats()

    @app.get("/api/progress")
    def get_progress() -> dict:
        return trainer.progress()

    @app.post("/api/rides")
    def add_ride(ride: OutdoorRide) -> dict:
        return trainer.add_outdoor_ride(ride.date, ride.km, ride.minutes, ride.ascent_m, ride.note)

    @app.delete("/api/rides/{ride_id}")
    def delete_ride(ride_id: int) -> dict:
        if not trainer.delete_outdoor_ride(ride_id):
            raise HTTPException(status_code=404, detail="unknown ride")
        return {"deleted": ride_id}

    @app.get("/api/ghosts/{name}")
    def get_ghosts(name: str) -> list:
        try:
            return trainer.ghost_candidates(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/upload/{name}")
    async def upload(name: str, request: Request) -> dict:
        """Add a .zwo workout or a .gpx route; the body is the raw file."""
        body = await request.body()
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="file too large")
        try:
            library.save(name, body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return library.listing()

    @app.get("/api/export/{profile}/{name}")
    def get_export(profile: str, name: str) -> FileResponse:
        path = (sessions_dir / profile / name).resolve()
        inside = path.parent.parent == sessions_dir.resolve()
        if not inside or path.suffix != ".tcx" or not path.is_file():
            raise HTTPException(status_code=404, detail="unknown export")
        return FileResponse(path, filename=name, media_type="application/vnd.garmin.tcx+xml")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        clients.add(ws)
        try:
            while True:
                try:
                    trainer.handle(json.loads(await ws.receive_text()))
                except ValueError as exc:  # includes malformed JSON
                    logger.warning("Ignored UI command: %s", exc)
                    await ws.send_text(json.dumps({"error": str(exc)}))
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="fake brake and rider, no ANT stick needed")
    parser.add_argument("--host", default="127.0.0.1",
                        help="use 0.0.0.0 to reach the console from a phone on the same network")
    parser.add_argument("--port", type=int, default=8981)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("openant").setLevel(logging.WARNING)
    logger.info("Console on http://%s:%d", args.host, args.port)
    if args.simulate:
        SIMULATION_DIR.mkdir(exist_ok=True)
        app = create_app(SimulatedLink(), SIMULATION_DIR)
    else:
        app = create_app(AntLink())
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
