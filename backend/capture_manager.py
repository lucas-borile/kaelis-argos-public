from __future__ import annotations

import asyncio
import os
import select
import subprocess
import time
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlparse

from frame_processor import capture_hls_frame
from stream_resolver import resolve_player

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover
    imageio_ffmpeg = None


CAPTURE_WORKER_ENABLED = os.getenv("CAPTURE_WORKER_ENABLED", "true").lower() == "true"
CAPTURE_WORKER_INTERVAL_S = float(os.getenv("CAPTURE_WORKER_INTERVAL_S", "3"))
CAPTURE_WORKER_ERROR_INTERVAL_S = float(os.getenv("CAPTURE_WORKER_ERROR_INTERVAL_S", "10"))
CAPTURE_WORKER_MAX_STALE_S = float(os.getenv("CAPTURE_WORKER_MAX_STALE_S", "20"))
CAPTURE_FFMPEG_PIPE_ENABLED = os.getenv("CAPTURE_FFMPEG_PIPE_ENABLED", "true").lower() == "true"
CAPTURE_FFMPEG_READ_TIMEOUT_S = float(os.getenv("CAPTURE_FFMPEG_READ_TIMEOUT_S", "12"))


class CameraCaptureManager:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self.latest: dict[str, dict] = {}
        self.status: dict[str, dict] = {}

    async def reconcile(self, cameras: list[dict]) -> None:
        if not CAPTURE_WORKER_ENABLED:
            return
        active_ids = {camera["id"] for camera in cameras if camera.get("active", True)}
        for camera in cameras:
            camera_id = camera["id"]
            if camera_id in active_ids and camera_id not in self.tasks:
                self.tasks[camera_id] = asyncio.create_task(self.capture_loop(camera))
        for camera_id in list(self.tasks.keys()):
            if camera_id not in active_ids:
                await self.stop_camera(camera_id)

    async def stop_camera(self, camera_id: str) -> None:
        task = self.tasks.pop(camera_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.latest.pop(camera_id, None)
        self.status.pop(camera_id, None)

    async def stop_all(self) -> None:
        for camera_id in list(self.tasks.keys()):
            await self.stop_camera(camera_id)

    def get_latest(self, camera_id: str, max_age_s: float | None = None) -> dict | None:
        item = self.latest.get(camera_id)
        if not item:
            return None
        max_age = CAPTURE_WORKER_MAX_STALE_S if max_age_s is None else max_age_s
        if time.monotonic() - float(item.get("captured_monotonic", 0)) > max_age:
            return None
        return dict(item)

    def snapshot_status(self) -> dict:
        now = time.monotonic()
        cameras = deepcopy(self.status)
        for item in cameras.values():
            captured_at = float(item.pop("captured_monotonic", now) or now)
            item["age_s"] = round(max(0.0, now - captured_at), 3)
        return {
            "enabled": CAPTURE_WORKER_ENABLED,
            "workers": len(self.tasks),
            "frames_cached": len(self.latest),
            "interval_s": CAPTURE_WORKER_INTERVAL_S,
            "max_stale_s": CAPTURE_WORKER_MAX_STALE_S,
            "cameras": cameras,
        }

    async def capture_loop(self, camera: dict) -> None:
        camera_id = camera["id"]
        frame_index = 0
        pipe: PersistentFFmpegPipe | None = None
        source: dict | None = None
        resolve_ms = 0.0
        try:
            while camera.get("active", True):
                started = time.perf_counter()
                if pipe is None:
                    source, resolve_ms = await asyncio.to_thread(resolve_camera_source, camera)
                    if should_use_persistent_pipe(source):
                        pipe = PersistentFFmpegPipe(
                            source["hls_url"],
                            frame_interval_s=worker_interval_seconds(camera),
                        )

                if pipe is not None:
                    item = await asyncio.to_thread(capture_pipe_once, camera, frame_index, source or {}, resolve_ms, pipe)
                    if item.get("frame") is None:
                        pipe.stop()
                        pipe = None
                else:
                    item = await asyncio.to_thread(capture_once, camera, frame_index)

                frame_index += 1
                if item.get("frame") is not None:
                    self.latest[camera_id] = item
                self.status[camera_id] = {
                    "ok": item.get("frame") is not None,
                    "timestamp": item.get("timestamp"),
                    "frame_index": frame_index,
                    "capture_ms": item.get("capture_ms", 0),
                    "resolve_ms": item.get("resolve_ms", 0),
                    "mode": item.get("mode", "poll"),
                    "error": item.get("error", ""),
                    "captured_monotonic": item.get("captured_monotonic"),
                }
                elapsed = time.perf_counter() - started
                interval = worker_interval(camera, item)
                await asyncio.sleep(max(0.05, interval - elapsed))
        finally:
            if pipe is not None:
                pipe.stop()


def capture_once(camera: dict, frame_index: int) -> dict:
    source, resolve_ms = resolve_camera_source(camera)
    error = source.get("error", "")

    frame = None
    capture_ms = 0.0
    if source.get("hls_url"):
        try:
            capture_started = time.perf_counter()
            frame = capture_hls_frame(source["hls_url"])
            capture_ms = (time.perf_counter() - capture_started) * 1000
        except Exception as exc:
            error = str(exc)
    elif not error:
        error = source.get("error") or "stream url unavailable"
    if frame is None and not error:
        error = "frame capture failed"

    return {
        "camera_id": camera["id"],
        "source": source,
        "frame": frame,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "captured_monotonic": time.monotonic(),
        "frame_index": frame_index,
        "resolve_ms": round(resolve_ms, 3),
        "capture_ms": round(capture_ms, 3),
        "error": error,
        "mode": "poll",
    }


def resolve_camera_source(camera: dict) -> tuple[dict, float]:
    source = {
        "player_url": camera.get("original_stream_url", ""),
        "hls_url": "",
        "poster_url": "",
        "source_type": "unknown",
    }
    resolve_ms = 0.0
    try:
        resolve_started = time.perf_counter()
        source = resolve_player(camera["original_stream_url"])
        resolve_ms = (time.perf_counter() - resolve_started) * 1000
    except Exception as exc:
        source["error"] = str(exc)
    return source, resolve_ms


def capture_pipe_once(
    camera: dict,
    frame_index: int,
    source: dict,
    resolve_ms: float,
    pipe: "PersistentFFmpegPipe",
) -> dict:
    frame = None
    error = ""
    capture_started = time.perf_counter()
    try:
        frame = pipe.read_frame(CAPTURE_FFMPEG_READ_TIMEOUT_S)
    except Exception as exc:
        error = str(exc)
    capture_ms = (time.perf_counter() - capture_started) * 1000
    if frame is None and not error:
        error = "persistent ffmpeg pipe returned no frame"
    return {
        "camera_id": camera["id"],
        "source": source,
        "frame": frame,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "captured_monotonic": time.monotonic(),
        "frame_index": frame_index,
        "resolve_ms": round(resolve_ms, 3),
        "capture_ms": round(capture_ms, 3),
        "error": error,
        "mode": "ffmpeg-pipe",
    }


def worker_interval(camera: dict, item: dict) -> float:
    if item.get("frame") is None:
        return CAPTURE_WORKER_ERROR_INTERVAL_S
    camera_interval = float(camera.get("capture_interval_s") or CAPTURE_WORKER_INTERVAL_S)
    return max(0.2, min(camera_interval, CAPTURE_WORKER_INTERVAL_S))


def worker_interval_seconds(camera: dict) -> float:
    camera_interval = float(camera.get("capture_interval_s") or CAPTURE_WORKER_INTERVAL_S)
    return max(0.2, min(camera_interval, CAPTURE_WORKER_INTERVAL_S))


def should_use_persistent_pipe(source: dict) -> bool:
    if not CAPTURE_FFMPEG_PIPE_ENABLED:
        return False
    if cv2 is None or np is None or imageio_ffmpeg is None:
        return False
    url = source.get("hls_url") or ""
    if not url:
        return False
    path = urlparse(url).path.lower()
    return not path.endswith((".jpg", ".jpeg")) and not path.endswith("/capture")


class PersistentFFmpegPipe:
    def __init__(self, url: str, frame_interval_s: float = 3.0) -> None:
        self.url = url
        self.frame_interval_s = frame_interval_s
        self.process: subprocess.Popen | None = None
        self.buffer = bytearray()

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        fps = 1 / max(self.frame_interval_s, 0.2)
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-rw_timeout",
            str(int(CAPTURE_FFMPEG_READ_TIMEOUT_S * 1_000_000)),
            "-i",
            self.url,
            "-an",
            "-vf",
            f"fps={fps:.4f}",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        self.stop()
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.buffer.clear()

    def stop(self) -> None:
        process = self.process
        self.process = None
        if not process:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def read_frame(self, timeout_s: float):
        self.start()
        if not self.process or not self.process.stdout:
            raise RuntimeError("ffmpeg pipe unavailable")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            frame_bytes = self.extract_jpeg()
            if frame_bytes:
                return decode_jpeg(frame_bytes)
            if self.process.poll() is not None:
                stderr = ""
                if self.process.stderr:
                    try:
                        stderr = self.process.stderr.read().decode("utf-8", "ignore")[-240:]
                    except Exception:
                        stderr = ""
                raise RuntimeError(f"ffmpeg pipe exited {self.process.returncode}: {stderr}")
            remaining = max(0.01, deadline - time.monotonic())
            ready, _, _ = select.select([self.process.stdout], [], [], min(0.25, remaining))
            if not ready:
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("ffmpeg pipe closed")
            self.buffer.extend(chunk)
            if len(self.buffer) > 8_000_000:
                del self.buffer[:4_000_000]
        raise TimeoutError("ffmpeg pipe frame timeout")

    def extract_jpeg(self) -> bytes | None:
        start = self.buffer.find(b"\xff\xd8")
        if start < 0:
            if len(self.buffer) > 2:
                del self.buffer[:-2]
            return None
        end = self.buffer.find(b"\xff\xd9", start + 2)
        if end < 0:
            if start > 0:
                del self.buffer[:start]
            return None
        end += 2
        frame = bytes(self.buffer[start:end])
        del self.buffer[:end]
        return frame


def decode_jpeg(frame_bytes: bytes):
    image = cv2.imdecode(np.frombuffer(frame_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("failed to decode ffmpeg jpeg frame")
    return image
