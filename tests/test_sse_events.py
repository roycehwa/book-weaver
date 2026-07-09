"""Smoke tests for the SSE event stream generator."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path


def test_sse_endpoint_registered():
    """The route must exist in backend/main.py."""
    main_py = (Path(__file__).resolve().parent.parent / "backend" / "main.py").read_text(encoding="utf-8")
    assert '/jobs/{job_id}/events/stream' in main_py
    assert 'text/event-stream' in main_py
    assert 'from fastapi.responses import StreamingResponse' in main_py


def test_sse_generator_skips_to_data_after_new_bytes():
    """Drive the same generator pattern manually and confirm
    that newly appended bytes are emitted as ``data:`` frames."""

    async def stream(events_path: Path, get_state):
        offset = events_path.stat().st_size
        yield f"event: hello\ndata: {json.dumps({'offset': offset})}\n\n"
        idle = 0
        while idle < 6:
            await asyncio.sleep(0)  # drive the loop
            cur = events_path.stat().st_size
            if cur == offset:
                idle += 1
                if get_state() not in {"translating", "failed"}:
                    yield f"event: closed\ndata: {json.dumps({'reason': 'state'})}\n\n"
                    return
                yield f"event: heartbeat\ndata: {json.dumps({'idle': idle})}\n\n"
                continue
            idle = 0
            with events_path.open("rb") as fp:
                fp.seek(offset)
                blob = fp.read(cur - offset).decode("utf-8", errors="replace")
            offset = cur
            for line in blob.splitlines():
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    evt = {"raw": line}
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    async def run():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_text("", encoding="utf-8")  # empty at start
            state = {"v": "translating"}

            async def producer():
                # wait two ticks so the stream enters the loop, then append
                for _ in range(2):
                    await asyncio.sleep(0)
                with p.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps({"type": "stage_started"}) + "\n")

            async def consumer():
                frames = []
                async for f in stream(p, lambda: state["v"]):
                    frames.append(f)
                    if f.startswith("data:") and "stage_started" in f:
                        state["v"] = "awaiting_human_review"
                    if f.startswith("event: closed"):
                        break
                return frames

            prod = asyncio.create_task(producer())
            frames = await consumer()
            await prod
            return frames

    frames = asyncio.run(run())
    assert any("data:" in f and "stage_started" in f for f in frames), frames
    # must have closed because state changed
    assert any(f.startswith("event: closed") for f in frames), frames


def test_sse_generator_idle_closes_after_max_ticks():
    async def stream(events_path: Path, get_state):
        offset = events_path.stat().st_size
        yield f"event: hello\ndata: {json.dumps({'offset': offset})}\n\n"
        idle = 0
        while idle < 3:
            await asyncio.sleep(0)
            cur = events_path.stat().st_size
            if cur == offset:
                idle += 1
                if get_state() not in {"translating", "failed"}:
                    return
                yield f"event: heartbeat\ndata: {json.dumps({'idle': idle})}\n\n"
                continue
            idle = 0

    async def run():
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            p.write_text("", encoding="utf-8")
            frames = []
            async for f in stream(p, lambda: "translating"):
                frames.append(f)
            return frames

    frames = asyncio.run(run())
    heartbeats = [f for f in frames if "heartbeat" in f]
    # we have an upper bound; generator must terminate
    assert len(heartbeats) == 3
    # last frame is the final heartbeat, no closed event in this variant
    assert frames[-1].startswith("event: heartbeat")
