from __future__ import annotations

import codecs
import os
import re
from functools import lru_cache
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from network_security import validate_safe_url


VIDEO_RE = re.compile(r'VIDEO_SRC\s*=\s*"([^"]+)"')
POSTER_RE = re.compile(r'POSTER_SRC\s*=\s*"([^"]+)"')
URL_RE = re.compile(r'(?:(?:https?:)?//|rtsp://)[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+?(?:\.m3u8|\.mp4|\.mjpeg|\.jpg|\.jpeg)(?:\?[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+)?', re.I)
VAR_RE = re.compile(r'var\s+([a-zA-Z0-9_]+)\s*=\s*"([^"]*)"')
DIRECT_SUFFIXES = (".m3u8", ".mp4", ".mjpeg", ".jpg", ".jpeg")
DIRECT_STREAM_PATHS = ("/stream", "/capture", "/video", "/mjpeg", "/jpg", "/jpeg")
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be")
YOUTUBE_FORMAT = os.getenv("YOUTUBE_FORMAT", "best[protocol*=m3u8]/best")
YOUTUBE_TIMEOUT_S = int(os.getenv("YOUTUBE_TIMEOUT_S", "20"))


def resolve_player(player_url: str) -> dict:
    validate_safe_url(player_url, context="player_url")
    if is_youtube_url(player_url):
        return resolve_youtube(player_url)
    return resolve_player_cached(player_url)


@lru_cache(maxsize=64)
def resolve_player_cached(player_url: str) -> dict:
    validate_safe_url(player_url, context="player_url")
    if is_direct_stream(player_url):
        return {
            "player_url": player_url,
            "hls_url": player_url,
            "poster_url": "",
            "source_type": "direct",
        }

    response = requests.get(player_url, timeout=10)
    response.raise_for_status()
    html = response.text

    hls_url = ""
    poster_url = resolve_poster(player_url, html)

    hls_url = resolve_webcamera_pl(player_url, html)
    if not hls_url:
        hls_url = resolve_direct_urls(player_url, html)
    if not hls_url:
        hls_url = resolve_player_im(player_url, html)

    return {
        "player_url": player_url,
        "hls_url": hls_url,
        "poster_url": poster_url,
        "source_type": "html",
    }


def is_direct_stream(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "rtsp":
        return True
    path = parsed.path.lower()
    return any(path.endswith(suffix) for suffix in DIRECT_SUFFIXES) or any(path.endswith(item) for item in DIRECT_STREAM_PATHS)


def is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return True
    return False


def resolve_youtube(player_url: str) -> dict:
    validate_safe_url(player_url, context="youtube_url", allowed_schemes={"http", "https"})
    try:
        from yt_dlp import YoutubeDL
    except Exception as exc:
        return {
            "player_url": player_url,
            "hls_url": "",
            "poster_url": "",
            "source_type": "youtube",
            "error": f"yt-dlp unavailable: {exc}",
        }

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "format": YOUTUBE_FORMAT,
        "socket_timeout": YOUTUBE_TIMEOUT_S,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(player_url, download=False)
    except Exception as exc:
        return {
            "player_url": player_url,
            "hls_url": "",
            "poster_url": "",
            "source_type": "youtube",
            "error": str(exc),
        }

    hls_url = choose_youtube_stream(info or {})
    if hls_url:
        validate_safe_url(hls_url, context="youtube_stream_url", allowed_schemes={"http", "https"})
    poster_url = (info or {}).get("thumbnail") or ""
    if poster_url:
        validate_safe_url(poster_url, context="youtube_poster_url", allowed_schemes={"http", "https"})
    return {
        "player_url": player_url,
        "hls_url": hls_url,
        "poster_url": poster_url,
        "source_type": "youtube",
        "title": (info or {}).get("title") or "",
        "video_id": youtube_video_id(player_url, info or {}),
    }


def choose_youtube_stream(info: dict) -> str:
    direct_url = info.get("url") or ""
    if direct_url:
        return direct_url
    formats = info.get("formats") or []
    hls_formats = [
        item for item in formats
        if item.get("url") and (
            item.get("protocol") in {"m3u8", "m3u8_native"}
            or ".m3u8" in item.get("url", "")
        )
    ]
    if hls_formats:
        return sorted(hls_formats, key=lambda item: item.get("height") or 0, reverse=True)[0]["url"]
    http_formats = [item for item in formats if item.get("url") and str(item.get("protocol", "")).startswith("http")]
    if http_formats:
        return sorted(http_formats, key=lambda item: item.get("height") or 0, reverse=True)[0]["url"]
    return ""


def youtube_video_id(player_url: str, info: dict) -> str:
    if info.get("id"):
        return str(info["id"])
    parsed = urlparse(player_url)
    if parsed.netloc.lower().endswith("youtu.be"):
        return parsed.path.strip("/")
    return parse_qs(parsed.query).get("v", [""])[0]


def resolve_webcamera_pl(player_url: str, html: str) -> str:
    video_match = VIDEO_RE.search(html)
    if video_match:
        decoded = codecs.decode(video_match.group(1), "rot_13").replace("\\/", "/")
        resolved = decoded if decoded.startswith("http") else f"https:{decoded}"
        validate_safe_url(resolved, context="webcamera_stream_url", allowed_schemes={"http", "https"})
        return resolved
    return ""


def resolve_poster(player_url: str, html: str) -> str:
    poster_match = POSTER_RE.search(html)
    if poster_match:
        poster_url = poster_match.group(1).replace("\\/", "/")
        resolved = urljoin(player_url, poster_url)
        validate_safe_url(resolved, context="poster_url", allowed_schemes={"http", "https"})
        return resolved
    return ""


def resolve_direct_urls(player_url: str, html: str) -> str:
    normalized_html = html.replace("\\/", "/")
    matches = URL_RE.findall(normalized_html)
    for match in matches:
        url = match.rstrip("\\);,")
        if url.startswith("//"):
            url = f"https:{url}"
        resolved = urljoin(player_url, url)
        validate_safe_url(resolved, context="direct_stream_url")
        return resolved
    return ""


def resolve_player_im(player_url: str, html: str) -> str:
    vars_ = dict(VAR_RE.findall(html))
    app_name = vars_.get("appismi")
    camera_name = vars_.get("kamera")
    token = vars_.get("anahtar") or vars_.get("verianahtar")
    if not app_name or not camera_name:
        return ""

    candidates = []
    hosts = [
        "https://cdn-ipv4.yayin.com.tr",
        "https://cdn-ip.yayin.com.tr",
        "https://cdn-ipv6.yayin.com.tr",
    ]
    paths = [
        f"/{app_name}/{camera_name}/playlist.m3u8",
        f"/{app_name}/{camera_name}/{camera_name}.m3u8",
        f"/{app_name}/{camera_name}/{camera_name}.stream/playlist.m3u8",
    ]
    for host in hosts:
        for path in paths:
            url = f"{host}{path}"
            if token:
                url = f"{url}?token={token}"
            candidates.append(url)

    for url in candidates:
        if is_reachable_playlist(url):
            return url
    return candidates[0] if candidates else ""


def is_reachable_playlist(url: str) -> bool:
    try:
        validate_safe_url(url, context="playlist_url", allowed_schemes={"http", "https"})
        response = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        return response.ok and "#EXTM3U" in response.text[:200]
    except requests.RequestException:
        return False
