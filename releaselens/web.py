"""Serve ReleaseLens and run only the bundled HTTP experiments."""
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .demo import experiment
from .engine import Gate

STATIC = Path(__file__).with_name("static")
app = FastAPI(title="ReleaseLens")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
demo_lock = Lock()


class DemoRequest(BaseModel):
    scenario: Literal["healthy", "slow", "contract", "mixed"] = "mixed"
    repeats: int = Field(8, ge=3, le=24, strict=True)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/experiment")
def run_demo(config: DemoRequest):
    if not demo_lock.acquire(blocking=False):
        raise HTTPException(409, "An experiment is already running. Try again when it finishes.")
    try:
        return experiment(config.scenario, Gate(repeats=config.repeats))
    finally:
        demo_lock.release()
