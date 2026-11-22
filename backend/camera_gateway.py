from __future__ import annotations

import base64
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from network_security import validate_safe_host, validate_safe_url

try:
    import cv2
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None


RTSP_CONNECT_TIMEOUT_S = float(os.getenv("RTSP_CONNECT_TIMEOUT_S", "8"))
RTSP_READ_TIMEOUT_S = float(os.getenv("RTSP_READ_TIMEOUT_S", "8"))
ONVIF_TIMEOUT_S = float(os.getenv("ONVIF_TIMEOUT_S", "6"))
CAMERA_SECRET_KEY = os.getenv("ARGOS_CAMERA_SECRET_KEY", "").strip()


@dataclass
class CameraConfig:
    id: str = ""
    name: str = ""
    host: str = ""
    rtsp_port: int = 554
    onvif_port: int = 80
    username: str = ""
    password: str = ""
    rtsp_url_manual: str = ""
    rtsp_url: str = ""
    manufacturer: str = ""
    model: str = ""
    selected_profile_token: str = ""
    selected_stream_role: str = "substream"
    status: str = "offline"
    lat: float | None = None
    lon: float | None = None
    azimuth_deg: float | None = None


@dataclass
class CameraInfo:
    host: str = ""
    onvif_port: int = 80
    manufacturer: str = ""
    model: str = ""
    name: str = ""
    xaddrs: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    status: str = "unknown"
    error: str = ""


@dataclass
class StreamProfile:
    token: str
    name: str = ""
    width: int | None = None
    height: int | None = None
    encoding: str = ""
    rtsp_uri: str = ""
    role: str = "mainstream"


@dataclass
class StreamHealth:
    status: str
    online: bool
    url: str = ""
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    latency_ms: float = 0.0
    checked_at: str = ""
    error: str = ""


@dataclass
class FrameResult:
    camera_id: str
    ok: bool
    timestamp: str
    width: int = 0
    height: int = 0
    image: str = ""
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class CameraHealth:
    camera_id: str
    status: str
    stream: StreamHealth
    checked_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_onvif_cameras(timeout_s: float = ONVIF_TIMEOUT_S) -> list[CameraInfo]:
    logger.info("onvif discovery started")
    try:
        from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
    except Exception as exc:
        logger.warning("onvif unavailable: WSDiscovery import failed: {}", exc)
        return [CameraInfo(status="error", error=f"WSDiscovery unavailable: {exc}")]

    discovery = WSDiscovery()
    try:
        discovery.start()
        services = discovery.searchServices(timeout=timeout_s)
    except Exception as exc:
        logger.warning("onvif discovery failed: {}", exc)
        return [CameraInfo(status="error", error=str(exc))]
    finally:
        try:
            discovery.stop()
        except Exception:
            pass

    cameras = []
    for service in services:
        xaddrs = [str(item) for item in service.getXAddrs()]
        scopes = [str(item) for item in service.getScopes()]
        host = first_host_from_xaddrs(xaddrs)
        cameras.append(
            CameraInfo(
                host=host,
                onvif_port=first_port_from_xaddrs(xaddrs) or 80,
                name=guess_scope_value(scopes, "name") or host,
                manufacturer=guess_scope_value(scopes, "hardware") or "",
                model=guess_scope_value(scopes, "model") or "",
                xaddrs=xaddrs,
                scopes=scopes,
                status="discovered",
            )
        )
    logger.info("onvif discovery completed: {} device(s)", len(cameras))
    return cameras


def get_onvif_profiles(camera_config: CameraConfig) -> list[StreamProfile]:
    logger.info("onvif profiles requested for {}", camera_config.host)
    validate_safe_host(camera_config.host, context="onvif_host")
    camera = create_onvif_camera(camera_config)
    media = camera.create_media_service()
    profiles = media.GetProfiles()
    output: list[StreamProfile] = []
    for profile in profiles or []:
        token = str(getattr(profile, "token", "") or "")
        video = getattr(profile, "VideoEncoderConfiguration", None)
        resolution = getattr(video, "Resolution", None)
        width = int(getattr(resolution, "Width", 0) or 0) or None
        height = int(getattr(resolution, "Height", 0) or 0) or None
        encoding = str(getattr(video, "Encoding", "") or "")
        stream = StreamProfile(
            token=token,
            name=str(getattr(profile, "Name", "") or token),
            width=width,
            height=height,
            encoding=encoding,
            role=classify_stream_role(width, height),
        )
        try:
            stream.rtsp_uri = get_rtsp_uri_from_onvif(camera_config, token)
        except Exception as exc:
            logger.warning("onvif GetStreamUri failed for {} profile {}: {}", camera_config.host, token, exc)
        output.append(stream)
    output.sort(key=lambda item: ((item.width or 0) * (item.height or 0), item.name))
    return output


