from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ObjectType = Literal["pessoa", "carro", "moto", "bicicleta", "onibus", "caminhao"]
DetectionProfile = Literal["near", "balanced", "far"]
CameraFrameAccess = Literal["all", "operator", "admin", "users"]


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str
    name: str
    expires_at: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)
    role: Literal["admin", "operator", "viewer"] = "viewer"
    name: str = Field(min_length=1, max_length=120)
    active: bool = True


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: Literal["admin", "operator", "viewer"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None


class Camera(BaseModel):
    id: str
    name: str
    url: str
    source_url: str
    original_stream_url: str = ""
    lat: float
    lon: float
    azimuth_deg: float
    elevation_deg: float
    height_m: float
    focal_length_px: float
    fov_h_deg: float
    frame_width: int
    frame_height: int
    capture_interval_s: int
    active: bool
    classes: list[int]
    confidence_min: float
    detection_profile: DetectionProfile = "balanced"
    yolo_model: str = ""
    yolo_imgsz: int | None = None
    person_confidence_min: float | None = None
    yolo_iou: float | None = None
    frame_access: CameraFrameAccess = "all"
    frame_access_users: list[str] = Field(default_factory=list)
    priority_level: int = Field(default=3, ge=1, le=10)
    event_priority_boost: float = Field(default=25.0, ge=0, le=100)
    event_boost_ttl_s: int = Field(default=45, ge=5, le=600)
    camera_gateway: dict | None = None


class CameraCreate(BaseModel):
    id: str | None = None
    name: str
    url: str
    source_url: str | None = None
    original_stream_url: str | None = None
    lat: float
    lon: float
    azimuth_deg: float = 0.0
    elevation_deg: float = -10.0
    height_m: float = 12.0
    focal_length_px: float = 900.0
    fov_h_deg: float = 80.0
    frame_width: int = 1280
    frame_height: int = 720
    capture_interval_s: int = 15
    active: bool = True
    classes: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 5, 7])
    confidence_min: float = 0.45
    detection_profile: DetectionProfile = "balanced"
    yolo_model: str = ""
    yolo_imgsz: int | None = Field(default=None, ge=160, le=2048)
    person_confidence_min: float | None = Field(default=None, ge=0, le=1)
    yolo_iou: float | None = Field(default=None, ge=0, le=1)
    frame_access: CameraFrameAccess = "all"
    frame_access_users: list[str] = Field(default_factory=list)
    priority_level: int = Field(default=3, ge=1, le=10)
    event_priority_boost: float = Field(default=25.0, ge=0, le=100)
    event_boost_ttl_s: int = Field(default=45, ge=5, le=600)


class CameraUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    source_url: str | None = None
    original_stream_url: str | None = None
    lat: float | None = None
    lon: float | None = None
    azimuth_deg: float | None = None
    elevation_deg: float | None = None
    height_m: float | None = None
    focal_length_px: float | None = None
    fov_h_deg: float | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    capture_interval_s: int | None = None
    active: bool | None = None
    classes: list[int] | None = None
    confidence_min: float | None = None
    detection_profile: DetectionProfile | None = None
    yolo_model: str | None = None
    yolo_imgsz: int | None = Field(default=None, ge=160, le=2048)
    person_confidence_min: float | None = Field(default=None, ge=0, le=1)
    yolo_iou: float | None = Field(default=None, ge=0, le=1)
    frame_access: CameraFrameAccess | None = None
    frame_access_users: list[str] | None = None
    priority_level: int | None = Field(default=None, ge=1, le=10)
    event_priority_boost: float | None = Field(default=None, ge=0, le=100)
    event_boost_ttl_s: int | None = Field(default=None, ge=5, le=600)
    camera_gateway: dict | None = None


class CameraDistanceCalibration(BaseModel):
    bbox_height_px: float = Field(gt=0)
    known_height_m: float = Field(gt=0, le=20)
    known_distance_m: float = Field(gt=0, le=2000)
    object_type: str = Field(default="", max_length=80)
    frame_id: int | None = Field(default=None, ge=1)


