from __future__ import annotations

import math
from typing import Any


def build_camera_cone(
    camera: dict[str, Any],
    scale_m_per_px: float,
    width: int,
    height: int,
    obstacles: list[dict[str, Any]] | None = None,
    segments: int = 36,
) -> list[dict[str, float]]:
    """Return a visual floor-plan cone. In this coordinate system, 0 deg points up."""
    scale = max(float(scale_m_per_px or 0.05), 0.0001)
    cx = float(camera.get("x_px") or 0)
    cy = float(camera.get("y_px") or 0)
    direction = float(camera.get("direction_deg") or 0)
    fov = max(1.0, min(float(camera.get("fov_deg") or 80), 360.0))
    range_px = max(1.0, float(camera.get("range_m") or 10) / scale)
    half = fov / 2
    points = [{"x": clamp(cx, 0, width), "y": clamp(cy, 0, height)}]
    for index in range(segments + 1):
        angle_deg = direction - half + (fov * index / segments)
        angle = math.radians(angle_deg - 90)
        raw_x = cx + math.cos(angle) * range_px
        raw_y = cy + math.sin(angle) * range_px
        x, y = clip_ray_to_obstacles(cx, cy, raw_x, raw_y, camera, obstacles or [])
        points.append({"x": clamp(x, 0, width), "y": clamp(y, 0, height)})
    return points


def calculate_floor_coverage(plan: dict[str, Any], cameras: list[dict[str, Any]], zones: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    width = int(plan.get("image_width_px") or 0)
    height = int(plan.get("image_height_px") or 0)
    scale = max(float(plan.get("scale_m_per_px") or 0.05), 0.0001)
    if width <= 0 or height <= 0:
        return {
            "cones": [],
            "blind_spots": [],
            "metrics": {
                "area_total_m2": 0,
                "area_covered_m2": 0,
                "coverage_percent": 0,
                "blind_spot_percent": 100,
            },
        }

    enabled_cameras = [camera for camera in cameras if camera.get("enabled", True)]
    obstacles = [zone for zone in zones or [] if zone.get("blocks_vision")]
    cones = [
        {
            "camera_id": camera["id"],
            "floor_plan_id": camera["floor_plan_id"],
            "name": camera["name"],
            "color": camera.get("color") or "#9cc7ff",
            "polygon": build_camera_cone(camera, scale, width, height, obstacles),
        }
        for camera in enabled_cameras
    ]
    covered_ratio = estimate_covered_ratio(width, height, scale, enabled_cameras, obstacles)
    total_area_m2 = width * height * scale * scale
    covered_area_m2 = total_area_m2 * covered_ratio
    return {
        "cones": cones,
        "blind_spots": [
            {
                "polygon": [
                    {"x": 0, "y": 0},
                    {"x": width, "y": 0},
                    {"x": width, "y": height},
                    {"x": 0, "y": height},
                ],
                "note": "Overlay aproximado: areas nao cobertas sao representadas visualmente pela mascara escura da planta.",
            }
        ],
        "metrics": {
            "area_total_m2": round(total_area_m2, 2),
            "area_covered_m2": round(covered_area_m2, 2),
            "coverage_percent": round(covered_ratio * 100, 1),
            "blind_spot_percent": round((1 - covered_ratio) * 100, 1),
        },
    }


def estimate_covered_ratio(width: int, height: int, scale_m_per_px: float, cameras: list[dict[str, Any]], obstacles: list[dict[str, Any]] | None = None) -> float:
    if not cameras:
        return 0.0
    target_samples = 4200
    step = max(4, int(math.sqrt((width * height) / target_samples)))
    total = 0
    covered = 0
    for y in range(step // 2, height, step):
        for x in range(step // 2, width, step):
            total += 1
            if any(point_in_camera_sector(x, y, camera, scale_m_per_px, obstacles or []) for camera in cameras):
                covered += 1
    return covered / total if total else 0.0


def point_in_camera_sector(x: float, y: float, camera: dict[str, Any], scale_m_per_px: float, obstacles: list[dict[str, Any]] | None = None) -> bool:
    cx = float(camera.get("x_px") or 0)
    cy = float(camera.get("y_px") or 0)
    dx = x - cx
    dy = y - cy
    distance_px = math.hypot(dx, dy)
    range_px = max(1.0, float(camera.get("range_m") or 10) / max(scale_m_per_px, 0.0001))
    if distance_px > range_px:
        return False
    angle = (math.degrees(math.atan2(dy, dx)) + 90) % 360
    direction = float(camera.get("direction_deg") or 0) % 360
    fov = max(1.0, min(float(camera.get("fov_deg") or 80), 360.0))
    delta = abs((angle - direction + 180) % 360 - 180)
    if delta > fov / 2:
        return False
    return not line_blocked_by_obstacle(cx, cy, x, y, camera, obstacles or [])


def clip_ray_to_obstacles(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    camera: dict[str, Any],
    obstacles: list[dict[str, Any]],
) -> tuple[float, float]:
    closest_distance = math.inf
    closest_point: tuple[float, float] | None = None
    for obstacle in visible_obstacles_for_camera(camera, obstacles):
        polygon = obstacle.get("polygon") or []
        for ax, ay, bx, by in polygon_edges(polygon):
            point = segment_intersection(x1, y1, x2, y2, ax, ay, bx, by)
            if point is None:
                continue
            distance = math.hypot(point[0] - x1, point[1] - y1)
            if 0 < distance < closest_distance:
                closest_distance = distance
                closest_point = point
    return closest_point or (x2, y2)


def line_blocked_by_obstacle(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    camera: dict[str, Any],
    obstacles: list[dict[str, Any]],
) -> bool:
    for obstacle in visible_obstacles_for_camera(camera, obstacles):
        polygon = obstacle.get("polygon") or []
        if point_in_polygon(x2, y2, polygon):
            return True
        if any(segment_intersection(x1, y1, x2, y2, ax, ay, bx, by) for ax, ay, bx, by in polygon_edges(polygon)):
            return True
    return False


def visible_obstacles_for_camera(camera: dict[str, Any], obstacles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    camera_height = float(camera.get("mount_height_m") or 2.8)
    return [
        obstacle
        for obstacle in obstacles
        if obstacle.get("blocks_vision") and float(obstacle.get("height_m") or 0) >= min(camera_height, 1.2)
    ]


def polygon_edges(polygon: list[dict[str, Any]]):
    if len(polygon) < 2:
        return
    for index, point in enumerate(polygon):
        nxt = polygon[(index + 1) % len(polygon)]
        yield float(point["x"]), float(point["y"]), float(nxt["x"]), float(nxt["y"])


def segment_intersection(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
) -> tuple[float, float] | None:
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denominator
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denominator
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def point_in_polygon(x: float, y: float, polygon: list[dict[str, Any]]) -> bool:
    inside = False
    points = [(float(point["x"]), float(point["y"])) for point in polygon]
    if len(points) < 3:
        return False
    j = len(points) - 1
    for i, point in enumerate(points):
        xi, yi = point
        xj, yj = points[j]
        intersects = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