def get_rtsp_uri_from_onvif(camera_config: CameraConfig, profile_token: str) -> str:
    logger.info("onvif GetStreamUri requested for {} profile {}", camera_config.host, profile_token)
    validate_safe_host(camera_config.host, context="onvif_host")
    camera = create_onvif_camera(camera_config)
    media = camera.create_media_service()
    request = media.create_type("GetStreamUri")
    request.StreamSetup = {
        "Stream": "RTP-Unicast",
        "Transport": {"Protocol": "RTSP"},
    }
    request.ProfileToken = profile_token
    response = media.GetStreamUri(request)
    return str(getattr(response, "Uri", "") or "")


def test_rtsp_connection(rtsp_url: str, timeout_s: float = RTSP_CONNECT_TIMEOUT_S) -> StreamHealth:
    started = time.perf_counter()
    logger.info("rtsp connection started: {}", scrub_url(rtsp_url))
    if not rtsp_url:
        return StreamHealth(status="error", online=False, checked_at=utc_now(), error="empty RTSP URL")
    try:
        validate_safe_url(rtsp_url, context="rtsp_url", allowed_schemes={"rtsp", "http", "https"})
    except ValueError as exc:
        return StreamHealth(status="error", online=False, url=scrub_url(rtsp_url), checked_at=utc_now(), error=str(exc))
    if cv2 is None:
        return StreamHealth(status="error", online=False, url=scrub_url(rtsp_url), checked_at=utc_now(), error="OpenCV unavailable")
    capture = None
    try:
        capture = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        set_capture_timeout(capture, timeout_s)
        if not capture.isOpened():
            logger.warning("rtsp unavailable: {}", scrub_url(rtsp_url))
            return StreamHealth(
                status="offline",
                online=False,
                url=scrub_url(rtsp_url),
                latency_ms=elapsed_ms(started),
                checked_at=utc_now(),
                error="stream could not be opened",
            )
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or None
        ok, frame = capture.read()
        if not ok or frame is None:
            logger.warning("rtsp stream read failed: {}", scrub_url(rtsp_url))
            return StreamHealth(
                status="offline",
                online=False,
                url=scrub_url(rtsp_url),
                fps=fps,
                latency_ms=elapsed_ms(started),
                checked_at=utc_now(),
                error="stream opened but no frame was read",
            )
        height, width = frame.shape[:2]
        logger.info("rtsp connection succeeded: {} {}x{}", scrub_url(rtsp_url), width, height)
        return StreamHealth(
            status="online",
            online=True,
            url=scrub_url(rtsp_url),
            fps=fps,
            width=int(width),
            height=int(height),
            latency_ms=elapsed_ms(started),
            checked_at=utc_now(),
        )
    except Exception as exc:
        logger.warning("rtsp connection failed: {}: {}", scrub_url(rtsp_url), exc)
        return StreamHealth(
            status="error",
            online=False,
            url=scrub_url(rtsp_url),
            latency_ms=elapsed_ms(started),
            checked_at=utc_now(),
            error=str(exc),
        )
    finally:
        if capture is not None:
            capture.release()


def capture_frame(camera_id: str, rtsp_url: str, timeout_s: float = RTSP_READ_TIMEOUT_S) -> FrameResult:
    started = time.perf_counter()
    logger.info("rtsp snapshot started for {}: {}", camera_id, scrub_url(rtsp_url))
    if not rtsp_url:
        return FrameResult(camera_id=camera_id, ok=False, timestamp=utc_now(), error="empty RTSP URL")
    try:
        validate_safe_url(rtsp_url, context="rtsp_url", allowed_schemes={"rtsp", "http", "https"})
    except ValueError as exc:
        return FrameResult(camera_id=camera_id, ok=False, timestamp=utc_now(), error=str(exc))
    if cv2 is None:
        return FrameResult(camera_id=camera_id, ok=False, timestamp=utc_now(), error="OpenCV unavailable")
    capture = None
    try:
        capture = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        set_capture_timeout(capture, timeout_s)
        if not capture.isOpened():
            return FrameResult(camera_id=camera_id, ok=False, timestamp=utc_now(), latency_ms=elapsed_ms(started), error="stream could not be opened")
        ok, frame = capture.read()
        if not ok or frame is None:
            return FrameResult(camera_id=camera_id, ok=False, timestamp=utc_now(), latency_ms=elapsed_ms(started), error="stream opened but no frame was read")
        encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not encoded:
            return FrameResult(camera_id=camera_id, ok=False, timestamp=utc_now(), latency_ms=elapsed_ms(started), error="failed to encode frame")
        height, width = frame.shape[:2]
        return FrameResult(
            camera_id=camera_id,
            ok=True,
            timestamp=utc_now(),
            width=int(width),
            height=int(height),
            image="data:image/jpeg;base64," + base64.b64encode(buffer).decode("ascii"),
            latency_ms=elapsed_ms(started),
        )
    except Exception as exc:
        logger.warning("rtsp snapshot failed for {}: {}", camera_id, exc)
        return FrameResult(camera_id=camera_id, ok=False, timestamp=utc_now(), latency_ms=elapsed_ms(started), error=str(exc))
    finally:
        if capture is not None:
            capture.release()