class AttentionItemUpdate(BaseModel):
    status: Literal["open", "assigned", "resolved", "dismissed"]
    resolution: str = Field(default="", max_length=160)
    comment: str = Field(default="", max_length=1000)


class AttentionQuickConfirm(BaseModel):
    action: Literal["confirm_risk", "false_alarm", "monitor", "notify_responsible", "ignore"]
    comment: str = Field(default="", max_length=1000)


class IncidentFromAttentionRequest(BaseModel):
    title: str = Field(default="", max_length=180)
    summary: str = Field(default="", max_length=1000)


class IncidentCreate(BaseModel):
    title: str = Field(default="", max_length=180)
    summary: str = Field(default="", max_length=1000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    source_kind: str = Field(default="manual", max_length=80)
    source_key: str = Field(default="", max_length=180)
    camera_id: str = Field(default="", max_length=120)
    event_id: int | None = Field(default=None, ge=1)
    frame_id: int | None = Field(default=None, ge=1)
    track_id: str = Field(default="", max_length=120)
    object_type: str = Field(default="", max_length=80)
    metadata: dict = Field(default_factory=dict)


class IncidentTimelineCreate(BaseModel):
    action: str = Field(default="note", max_length=80)
    message: str = Field(default="", max_length=2000)
    payload: dict = Field(default_factory=dict)


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=180)
    summary: str | None = Field(default=None, max_length=1000)
    status: Literal["open", "closed"] | None = None
    assigned_to: str | None = Field(default=None, max_length=80)
    resolution: str | None = Field(default=None, max_length=180)


class GeoPoint(BaseModel):
    lat: float
    lon: float
    accuracy_m: float


class LatLon(BaseModel):
    lat: float
    lon: float


class BBox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Detection(BaseModel):
    event: Literal["detection"] = "detection"
    camera_id: str
    camera_name: str
    timestamp: datetime
    object_type: ObjectType
    track_id: str | None = None
    track_score: float | None = None
    attributes: dict | None = None
    confidence: float = Field(ge=0, le=1)
    geo: GeoPoint
    bbox: BBox
    distance_m: float


class GeoFenceCreate(BaseModel):
    name: str
    points: list[LatLon]
    description: str = ""
    color: str = Field(default="#9cc7ff", pattern=r"^#[0-9A-Fa-f]{6}$")
    priority_level: int = Field(default=3, ge=1, le=10)
    active: bool = True


class GeoFenceUpdate(BaseModel):
    name: str | None = None
    points: list[LatLon] | None = None
    description: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    priority_level: int | None = Field(default=None, ge=1, le=10)
    active: bool | None = None


class GeoFenceAlertUpdate(BaseModel):
    status: Literal["open", "resolved", "dismissed"] | None = None
    resolution: str = Field(default="", max_length=180)


class GeoFenceRuleCreate(BaseModel):
    geofence_id: int
    name: str
    object_type: ObjectType | Literal["todos"] = "pessoa"
    start_time: str = "00:00"
    end_time: str = "23:59"
    active: bool = True
    object_types: list[ObjectType | Literal["todos"]] = Field(default_factory=list)
    max_stationary_seconds: int = Field(default=0, ge=0)
    max_presence_seconds: int = Field(default=0, ge=0)
    min_frames_required: int = Field(default=3, ge=1, le=1000)
    min_confidence_required: float = Field(default=0.0, ge=0, le=1)
    active_time_start: str = "00:00"
    active_time_end: str = "23:59"
    active_days: list[int] = Field(default_factory=list)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    cooldown_seconds: int = Field(default=300, ge=0)
    notification_channels: list[str] = Field(default_factory=list)
    message_template: str = Field(default="", max_length=1000)


