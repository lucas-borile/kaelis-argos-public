from __future__ import annotations

import math


EARTH_RADIUS_M = 6_371_000


def haversine_offset(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    angular_distance = distance_m / EARTH_RADIUS_M

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def estimate_geo(camera: dict, bbox: dict, ref_object_height_m: float = 1.75) -> dict:
    bbox_height_px = max(float(bbox["h"]), 1.0)
    distance_m = (ref_object_height_m * camera["focal_length_px"]) / bbox_height_px
    distance_m = max(8.0, min(distance_m, 220.0))

    bbox_cx = bbox["x"] + bbox["w"] / 2
    angle_from_center = (
        (bbox_cx - camera["frame_width"] / 2) / camera["focal_length_px"]
    ) * (180 / math.pi)
    bearing = camera["azimuth_deg"] + angle_from_center
    lat, lon = haversine_offset(camera["lat"], camera["lon"], bearing, distance_m)

    return {
        "lat": lat,
        "lon": lon,
        "accuracy_m": round(max(5.0, distance_m * 0.18), 1),
        "distance_m": round(distance_m, 1),
    }