def get_camera_health(camera_id: str, rtsp_url: str) -> CameraHealth:
    stream = test_rtsp_connection(rtsp_url)
    return CameraHealth(camera_id=camera_id, status=stream.status, stream=stream, checked_at=utc_now())


def select_preferred_profile(profiles: list[StreamProfile]) -> StreamProfile | None:
    if not profiles:
        return None
    substreams = [item for item in profiles if item.role == "substream" and item.rtsp_uri]
    if substreams:
        return substreams[0]
    with_uri = [item for item in profiles if item.rtsp_uri]
    if with_uri:
        return with_uri[0]
    return profiles[0]


def create_onvif_camera(camera_config: CameraConfig):
    validate_safe_host(camera_config.host, context="onvif_host")
    try:
        from onvif import ONVIFCamera
    except Exception as exc:
        logger.warning("onvif unavailable: onvif import failed: {}", exc)
        raise RuntimeError(f"ONVIF unavailable: {exc}") from exc
    try:
        return ONVIFCamera(
            camera_config.host,
            int(camera_config.onvif_port or 80),
            camera_config.username,
            camera_config.password,
            no_cache=True,
            adjust_time=True,
        )
    except TypeError:
        return ONVIFCamera(
            camera_config.host,
            int(camera_config.onvif_port or 80),
            camera_config.username,
            camera_config.password,
        )


def config_from_camera(camera: dict[str, Any], password: str = "") -> CameraConfig:
    gateway = camera.get("camera_gateway") or {}
    return CameraConfig(
        id=str(camera.get("id") or ""),
        name=str(camera.get("name") or ""),
        host=str(gateway.get("host") or host_from_url(camera.get("original_stream_url") or camera.get("url") or "")),
        rtsp_port=int(gateway.get("rtsp_port") or 554),
        onvif_port=int(gateway.get("onvif_port") or 80),
        username=str(gateway.get("username") or ""),
        password=password,
        rtsp_url_manual=str(gateway.get("rtsp_url_manual") or ""),
        rtsp_url=str(camera.get("original_stream_url") or camera.get("url") or ""),
        manufacturer=str(gateway.get("manufacturer") or ""),
        model=str(gateway.get("model") or ""),
        selected_profile_token=str(gateway.get("selected_profile_token") or ""),
        selected_stream_role=str(gateway.get("selected_stream_role") or "substream"),
        status=str(gateway.get("status") or "offline"),
        lat=float(camera["lat"]) if camera.get("lat") is not None else None,
        lon=float(camera["lon"]) if camera.get("lon") is not None else None,
        azimuth_deg=float(camera["azimuth_deg"]) if camera.get("azimuth_deg") is not None else None,
    )


def public_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "__dataclass_fields__"):
        return asdict(item)
    return dict(item)


def classify_stream_role(width: int | None, height: int | None) -> str:
    pixels = int(width or 0) * int(height or 0)
    return "substream" if pixels and pixels <= 640 * 480 else "mainstream"


def first_host_from_xaddrs(xaddrs: list[str]) -> str:
    for xaddr in xaddrs:
        host = host_from_url(xaddr)
        if host:
            return host
    return ""


def first_port_from_xaddrs(xaddrs: list[str]) -> int | None:
    for xaddr in xaddrs:
        parsed = urlparse(xaddr)
        if parsed.port:
            return int(parsed.port)
    return None


def host_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.hostname or ""


def guess_scope_value(scopes: list[str], key: str) -> str:
    marker = f"/{key}/"
    for scope in scopes:
        if marker in scope:
            return scope.rsplit("/", 1)[-1].replace("%20", " ")
    return ""


def set_capture_timeout(capture, timeout_s: float) -> None:
    for prop in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC"):
        value = getattr(cv2, prop, None)
        if value is not None:
            try:
                capture.set(value, int(timeout_s * 1000))
            except Exception:
                pass


def scrub_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.username or parsed.password:
        userinfo = parsed.username or "user"
        netloc = f"{userinfo}:***@{parsed.hostname or ''}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()
    return url or ""


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