class GeoFenceRuleUpdate(BaseModel):
    geofence_id: int | None = None
    name: str | None = None
    object_type: ObjectType | Literal["todos"] | None = None
    start_time: str | None = None
    end_time: str | None = None
    active: bool | None = None
    object_types: list[ObjectType | Literal["todos"]] | None = None
    max_stationary_seconds: int | None = Field(default=None, ge=0)
    max_presence_seconds: int | None = Field(default=None, ge=0)
    min_frames_required: int | None = Field(default=None, ge=1, le=1000)
    min_confidence_required: float | None = Field(default=None, ge=0, le=1)
    active_time_start: str | None = None
    active_time_end: str | None = None
    active_days: list[int] | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    notification_channels: list[str] | None = None
    message_template: str | None = Field(default=None, max_length=1000)


class ChatMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class FloorPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    scale_m_per_px: float = Field(default=0.05, gt=0, le=10)
    image_width_px: int = Field(default=1200, ge=100, le=20000)
    image_height_px: int = Field(default=800, ge=100, le=20000)


class FloorPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    scale_m_per_px: float | None = Field(default=None, gt=0, le=10)
    image_width_px: int | None = Field(default=None, ge=100, le=20000)
    image_height_px: int | None = Field(default=None, ge=100, le=20000)


class FloorPlanCameraCreate(BaseModel):
    linked_camera_id: str = ""
    url: str = ""
    source_url: str = ""
    name: str = Field(min_length=1, max_length=160)
    x_px: float = Field(ge=0)
    y_px: float = Field(ge=0)
    direction_deg: float = Field(default=0, ge=0, le=360)
    fov_deg: float = Field(default=80, ge=1, le=360)
    range_m: float = Field(default=12, gt=0, le=1000)
    mount_height_m: float = Field(default=2.8, gt=0, le=50)
    color: str = Field(default="#9cc7ff", pattern=r"^#[0-9A-Fa-f]{6}$")
    enabled: bool = True


class FloorPlanCameraUpdate(BaseModel):
    linked_camera_id: str | None = None
    url: str | None = None
    source_url: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    x_px: float | None = Field(default=None, ge=0)
    y_px: float | None = Field(default=None, ge=0)
    direction_deg: float | None = Field(default=None, ge=0, le=360)
    fov_deg: float | None = Field(default=None, ge=1, le=360)
    range_m: float | None = Field(default=None, gt=0, le=1000)
    mount_height_m: float | None = Field(default=None, gt=0, le=50)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    enabled: bool | None = None


class FloorPlanZonePoint(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)


class FloorPlanZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    zone_type: str = Field(default="area", max_length=80)
    polygon: list[FloorPlanZonePoint] = Field(min_length=3)
    color: str = Field(default="#2f3a46", pattern=r"^#[0-9A-Fa-f]{6}$")
    blocks_vision: bool = False
    height_m: float = Field(default=3, ge=0, le=100)


class FloorPlanZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    zone_type: str | None = Field(default=None, max_length=80)
    polygon: list[FloorPlanZonePoint] | None = Field(default=None, min_length=3)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    blocks_vision: bool | None = None
    height_m: float | None = Field(default=None, ge=0, le=100)


class IpCameraCreate(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    host: str = Field(default="", max_length=255)
    rtsp_port: int = Field(default=554, ge=1, le=65535)
    onvif_port: int = Field(default=80, ge=1, le=65535)
    username: str = Field(default="", max_length=160)
    password: str = Field(default="", max_length=512)
    rtsp_url_manual: str = Field(default="", max_length=2048)
    manufacturer: str = Field(default="", max_length=160)
    model: str = Field(default="", max_length=160)
    lat: float = 0.0
    lon: float = 0.0
    azimuth_deg: float = 0.0
    active: bool = True
    use_onvif: bool = True
    selected_profile_token: str = Field(default="", max_length=255)
    selected_stream_role: Literal["mainstream", "substream"] = "substream"


class IpCameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    host: str | None = Field(default=None, max_length=255)
    rtsp_port: int | None = Field(default=None, ge=1, le=65535)
    onvif_port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=160)
    password: str | None = Field(default=None, max_length=512)
    rtsp_url_manual: str | None = Field(default=None, max_length=2048)
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    lat: float | None = None
    lon: float | None = None
    azimuth_deg: float | None = None
    active: bool | None = None
    selected_profile_token: str | None = Field(default=None, max_length=255)
    selected_stream_role: Literal["mainstream", "substream"] | None = None


