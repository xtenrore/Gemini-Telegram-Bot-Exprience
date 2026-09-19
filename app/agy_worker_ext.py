"""Small extension around the AGY worker for raw TUI key injection.

The existing /console/input endpoint is line-oriented and always appends a
newline, which makes arrow-key navigation impossible because ESC[A would be
followed by Enter. This endpoint writes a tightly allowlisted key sequence
straight to the already-running PTY and nothing else.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.agy_worker import _auth, app, console
from app.agy_permission_guard_v422 import install_permission_guard_v422

# Install before FastAPI startup creates/runs the supervisor task. This keeps
# strict headless permissions while making denied tool actions retryable.
install_permission_guard_v422()


class KeyInput(BaseModel):
    key: str


_KEYS: dict[str, bytes] = {
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "left": b"\x1b[D",
    "right": b"\x1b[C",
    "enter": b"\r",
    "space": b" ",
    "tab": b"\t",
    "escape": b"\x1b",
    "ctrl_c": b"\x03",
    "ctrl_s": b"\x13",
}


@app.post("/console/key", dependencies=[Depends(_auth)])
async def console_key(payload: KeyInput) -> dict[str, bool]:
    raw = _KEYS.get(payload.key)
    if raw is None:
        raise HTTPException(status_code=400, detail="Unsupported key")
    if not console.process or console.process.returncode is not None or console.master_fd is None:
        raise HTTPException(status_code=409, detail="AGY console is not running")
    await asyncio.to_thread(os.write, console.master_fd, raw)
    return {"ok": True}