class CameraConnectionTest(BaseModel):
    rtsp_url: str = Field(default="", max_length=2048)
    username: str = Field(default="", max_length=160)
    password: str = Field(default="", max_length=512)


class OnvifDiscoveryRequest(BaseModel):
    timeout_s: float = Field(default=6, gt=0, le=30)


FeedbackType = Literal[
    "detection_correct",
    "false_positive",
    "wrong_class",
    "event_relevant",
    "event_irrelevant",
    "alert_correct",
    "alert_false",
    "camera_issue",
    "privacy_sensitive",
    "false_negative",
    "bad_bbox",
    "wrong_attribute",
    "wrong_track_association",
    "same_object",
    "different_object",
    "bad_geolocation",
]
CurationStatus = Literal["pending", "approved", "rejected", "needs_review", "privacy_blocked"]


class FeedbackCreate(BaseModel):
    feedback_type: FeedbackType
    frame_id: int | None = Field(default=None, ge=1)
    camera_id: str = ""
    track_id: str = ""
    original_value: str = ""
    corrected_value: str = ""
    confidence: int | None = Field(default=None, ge=0, le=100)
    comment: str = Field(default="", max_length=4000)
    payload: dict = Field(default_factory=dict)
    curation_status: CurationStatus = "pending"


class FeedbackUpdate(BaseModel):
    feedback_type: FeedbackType | None = None
    original_value: str | None = None
    corrected_value: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    comment: str | None = Field(default=None, max_length=4000)
    payload: dict | None = None
    curation_status: CurationStatus | None = None
    deleted: bool | None = None


class FeedbackExportRequest(BaseModel):
    start: str | None = None
    end: str | None = None
    camera_id: list[str] = Field(default_factory=list)
    feedback_type: list[FeedbackType] = Field(default_factory=list)
    curation_status: CurationStatus = "approved"
    include_sensitive: bool = False
    dataset_version: str = ""


class AnnotationBox(BaseModel):
    object_type: ObjectType
    bbox: BBox
    confidence: int | None = Field(default=100, ge=0, le=100)
    comment: str = Field(default="", max_length=1000)


class FrameAnnotationRequest(BaseModel):
    boxes: list[AnnotationBox] = Field(default_factory=list, min_length=1, max_length=100)
    curation_status: CurationStatus = "approved"
    comment: str = Field(default="", max_length=4000)


class YoloDatasetExportRequest(BaseModel):
    start: str | None = None
    end: str | None = None
    hours: int | None = Field(default=12, ge=1, le=24 * 30)
    camera_id: list[str] = Field(default_factory=list)
    curation_status: CurationStatus = "approved"
    include_sensitive: bool = False
    dataset_version: str = ""
    train_split: float = Field(default=0.8, ge=0.5, le=0.95)


class YoloTrainingRequest(BaseModel):
    dataset_version: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    model: str = Field(default="yolov8n.pt", max_length=160)
    epochs: int = Field(default=30, ge=1, le=300)
    imgsz: int = Field(default=640, ge=160, le=2048)
    batch: int = Field(default=8, ge=1, le=128)
    device: str = Field(default="cpu", max_length=40)
    dry_run: bool = False


class TelegramIntegrationUpdate(BaseModel):
    enabled: bool = False
    bot_token: str | None = None
    chat_id: str = ""
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2"] = "HTML"


class TelegramTestRequest(BaseModel):
    message: str = "Teste da integracao Telegram do Kaelis Argos."


class StressBenchmarkRequest(BaseModel):
    source: str = "backend/data/frames"
    cameras: list[int] = Field(default_factory=lambda: [5, 10, 15], min_length=1, max_length=12)
    interval: float = Field(default=10, gt=0, le=3600)
    duration_minutes: float = Field(default=1, gt=0, le=1440)
    workers: int = Field(default=2, ge=1, le=64)
    model: str = "yolov8n.pt"
    device: str = "cpu"
    imgsz: int = Field(default=512, ge=160, le=2048)
    conf: float = Field(default=0.25, ge=0, le=1)
