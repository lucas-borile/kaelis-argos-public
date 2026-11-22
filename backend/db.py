from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional unless PostgreSQL is enabled
    psycopg = None
    dict_row = None


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)
DB_PATH = Path(os.getenv("SPMU_DB_PATH", "spmu.db"))
FRAME_IMAGE_STORAGE_DIR = Path(os.getenv("FRAME_IMAGE_STORAGE_DIR", "data/frames"))
STORE_FRAME_IMAGES_IN_DB = os.getenv("STORE_FRAME_IMAGES_IN_DB", "false").lower() == "true"
TELEGRAM_BOT_TOKEN_ENV = "ARGOS_TELEGRAM_BOT_TOKEN"
TELEGRAM_SECRET_KEY_ENV = "ARGOS_TELEGRAM_SECRET_KEY"
TELEGRAM_TOKEN_PREFIX = "fernet:"
DEVELOPMENT_SEED_USERS = {
    "admin": {"password": "admin123", "role": "admin", "name": "Administrador"},
    "operator": {"password": "operator123", "role": "operator", "name": "Operador"},
    "viewer": {"password": "viewer123", "role": "viewer", "name": "Visualizador"},
}
INSECURE_SEED_PASSWORDS_BY_USER = {
    username: config["password"]
    for username, config in DEVELOPMENT_SEED_USERS.items()
}
INSECURE_SEED_PASSWORD_VALUES = set(INSECURE_SEED_PASSWORDS_BY_USER.values()) | {
    "troque-esta-senha-admin",
    "troque-esta-senha-operador",
    "troque-esta-senha-visualizador",
}
USER_ROLES = {"admin", "operator", "viewer"}
ID_TABLES = {
    "processed_frames",
    "detection_events",
    "audit_logs",
    "geofences",
    "geofence_rules",
    "geofence_alerts",
    "object_tracks",
    "track_history",
    "events",
    "attention_item_states",
    "incidents",
    "incident_timeline",
    "feature_flag_audit_log",
    "feedback_annotations",
    "feedback_dataset_exports",
    "chat_messages",
    "floor_plans",
    "floor_plan_cameras",
    "floor_plan_zones",
}


class CursorProxy:
    def __init__(self, cursor, lastrowid: int | None = None) -> None:
        self.cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return self.cursor.rowcount

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class PostgresConnection:
    def __init__(self, dsn: str) -> None:
        if psycopg is None or dict_row is None:
            raise RuntimeError("psycopg is required when DATABASE_URL is configured")
        self.connection = psycopg.connect(dsn, row_factory=dict_row)

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type:
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()

    def execute(self, sql: str, params: tuple | list | None = None) -> CursorProxy:
        sql = translate_sql(sql)
        lower = " ".join(sql.lower().split())
        returning_id = False
        if lower.startswith("insert into"):
            table = lower.split("insert into", 1)[1].strip().split()[0].strip('"')
            if table in ID_TABLES and " returning " not in lower:
                sql = f"{sql.rstrip().rstrip(';')} RETURNING id"
                returning_id = True
        cursor = self.connection.execute(sql, params or ())
        lastrowid = None
        if returning_id:
            row = cursor.fetchone()
            lastrowid = int(row["id"]) if row else None
        return CursorProxy(cursor, lastrowid)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


def translate_sql(sql: str) -> str:
    return sql.replace("?", "%s")


def get_connection() -> sqlite3.Connection:
    if USE_POSTGRES:
        return PostgresConnection(DATABASE_URL)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def database_backend() -> str:
    return "postgresql-postgis" if USE_POSTGRES else "sqlite-fallback"


def init_db() -> None:
    if USE_POSTGRES:
        init_postgres_db()
        return
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                image TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS detection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_id INTEGER NOT NULL,
                camera_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                object_type TEXT NOT NULL,
                track_id TEXT NOT NULL DEFAULT '',
                track_score REAL NOT NULL DEFAULT 1,
                confidence REAL NOT NULL,
                distance_m REAL NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                bbox_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(frame_id) REFERENCES processed_frames(id)
            );

            CREATE INDEX IF NOT EXISTS idx_frames_camera_time
                ON processed_frames(camera_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_events_time
                ON detection_events(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_events_frame
                ON detection_events(frame_id);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                ip TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_audit_time
                ON audit_logs(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_action
                ON audit_logs(action);

            CREATE TABLE IF NOT EXISTS geofences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#9cc7ff',
                priority_level INTEGER NOT NULL DEFAULT 3,
                active INTEGER NOT NULL DEFAULT 1,
                points_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS geofence_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                geofence_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                object_type TEXT NOT NULL DEFAULT 'pessoa',
                start_time TEXT NOT NULL DEFAULT '00:00',
                end_time TEXT NOT NULL DEFAULT '23:59',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(geofence_id) REFERENCES geofences(id)
            );

            CREATE TABLE IF NOT EXISTS geofence_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                geofence_id INTEGER NOT NULL,
                rule_id INTEGER NOT NULL,
                camera_id TEXT NOT NULL,
                object_type TEXT NOT NULL,
                track_id TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                resolved_at TEXT NOT NULL DEFAULT '',
                resolved_by TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                incident_id INTEGER,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_geofences_active
                ON geofences(active);
            CREATE INDEX IF NOT EXISTS idx_geofence_rules_active
                ON geofence_rules(active);
            CREATE INDEX IF NOT EXISTS idx_geofence_alerts_time
                ON geofence_alerts(timestamp DESC);

            CREATE TABLE IF NOT EXISTS object_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id TEXT NOT NULL UNIQUE,
                camera_id TEXT NOT NULL,
                object_type TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                first_confirmed_at TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL,
                current_geofence_id INTEGER,
                entered_geofence_at TEXT NOT NULL DEFAULT '',
                stationary_candidate_started_at TEXT NOT NULL DEFAULT '',
                stationary_started_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                frames_seen_count INTEGER NOT NULL DEFAULT 0,
                consecutive_frames_seen_count INTEGER NOT NULL DEFAULT 0,
                frames_missing_count INTEGER NOT NULL DEFAULT 0,
                last_position_x REAL,
                last_position_y REAL,
                last_lat REAL,
                last_lng REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_object_tracks_status
                ON object_tracks(status);
            CREATE INDEX IF NOT EXISTS idx_object_tracks_camera
                ON object_tracks(camera_id, last_seen_at DESC);

            CREATE TABLE IF NOT EXISTS track_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                frame_id INTEGER,
                timestamp TEXT NOT NULL,
                bbox_x REAL NOT NULL,
                bbox_y REAL NOT NULL,
                bbox_w REAL NOT NULL,
                bbox_h REAL NOT NULL,
                centroid_x REAL NOT NULL,
                centroid_y REAL NOT NULL,
                lat REAL,
                lng REAL,
                geofence_id INTEGER,
                detection_confidence REAL NOT NULL DEFAULT 0,
                snapshot_path TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_track_history_track_time
                ON track_history(track_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_track_history_frame
                ON track_history(frame_id);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                rule_id INTEGER,
                track_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                geofence_id INTEGER,
                object_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                seen_duration_seconds INTEGER NOT NULL DEFAULT 0,
                geofence_duration_seconds INTEGER NOT NULL DEFAULT 0,
                stationary_duration_seconds INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                first_evidence_at TEXT NOT NULL,
                last_evidence_at TEXT NOT NULL,
                snapshot_path TEXT NOT NULL DEFAULT '',
                frame_reference INTEGER,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                acknowledged_at TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_events_created_at
                ON events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_track
                ON events(track_id);
            CREATE INDEX IF NOT EXISTS idx_events_rule_track
                ON events(rule_id, track_id, geofence_id);

            CREATE TABLE IF NOT EXISTS attention_item_states (
                item_key TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'open',
                assigned_to TEXT NOT NULL DEFAULT '',
                assigned_at TEXT NOT NULL DEFAULT '',
                resolved_at TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                quick_action TEXT NOT NULL DEFAULT '',
                confirmed_at TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_attention_state_status
                ON attention_item_states(status);

            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                severity TEXT NOT NULL DEFAULT 'medium',
                source_kind TEXT NOT NULL DEFAULT '',
                source_key TEXT NOT NULL DEFAULT '',
                attention_item_key TEXT NOT NULL DEFAULT '',
                camera_id TEXT NOT NULL DEFAULT '',
                event_id INTEGER,
                frame_id INTEGER,
                track_id TEXT NOT NULL DEFAULT '',
                object_type TEXT NOT NULL DEFAULT '',
                assigned_to TEXT NOT NULL DEFAULT '',
                opened_at TEXT NOT NULL,
                opened_by TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT '',
                closed_by TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_incidents_status
                ON incidents(status, opened_at DESC);
            CREATE INDEX IF NOT EXISTS idx_incidents_attention
                ON incidents(attention_item_key);

            CREATE TABLE IF NOT EXISTS incident_timeline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_incident_timeline_incident
                ON incident_timeline(incident_id, timestamp ASC);

            CREATE TABLE IF NOT EXISTS feedback_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                frame_id INTEGER,
                camera_id TEXT NOT NULL DEFAULT '',
                track_id TEXT NOT NULL DEFAULT '',
                feedback_type TEXT NOT NULL,
                original_value TEXT NOT NULL DEFAULT '',
                corrected_value TEXT NOT NULL DEFAULT '',
                confidence INTEGER,
                reviewer_id TEXT NOT NULL DEFAULT '',
                reviewer_role TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                curation_status TEXT NOT NULL DEFAULT 'pending',
                deleted_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_event_id ON feedback_annotations(event_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_camera_id ON feedback_annotations(camera_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_track_id ON feedback_annotations(track_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback_annotations(feedback_type);
            CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback_annotations(created_at);
            CREATE INDEX IF NOT EXISTS idx_feedback_curation ON feedback_annotations(curation_status);

            CREATE TABLE IF NOT EXISTS feedback_dataset_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                filters_json TEXT NOT NULL DEFAULT '{}',
                output_path TEXT NOT NULL DEFAULT '',
                items_count INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                body TEXT NOT NULL,
                mentions_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_chat_messages_time
                ON chat_messages(timestamp DESC);

            CREATE TABLE IF NOT EXISTS telegram_integration (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                bot_token TEXT NOT NULL DEFAULT '',
                chat_id TEXT NOT NULL DEFAULT '',
                parse_mode TEXT NOT NULL DEFAULT 'HTML',
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_app_users_role
                ON app_users(role);

            CREATE TABLE IF NOT EXISTS app_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(username) REFERENCES app_users(username) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_app_sessions_user
                ON app_sessions(username);
            CREATE INDEX IF NOT EXISTS idx_app_sessions_expires
                ON app_sessions(expires_at);

            CREATE TABLE IF NOT EXISTS feature_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_key TEXT NOT NULL UNIQUE,
                feature_name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                processing_cost_level TEXT NOT NULL DEFAULT 'baixo',
                cpu_cost TEXT NOT NULL DEFAULT 'baixo',
                gpu_cost TEXT NOT NULL DEFAULT 'nenhum',
                memory_cost TEXT NOT NULL DEFAULT 'baixo',
                storage_cost TEXT NOT NULL DEFAULT 'baixo',
                network_cost TEXT NOT NULL DEFAULT 'baixo',
                estimated_processing_impact TEXT NOT NULL DEFAULT '',
                disable_consequence TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS feature_flag_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_key TEXT NOT NULL,
                previous_enabled INTEGER,
                new_enabled INTEGER NOT NULL,
                changed_by TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_feature_audit_time
                ON feature_flag_audit_log(changed_at DESC);

            CREATE TABLE IF NOT EXISTS cameras (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                source_url TEXT NOT NULL,
                original_stream_url TEXT NOT NULL DEFAULT '',
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                azimuth_deg REAL NOT NULL,
                elevation_deg REAL NOT NULL,
                height_m REAL NOT NULL,
                focal_length_px REAL NOT NULL,
                fov_h_deg REAL NOT NULL,
                frame_width INTEGER NOT NULL,
                frame_height INTEGER NOT NULL,
                capture_interval_s INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                classes_json TEXT NOT NULL,
                confidence_min REAL NOT NULL,
                detection_profile TEXT NOT NULL DEFAULT 'balanced',
                yolo_model TEXT NOT NULL DEFAULT '',
                yolo_imgsz INTEGER,
                person_confidence_min REAL,
                yolo_iou REAL,
                frame_access TEXT NOT NULL DEFAULT 'all',
                frame_access_users_json TEXT NOT NULL DEFAULT '[]',
                priority_level INTEGER NOT NULL DEFAULT 3,
                event_priority_boost REAL NOT NULL DEFAULT 25,
                event_boost_ttl_s INTEGER NOT NULL DEFAULT 45,
                camera_gateway_json TEXT NOT NULL DEFAULT '{}',
                camera_secret TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS floor_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                image_width_px INTEGER NOT NULL DEFAULT 0,
                image_height_px INTEGER NOT NULL DEFAULT 0,
                scale_m_per_px REAL NOT NULL DEFAULT 0.05,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS floor_plan_cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                floor_plan_id INTEGER NOT NULL,
                linked_camera_id TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                x_px REAL NOT NULL,
                y_px REAL NOT NULL,
                direction_deg REAL NOT NULL DEFAULT 0,
                fov_deg REAL NOT NULL DEFAULT 80,
                range_m REAL NOT NULL DEFAULT 12,
                mount_height_m REAL NOT NULL DEFAULT 2.8,
                color TEXT NOT NULL DEFAULT '#9cc7ff',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(floor_plan_id) REFERENCES floor_plans(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_floor_plan_cameras_plan
                ON floor_plan_cameras(floor_plan_id);

            CREATE TABLE IF NOT EXISTS floor_plan_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                floor_plan_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                zone_type TEXT NOT NULL DEFAULT 'area',
                polygon_json TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '#2f3a46',
                blocks_vision INTEGER NOT NULL DEFAULT 0,
                height_m REAL NOT NULL DEFAULT 3,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(floor_plan_id) REFERENCES floor_plans(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_floor_plan_zones_plan
                ON floor_plan_zones(floor_plan_id);
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(detection_events)").fetchall()}
        if "track_id" not in columns:
            connection.execute("ALTER TABLE detection_events ADD COLUMN track_id TEXT NOT NULL DEFAULT ''")
        if "track_score" not in columns:
            connection.execute("ALTER TABLE detection_events ADD COLUMN track_score REAL NOT NULL DEFAULT 1")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_track ON detection_events(track_id)")
        frame_columns = {row["name"] for row in connection.execute("PRAGMA table_info(processed_frames)").fetchall()}
        if "image_path" not in frame_columns:
            connection.execute("ALTER TABLE processed_frames ADD COLUMN image_path TEXT NOT NULL DEFAULT ''")
        camera_columns = {row["name"] for row in connection.execute("PRAGMA table_info(cameras)").fetchall()}
        if "detection_profile" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN detection_profile TEXT NOT NULL DEFAULT 'balanced'")
        if "yolo_model" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN yolo_model TEXT NOT NULL DEFAULT ''")
        if "yolo_imgsz" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN yolo_imgsz INTEGER")
        if "person_confidence_min" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN person_confidence_min REAL")
        if "yolo_iou" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN yolo_iou REAL")
        if "frame_access" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN frame_access TEXT NOT NULL DEFAULT 'all'")
        if "frame_access_users_json" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN frame_access_users_json TEXT NOT NULL DEFAULT '[]'")
        if "priority_level" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN priority_level INTEGER NOT NULL DEFAULT 3")
        if "event_priority_boost" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN event_priority_boost REAL NOT NULL DEFAULT 25")
        if "event_boost_ttl_s" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN event_boost_ttl_s INTEGER NOT NULL DEFAULT 45")
        if "camera_gateway_json" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN camera_gateway_json TEXT NOT NULL DEFAULT '{}'")
        if "camera_secret" not in camera_columns:
            connection.execute("ALTER TABLE cameras ADD COLUMN camera_secret TEXT NOT NULL DEFAULT ''")
        geofence_columns = {row["name"] for row in connection.execute("PRAGMA table_info(geofences)").fetchall()}
        if "color" not in geofence_columns:
            connection.execute("ALTER TABLE geofences ADD COLUMN color TEXT NOT NULL DEFAULT '#9cc7ff'")
        if "priority_level" not in geofence_columns:
            connection.execute("ALTER TABLE geofences ADD COLUMN priority_level INTEGER NOT NULL DEFAULT 3")
        alert_columns = {row["name"] for row in connection.execute("PRAGMA table_info(geofence_alerts)").fetchall()}
        geofence_alert_additions = {
            "status": "TEXT NOT NULL DEFAULT 'open'",
            "resolved_at": "TEXT NOT NULL DEFAULT ''",
            "resolved_by": "TEXT NOT NULL DEFAULT ''",
            "resolution": "TEXT NOT NULL DEFAULT ''",
            "incident_id": "INTEGER",
        }
        for column, definition in geofence_alert_additions.items():
            if column not in alert_columns:
                connection.execute(f"ALTER TABLE geofence_alerts ADD COLUMN {column} {definition}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_geofence_alerts_status ON geofence_alerts(status, timestamp DESC)")
        rule_columns = {row["name"] for row in connection.execute("PRAGMA table_info(geofence_rules)").fetchall()}
        geofence_rule_additions = {
            "object_types": "TEXT NOT NULL DEFAULT '[]'",
            "max_stationary_seconds": "INTEGER NOT NULL DEFAULT 0",
            "max_presence_seconds": "INTEGER NOT NULL DEFAULT 0",
            "min_frames_required": "INTEGER NOT NULL DEFAULT 3",
            "min_confidence_required": "REAL NOT NULL DEFAULT 0",
            "active_time_start": "TEXT NOT NULL DEFAULT '00:00'",
            "active_time_end": "TEXT NOT NULL DEFAULT '23:59'",
            "active_days": "TEXT NOT NULL DEFAULT '[]'",
            "severity": "TEXT NOT NULL DEFAULT 'medium'",
            "cooldown_seconds": "INTEGER NOT NULL DEFAULT 300",
            "notification_channels": "TEXT NOT NULL DEFAULT '[]'",
            "message_template": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in geofence_rule_additions.items():
            if column not in rule_columns:
                connection.execute(f"ALTER TABLE geofence_rules ADD COLUMN {column} {definition}")
        floor_camera_columns = {row["name"] for row in connection.execute("PRAGMA table_info(floor_plan_cameras)").fetchall()}
        if "url" not in floor_camera_columns:
            connection.execute("ALTER TABLE floor_plan_cameras ADD COLUMN url TEXT NOT NULL DEFAULT ''")
        if "source_url" not in floor_camera_columns:
            connection.execute("ALTER TABLE floor_plan_cameras ADD COLUMN source_url TEXT NOT NULL DEFAULT ''")
        if "mount_height_m" not in floor_camera_columns:
            connection.execute("ALTER TABLE floor_plan_cameras ADD COLUMN mount_height_m REAL NOT NULL DEFAULT 2.8")
        floor_zone_columns = {row["name"] for row in connection.execute("PRAGMA table_info(floor_plan_zones)").fetchall()}
        if "blocks_vision" not in floor_zone_columns:
            connection.execute("ALTER TABLE floor_plan_zones ADD COLUMN blocks_vision INTEGER NOT NULL DEFAULT 0")
        if "height_m" not in floor_zone_columns:
            connection.execute("ALTER TABLE floor_plan_zones ADD COLUMN height_m REAL NOT NULL DEFAULT 3")
        feature_columns = {row["name"] for row in connection.execute("PRAGMA table_info(feature_flags)").fetchall()}
        feature_flag_additions = {
            "description": "TEXT NOT NULL DEFAULT ''",
            "category": "TEXT NOT NULL DEFAULT ''",
            "processing_cost_level": "TEXT NOT NULL DEFAULT 'baixo'",
            "cpu_cost": "TEXT NOT NULL DEFAULT 'baixo'",
            "gpu_cost": "TEXT NOT NULL DEFAULT 'nenhum'",
            "memory_cost": "TEXT NOT NULL DEFAULT 'baixo'",
            "storage_cost": "TEXT NOT NULL DEFAULT 'baixo'",
            "network_cost": "TEXT NOT NULL DEFAULT 'baixo'",
            "estimated_processing_impact": "TEXT NOT NULL DEFAULT ''",
            "disable_consequence": "TEXT NOT NULL DEFAULT ''",
            "reason": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "updated_by": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in feature_flag_additions.items():
            if column not in feature_columns:
                connection.execute(f"ALTER TABLE feature_flags ADD COLUMN {column} {definition}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attention_item_states (
                item_key TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'open',
                assigned_to TEXT NOT NULL DEFAULT '',
                assigned_at TEXT NOT NULL DEFAULT '',
                resolved_at TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                quick_action TEXT NOT NULL DEFAULT '',
                confirmed_at TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_attention_state_status ON attention_item_states(status)")
        attention_columns = {row["name"] for row in connection.execute("PRAGMA table_info(attention_item_states)").fetchall()}
        if "quick_action" not in attention_columns:
            connection.execute("ALTER TABLE attention_item_states ADD COLUMN quick_action TEXT NOT NULL DEFAULT ''")
        if "confirmed_at" not in attention_columns:
            connection.execute("ALTER TABLE attention_item_states ADD COLUMN confirmed_at TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                severity TEXT NOT NULL DEFAULT 'medium',
                source_kind TEXT NOT NULL DEFAULT '',
                source_key TEXT NOT NULL DEFAULT '',
                attention_item_key TEXT NOT NULL DEFAULT '',
                camera_id TEXT NOT NULL DEFAULT '',
                event_id INTEGER,
                frame_id INTEGER,
                track_id TEXT NOT NULL DEFAULT '',
                object_type TEXT NOT NULL DEFAULT '',
                assigned_to TEXT NOT NULL DEFAULT '',
                opened_at TEXT NOT NULL,
                opened_by TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT '',
                closed_by TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, opened_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_incidents_attention ON incidents(attention_item_key)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incident_timeline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_incident_timeline_incident ON incident_timeline(incident_id, timestamp ASC)")
        seed_feature_flags(connection)
        seed_default_users(connection)
        seed_default_cameras(connection)


def init_postgres_db() -> None:
    with get_connection() as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_frames (
                id BIGSERIAL PRIMARY KEY,
                camera_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                image TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                payload_json JSONB NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_frames_camera_time ON processed_frames(camera_id, timestamp DESC)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_events (
                id BIGSERIAL PRIMARY KEY,
                frame_id BIGINT NOT NULL REFERENCES processed_frames(id) ON DELETE CASCADE,
                camera_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                object_type TEXT NOT NULL,
                track_id TEXT NOT NULL DEFAULT '',
                track_score DOUBLE PRECISION NOT NULL DEFAULT 1,
                confidence DOUBLE PRECISION NOT NULL,
                distance_m DOUBLE PRECISION NOT NULL,
                lat DOUBLE PRECISION NOT NULL,
                lon DOUBLE PRECISION NOT NULL,
                geom geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
                bbox_json JSONB NOT NULL,
                payload_json JSONB NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON detection_events(timestamp DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_frame ON detection_events(frame_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_track ON detection_events(track_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_geom ON detection_events USING GIST(geom)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id BIGSERIAL PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                ip TEXT NOT NULL DEFAULT '',
                details_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS geofences (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#9cc7ff',
                priority_level INTEGER NOT NULL DEFAULT 3,
                active INTEGER NOT NULL DEFAULT 1,
                points_json JSONB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS geofence_rules (
                id BIGSERIAL PRIMARY KEY,
                geofence_id BIGINT NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                object_type TEXT NOT NULL DEFAULT 'pessoa',
                start_time TEXT NOT NULL DEFAULT '00:00',
                end_time TEXT NOT NULL DEFAULT '23:59',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS geofence_alerts (
                id BIGSERIAL PRIMARY KEY,
                geofence_id BIGINT NOT NULL,
                rule_id BIGINT NOT NULL,
                camera_id TEXT NOT NULL,
                object_type TEXT NOT NULL,
                track_id TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL,
                lat DOUBLE PRECISION NOT NULL,
                lon DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                resolved_at TEXT NOT NULL DEFAULT '',
                resolved_by TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                incident_id BIGINT,
                geom geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
                payload_json JSONB NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_geofences_active ON geofences(active)")
        connection.execute("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS priority_level INTEGER NOT NULL DEFAULT 3")
        connection.execute("ALTER TABLE geofence_alerts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'open'")
        connection.execute("ALTER TABLE geofence_alerts ADD COLUMN IF NOT EXISTS resolved_at TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE geofence_alerts ADD COLUMN IF NOT EXISTS resolved_by TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE geofence_alerts ADD COLUMN IF NOT EXISTS resolution TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE geofence_alerts ADD COLUMN IF NOT EXISTS incident_id BIGINT")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_geofence_rules_active ON geofence_rules(active)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_geofence_alerts_time ON geofence_alerts(timestamp DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_geofence_alerts_status ON geofence_alerts(status, timestamp DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_geofence_alerts_geom ON geofence_alerts USING GIST(geom)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS object_tracks (
                id BIGSERIAL PRIMARY KEY,
                track_id TEXT NOT NULL UNIQUE,
                camera_id TEXT NOT NULL,
                object_type TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                first_confirmed_at TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL,
                current_geofence_id BIGINT,
                entered_geofence_at TEXT NOT NULL DEFAULT '',
                stationary_candidate_started_at TEXT NOT NULL DEFAULT '',
                stationary_started_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                frames_seen_count INTEGER NOT NULL DEFAULT 0,
                consecutive_frames_seen_count INTEGER NOT NULL DEFAULT 0,
                frames_missing_count INTEGER NOT NULL DEFAULT 0,
                last_position_x DOUBLE PRECISION,
                last_position_y DOUBLE PRECISION,
                last_lat DOUBLE PRECISION,
                last_lng DOUBLE PRECISION,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_object_tracks_status ON object_tracks(status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_object_tracks_camera ON object_tracks(camera_id, last_seen_at DESC)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS track_history (
                id BIGSERIAL PRIMARY KEY,
                track_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                frame_id BIGINT,
                timestamp TEXT NOT NULL,
                bbox_x DOUBLE PRECISION NOT NULL,
                bbox_y DOUBLE PRECISION NOT NULL,
                bbox_w DOUBLE PRECISION NOT NULL,
                bbox_h DOUBLE PRECISION NOT NULL,
                centroid_x DOUBLE PRECISION NOT NULL,
                centroid_y DOUBLE PRECISION NOT NULL,
                lat DOUBLE PRECISION,
                lng DOUBLE PRECISION,
                geofence_id BIGINT,
                detection_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                snapshot_path TEXT NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_track_history_track_time ON track_history(track_id, timestamp DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_track_history_frame ON track_history(frame_id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id BIGSERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                rule_id BIGINT,
                track_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                geofence_id BIGINT,
                object_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                seen_duration_seconds INTEGER NOT NULL DEFAULT 0,
                geofence_duration_seconds INTEGER NOT NULL DEFAULT 0,
                stationary_duration_seconds INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                first_evidence_at TEXT NOT NULL,
                last_evidence_at TEXT NOT NULL,
                snapshot_path TEXT NOT NULL DEFAULT '',
                frame_reference BIGINT,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                acknowledged_at TEXT NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_track ON events(track_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_rule_track ON events(rule_id, track_id, geofence_id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attention_item_states (
                item_key TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'open',
                assigned_to TEXT NOT NULL DEFAULT '',
                assigned_at TEXT NOT NULL DEFAULT '',
                resolved_at TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                quick_action TEXT NOT NULL DEFAULT '',
                confirmed_at TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_attention_state_status ON attention_item_states(status)")
        connection.execute("ALTER TABLE attention_item_states ADD COLUMN IF NOT EXISTS quick_action TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE attention_item_states ADD COLUMN IF NOT EXISTS confirmed_at TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id BIGSERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                severity TEXT NOT NULL DEFAULT 'medium',
                source_kind TEXT NOT NULL DEFAULT '',
                source_key TEXT NOT NULL DEFAULT '',
                attention_item_key TEXT NOT NULL DEFAULT '',
                camera_id TEXT NOT NULL DEFAULT '',
                event_id BIGINT,
                frame_id BIGINT,
                track_id TEXT NOT NULL DEFAULT '',
                object_type TEXT NOT NULL DEFAULT '',
                assigned_to TEXT NOT NULL DEFAULT '',
                opened_at TEXT NOT NULL,
                opened_by TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT '',
                closed_by TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, opened_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_incidents_attention ON incidents(attention_item_key)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incident_timeline (
                id BIGSERIAL PRIMARY KEY,
                incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_incident_timeline_incident ON incident_timeline(incident_id, timestamp ASC)")
        for statement in [
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS object_types JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS max_stationary_seconds INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS max_presence_seconds INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS min_frames_required INTEGER NOT NULL DEFAULT 3",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS min_confidence_required DOUBLE PRECISION NOT NULL DEFAULT 0",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS active_time_start TEXT NOT NULL DEFAULT '00:00'",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS active_time_end TEXT NOT NULL DEFAULT '23:59'",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS active_days JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT 'medium'",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS cooldown_seconds INTEGER NOT NULL DEFAULT 300",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS notification_channels JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE geofence_rules ADD COLUMN IF NOT EXISTS message_template TEXT NOT NULL DEFAULT ''",
        ]:
            connection.execute(statement)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_annotations (
                id BIGSERIAL PRIMARY KEY,
                event_id BIGINT,
                frame_id BIGINT,
                camera_id TEXT NOT NULL DEFAULT '',
                track_id TEXT NOT NULL DEFAULT '',
                feedback_type TEXT NOT NULL,
                original_value TEXT NOT NULL DEFAULT '',
                corrected_value TEXT NOT NULL DEFAULT '',
                confidence INTEGER,
                reviewer_id TEXT NOT NULL DEFAULT '',
                reviewer_role TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                curation_status TEXT NOT NULL DEFAULT 'pending',
                deleted_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_feedback_event_id ON feedback_annotations(event_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_feedback_camera_id ON feedback_annotations(camera_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_feedback_track_id ON feedback_annotations(track_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback_annotations(feedback_type)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback_annotations(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_feedback_curation ON feedback_annotations(curation_status)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_dataset_exports (
                id BIGSERIAL PRIMARY KEY,
                dataset_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                filters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                output_path TEXT NOT NULL DEFAULT '',
                items_count INTEGER NOT NULL DEFAULT 0,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id BIGSERIAL PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                body TEXT NOT NULL,
                mentions_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_time ON chat_messages(timestamp DESC)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_integration (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                bot_token TEXT NOT NULL DEFAULT '',
                chat_id TEXT NOT NULL DEFAULT '',
                parse_mode TEXT NOT NULL DEFAULT 'HTML',
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_app_users_role ON app_users(role)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(username) REFERENCES app_users(username) ON DELETE CASCADE
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_app_sessions_user ON app_sessions(username)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_app_sessions_expires ON app_sessions(expires_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_flags (
                id BIGSERIAL PRIMARY KEY,
                feature_key TEXT NOT NULL UNIQUE,
                feature_name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                processing_cost_level TEXT NOT NULL DEFAULT 'baixo',
                cpu_cost TEXT NOT NULL DEFAULT 'baixo',
                gpu_cost TEXT NOT NULL DEFAULT 'nenhum',
                memory_cost TEXT NOT NULL DEFAULT 'baixo',
                storage_cost TEXT NOT NULL DEFAULT 'baixo',
                network_cost TEXT NOT NULL DEFAULT 'baixo',
                estimated_processing_impact TEXT NOT NULL DEFAULT '',
                disable_consequence TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_flag_audit_log (
                id BIGSERIAL PRIMARY KEY,
                feature_key TEXT NOT NULL,
                previous_enabled INTEGER,
                new_enabled INTEGER NOT NULL,
                changed_by TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_feature_audit_time ON feature_flag_audit_log(changed_at DESC)")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS processing_cost_level TEXT NOT NULL DEFAULT 'baixo'")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS cpu_cost TEXT NOT NULL DEFAULT 'baixo'")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS gpu_cost TEXT NOT NULL DEFAULT 'nenhum'")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS memory_cost TEXT NOT NULL DEFAULT 'baixo'")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS storage_cost TEXT NOT NULL DEFAULT 'baixo'")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS network_cost TEXT NOT NULL DEFAULT 'baixo'")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS estimated_processing_impact TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS disable_consequence TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS created_at TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS updated_at TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE feature_flags ADD COLUMN IF NOT EXISTS updated_by TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cameras (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                source_url TEXT NOT NULL,
                original_stream_url TEXT NOT NULL DEFAULT '',
                lat DOUBLE PRECISION NOT NULL,
                lon DOUBLE PRECISION NOT NULL,
                geom geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
                azimuth_deg DOUBLE PRECISION NOT NULL,
                elevation_deg DOUBLE PRECISION NOT NULL,
                height_m DOUBLE PRECISION NOT NULL,
                focal_length_px DOUBLE PRECISION NOT NULL,
                fov_h_deg DOUBLE PRECISION NOT NULL,
                frame_width INTEGER NOT NULL,
                frame_height INTEGER NOT NULL,
                capture_interval_s INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                classes_json JSONB NOT NULL,
                confidence_min DOUBLE PRECISION NOT NULL,
                detection_profile TEXT NOT NULL DEFAULT 'balanced',
                yolo_model TEXT NOT NULL DEFAULT '',
                yolo_imgsz INTEGER,
                person_confidence_min DOUBLE PRECISION,
                yolo_iou DOUBLE PRECISION,
                frame_access TEXT NOT NULL DEFAULT 'all',
                frame_access_users_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                priority_level INTEGER NOT NULL DEFAULT 3,
                event_priority_boost DOUBLE PRECISION NOT NULL DEFAULT 25,
                event_boost_ttl_s INTEGER NOT NULL DEFAULT 45,
                camera_gateway_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                camera_secret TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS detection_profile TEXT NOT NULL DEFAULT 'balanced'")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS yolo_model TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS yolo_imgsz INTEGER")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS person_confidence_min DOUBLE PRECISION")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS yolo_iou DOUBLE PRECISION")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS frame_access TEXT NOT NULL DEFAULT 'all'")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS frame_access_users_json JSONB NOT NULL DEFAULT '[]'::jsonb")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS priority_level INTEGER NOT NULL DEFAULT 3")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS event_priority_boost DOUBLE PRECISION NOT NULL DEFAULT 25")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS event_boost_ttl_s INTEGER NOT NULL DEFAULT 45")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS camera_gateway_json JSONB NOT NULL DEFAULT '{}'::jsonb")
        connection.execute("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS camera_secret TEXT NOT NULL DEFAULT ''")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_cameras_active ON cameras(active)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_cameras_geom ON cameras USING GIST(geom)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS floor_plans (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                image_width_px INTEGER NOT NULL DEFAULT 0,
                image_height_px INTEGER NOT NULL DEFAULT 0,
                scale_m_per_px DOUBLE PRECISION NOT NULL DEFAULT 0.05,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS floor_plan_cameras (
                id BIGSERIAL PRIMARY KEY,
                floor_plan_id BIGINT NOT NULL REFERENCES floor_plans(id) ON DELETE CASCADE,
                linked_camera_id TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                x_px DOUBLE PRECISION NOT NULL,
                y_px DOUBLE PRECISION NOT NULL,
                direction_deg DOUBLE PRECISION NOT NULL DEFAULT 0,
                fov_deg DOUBLE PRECISION NOT NULL DEFAULT 80,
                range_m DOUBLE PRECISION NOT NULL DEFAULT 12,
                mount_height_m DOUBLE PRECISION NOT NULL DEFAULT 2.8,
                color TEXT NOT NULL DEFAULT '#9cc7ff',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_floor_plan_cameras_plan ON floor_plan_cameras(floor_plan_id)")
        connection.execute("ALTER TABLE floor_plan_cameras ADD COLUMN IF NOT EXISTS url TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE floor_plan_cameras ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE floor_plan_cameras ADD COLUMN IF NOT EXISTS mount_height_m DOUBLE PRECISION NOT NULL DEFAULT 2.8")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS floor_plan_zones (
                id BIGSERIAL PRIMARY KEY,
                floor_plan_id BIGINT NOT NULL REFERENCES floor_plans(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                zone_type TEXT NOT NULL DEFAULT 'area',
                polygon_json JSONB NOT NULL,
                color TEXT NOT NULL DEFAULT '#2f3a46',
                blocks_vision INTEGER NOT NULL DEFAULT 0,
                height_m DOUBLE PRECISION NOT NULL DEFAULT 3,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_floor_plan_zones_plan ON floor_plan_zones(floor_plan_id)")
        connection.execute("ALTER TABLE floor_plan_zones ADD COLUMN IF NOT EXISTS blocks_vision INTEGER NOT NULL DEFAULT 0")
        connection.execute("ALTER TABLE floor_plan_zones ADD COLUMN IF NOT EXISTS height_m DOUBLE PRECISION NOT NULL DEFAULT 3")
        seed_feature_flags(connection)
        seed_default_users(connection)
        seed_default_cameras(connection)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def load_json(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def seed_feature_flags(connection) -> None:
    from feature_manager import DEFAULT_FEATURES

    now = _utc_now()
    for feature in DEFAULT_FEATURES:
        existing = connection.execute(
            "SELECT id FROM feature_flags WHERE feature_key = ?",
            (feature["feature_key"],),
        ).fetchone()
        values = (
            feature["feature_name"],
            feature.get("description", ""),
            feature.get("category", ""),
            feature.get("processing_cost_level", "baixo"),
            feature.get("cpu_cost", "baixo"),
            feature.get("gpu_cost", "nenhum"),
            feature.get("memory_cost", "baixo"),
            feature.get("storage_cost", "baixo"),
            feature.get("network_cost", "baixo"),
            feature.get("estimated_processing_impact", ""),
            feature.get("disable_consequence", ""),
        )
        if existing:
            connection.execute(
                """
                UPDATE feature_flags
                SET feature_name = ?,
                    description = ?,
                    category = ?,
                    processing_cost_level = ?,
                    cpu_cost = ?,
                    gpu_cost = ?,
                    memory_cost = ?,
                    storage_cost = ?,
                    network_cost = ?,
                    estimated_processing_impact = ?,
                    disable_consequence = ?
                WHERE feature_key = ?
                """,
                (*values, feature["feature_key"]),
            )
            continue
        connection.execute(
            """
            INSERT INTO feature_flags (
                feature_key, feature_name, description, category, enabled,
                processing_cost_level, cpu_cost, gpu_cost, memory_cost, storage_cost,
                network_cost, estimated_processing_impact, disable_consequence,
                reason, created_at, updated_at, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 'seed')
            """,
            (
                feature["feature_key"],
                *values[:3],
                1 if feature.get("enabled", True) else 0,
                *values[3:],
                now,
                now,
            ),
        )


def list_feature_flags() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM feature_flags
            ORDER BY category ASC, feature_name ASC
            """
        ).fetchall()
    return [feature_flag_from_row(row) for row in rows]


def get_feature_flag(feature_key: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM feature_flags WHERE feature_key = ?", (feature_key,)).fetchone()
    return feature_flag_from_row(row) if row else None


def update_feature_flag(feature_key: str, enabled: bool, actor: str = "", reason: str = "") -> dict[str, Any]:
    from feature_manager import FEATURE_BY_KEY

    if feature_key not in FEATURE_BY_KEY:
        raise ValueError("unknown feature")
    previous = get_feature_flag(feature_key)
    if previous is None:
        with get_connection() as connection:
            seed_feature_flags(connection)
        previous = get_feature_flag(feature_key)
    now = _utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE feature_flags
            SET enabled = ?, reason = ?, updated_at = ?, updated_by = ?
            WHERE feature_key = ?
            """,
            (1 if enabled else 0, reason, now, actor, feature_key),
        )
        connection.execute(
            """
            INSERT INTO feature_flag_audit_log (
                feature_key, previous_enabled, new_enabled, changed_by, changed_at, reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                feature_key,
                None if previous is None else 1 if previous.get("enabled") else 0,
                1 if enabled else 0,
                actor,
                now,
                reason,
            ),
        )
    item = get_feature_flag(feature_key)
    if item is None:
        raise RuntimeError("failed to update feature flag")
    return item


def list_feature_flag_audit_log(limit: int = 100, feature_key: str | None = None) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if feature_key:
        clauses.append("feature_key = ?")
        params.append(feature_key)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM feature_flag_audit_log
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [feature_flag_audit_from_row(row) for row in rows]


def feature_flag_from_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "feature_key": row["feature_key"],
        "feature_name": row["feature_name"],
        "description": row["description"],
        "category": row["category"],
        "enabled": bool(row["enabled"]),
        "processing_cost_level": row["processing_cost_level"],
        "cpu_cost": row["cpu_cost"],
        "gpu_cost": row["gpu_cost"],
        "memory_cost": row["memory_cost"],
        "storage_cost": row["storage_cost"],
        "network_cost": row["network_cost"],
        "estimated_processing_impact": row["estimated_processing_impact"],
        "disable_consequence": row["disable_consequence"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def feature_flag_audit_from_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "feature_key": row["feature_key"],
        "previous_enabled": None if row["previous_enabled"] is None else bool(row["previous_enabled"]),
        "new_enabled": bool(row["new_enabled"]),
        "changed_by": row["changed_by"],
        "changed_at": row["changed_at"],
        "reason": row["reason"],
    }


def load_seed_users() -> dict[str, dict[str, str]]:
    raw_users = os.getenv("ARGOS_USERS_JSON")
    if not raw_users:
        if is_production_environment():
            raise RuntimeError("ARGOS_USERS_JSON is required when ARGOS_ENV=production")
        return DEVELOPMENT_SEED_USERS
    try:
        users = json.loads(raw_users)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ARGOS_USERS_JSON must be valid JSON") from exc
    validate_seed_users(users)
    return users


def is_production_environment() -> bool:
    value = os.getenv("ARGOS_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
    return value in {"prod", "production"}


def validate_seed_users(users: Any) -> None:
    if not isinstance(users, dict):
        raise RuntimeError("ARGOS_USERS_JSON must be an object keyed by username")
    if is_production_environment() and not users:
        raise RuntimeError("ARGOS_USERS_JSON must define at least one user when ARGOS_ENV=production")
    for username, user in users.items():
        if not isinstance(user, dict):
            raise RuntimeError(f"ARGOS_USERS_JSON user {username!r} must be an object")
        password = str(user.get("password", ""))
        if is_production_environment() and (
            INSECURE_SEED_PASSWORDS_BY_USER.get(str(username)) == password
            or password in INSECURE_SEED_PASSWORD_VALUES
        ):
            raise RuntimeError(f"Refusing insecure default password for seed user {username!r} in production")


def hash_password(password: str) -> str:
    iterations = 210_000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("ascii"),
            int(iterations_raw),
        ).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def seed_default_users(connection: sqlite3.Connection) -> None:
    now = _utc_now()
    users = load_seed_users()
    for username, user in users.items():
        if not username:
            continue
        role = user.get("role", "viewer")
        if role not in USER_ROLES:
            role = "viewer"
        existing = connection.execute("SELECT username FROM app_users WHERE username = ?", (username,)).fetchone()
        if existing:
            continue
        connection.execute(
            """
            INSERT INTO app_users (
                username, password_hash, role, name, active, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                username,
                hash_password(str(user.get("password", ""))),
                role,
                user.get("name", username),
                now,
                now,
                "seed",
            ),
        )


def app_user_from_row(row: sqlite3.Row, include_hash: bool = False) -> dict[str, Any]:
    item = {
        "username": row["username"],
        "role": row["role"],
        "name": row["name"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"],
    }
    if include_hash:
        item["password_hash"] = row["password_hash"]
    return item


def get_app_user(username: str, include_hash: bool = False) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM app_users WHERE username = ?", (username,)).fetchone()
    return app_user_from_row(row, include_hash) if row else None


def list_app_users() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM app_users ORDER BY active DESC, role ASC, username ASC"
        ).fetchall()
    return [app_user_from_row(row) for row in rows]


def create_app_user(payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    username = str(payload["username"]).strip()
    role = payload.get("role", "viewer")
    if role not in USER_ROLES:
        role = "viewer"
    now = _utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO app_users (
                username, password_hash, role, name, active, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                hash_password(str(payload["password"])),
                role,
                payload.get("name") or username,
                1 if payload.get("active", True) else 0,
                now,
                now,
                actor,
            ),
        )
    item = get_app_user(username)
    if item is None:
        raise RuntimeError("failed to create user")
    return item


def update_app_user(username: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    if not changes:
        return get_app_user(username)
    fields = []
    params: list[Any] = []
    if "name" in changes and changes["name"] is not None:
        fields.append("name = ?")
        params.append(changes["name"])
    if "role" in changes and changes["role"] is not None:
        role = changes["role"] if changes["role"] in USER_ROLES else "viewer"
        fields.append("role = ?")
        params.append(role)
    if "active" in changes and changes["active"] is not None:
        fields.append("active = ?")
        params.append(1 if changes["active"] else 0)
    if "password" in changes and changes["password"]:
        fields.append("password_hash = ?")
        params.append(hash_password(str(changes["password"])))
    if not fields:
        return get_app_user(username)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(username)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE app_users SET {', '.join(fields)} WHERE username = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_app_user(username)


def delete_app_user(username: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM app_users WHERE username = ?", (username,))
        return cursor.rowcount > 0


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_app_session(token: str, user: dict[str, Any], ttl_seconds: int) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    expires_dt = now_dt + timedelta(seconds=max(60, int(ttl_seconds)))
    now = now_dt.isoformat()
    expires_at = expires_dt.isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO app_sessions (
                token_hash, username, role, name, created_at, expires_at, last_seen_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '')
            """,
            (
                session_token_hash(token),
                user.get("username", ""),
                user.get("role", "viewer"),
                user.get("name", user.get("username", "")),
                now,
                expires_at,
                now,
            ),
        )
    return {
        "token": token,
        "username": user.get("username", ""),
        "role": user.get("role", "viewer"),
        "name": user.get("name", user.get("username", "")),
        "created_at": now,
        "expires_at": expires_at,
    }


def get_app_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    token_hash = session_token_hash(token)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT s.*, u.active AS user_active, u.role AS current_role, u.name AS current_name
            FROM app_sessions s
            JOIN app_users u ON u.username = s.username
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if row["revoked_at"] or row["expires_at"] <= now or not bool(row["user_active"]):
            connection.execute(
                "UPDATE app_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at = ''",
                (now, token_hash),
            )
            return None
        connection.execute(
            "UPDATE app_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
    return {
        "token": token,
        "username": row["username"],
        "role": row["current_role"] or row["role"],
        "name": row["current_name"] or row["name"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def revoke_app_session(token: str | None) -> bool:
    if not token:
        return False
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE app_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at = ''",
            (now, session_token_hash(token)),
        )
        return cursor.rowcount > 0


def revoke_app_user_sessions(username: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE app_sessions SET revoked_at = ? WHERE username = ? AND revoked_at = ''",
            (now, username),
        )
        return cursor.rowcount


def purge_expired_app_sessions() -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM app_sessions WHERE expires_at <= ? OR revoked_at != ''",
            (now,),
        )
        return cursor.rowcount


def seed_default_cameras(connection) -> None:
    from config import CAMERAS as DEFAULT_CAMERAS

    existing = connection.execute("SELECT id FROM cameras LIMIT 1").fetchone()
    if existing:
        return
    now = _utc_now()
    for camera in DEFAULT_CAMERAS:
        connection.execute(
            """
            INSERT INTO cameras (
                id, name, url, source_url, original_stream_url, lat, lon,
                azimuth_deg, elevation_deg, height_m, focal_length_px, fov_h_deg,
                frame_width, frame_height, capture_interval_s, active, classes_json,
                confidence_min, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                camera["id"],
                camera["name"],
                camera["url"],
                camera["source_url"],
                camera.get("original_stream_url", camera["url"]),
                camera["lat"],
                camera["lon"],
                camera["azimuth_deg"],
                camera["elevation_deg"],
                camera["height_m"],
                camera["focal_length_px"],
                camera["fov_h_deg"],
                camera["frame_width"],
                camera["frame_height"],
                camera["capture_interval_s"],
                1 if camera.get("active", True) else 0,
                json.dumps(camera.get("classes") or [0, 1, 2, 3, 5, 7]),
                camera["confidence_min"],
                now,
                now,
                "seed",
            ),
        )


def camera_from_row(row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "source_url": row["source_url"],
        "original_stream_url": row["original_stream_url"],
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "azimuth_deg": float(row["azimuth_deg"]),
        "elevation_deg": float(row["elevation_deg"]),
        "height_m": float(row["height_m"]),
        "focal_length_px": float(row["focal_length_px"]),
        "fov_h_deg": float(row["fov_h_deg"]),
        "frame_width": int(row["frame_width"]),
        "frame_height": int(row["frame_height"]),
        "capture_interval_s": int(row["capture_interval_s"]),
        "active": bool(row["active"]),
        "classes": load_json(row["classes_json"], [0, 1, 2, 3, 5, 7]),
        "confidence_min": float(row["confidence_min"]),
        "detection_profile": row["detection_profile"] if "detection_profile" in keys else "balanced",
        "yolo_model": row["yolo_model"] if "yolo_model" in keys else "",
        "yolo_imgsz": int(row["yolo_imgsz"]) if "yolo_imgsz" in keys and row["yolo_imgsz"] is not None else None,
        "person_confidence_min": float(row["person_confidence_min"]) if "person_confidence_min" in keys and row["person_confidence_min"] is not None else None,
        "yolo_iou": float(row["yolo_iou"]) if "yolo_iou" in keys and row["yolo_iou"] is not None else None,
        "frame_access": row["frame_access"] if "frame_access" in keys else "all",
        "frame_access_users": load_json(row["frame_access_users_json"], []) if "frame_access_users_json" in keys else [],
        "priority_level": int(row["priority_level"]) if "priority_level" in keys and row["priority_level"] is not None else 3,
        "event_priority_boost": float(row["event_priority_boost"]) if "event_priority_boost" in keys and row["event_priority_boost"] is not None else 25.0,
        "event_boost_ttl_s": int(row["event_boost_ttl_s"]) if "event_boost_ttl_s" in keys and row["event_boost_ttl_s"] is not None else 45,
        "camera_gateway": load_json(row["camera_gateway_json"], {}) if "camera_gateway_json" in keys else {},
        "camera_secret": row["camera_secret"] if "camera_secret" in keys else "",
    }


def list_persisted_cameras() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM cameras ORDER BY id ASC").fetchall()
    return [camera_from_row(row) for row in rows]


def get_persisted_camera(camera_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return camera_from_row(row) if row else None


def create_persisted_camera(payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO cameras (
                id, name, url, source_url, original_stream_url, lat, lon,
                azimuth_deg, elevation_deg, height_m, focal_length_px, fov_h_deg,
                frame_width, frame_height, capture_interval_s, active, classes_json,
                confidence_min, detection_profile, yolo_model, yolo_imgsz, person_confidence_min,
                yolo_iou, frame_access, frame_access_users_json, priority_level, event_priority_boost,
                event_boost_ttl_s, camera_gateway_json, camera_secret, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["name"],
                payload["url"],
                payload["source_url"],
                payload.get("original_stream_url") or payload["url"],
                payload["lat"],
                payload["lon"],
                payload["azimuth_deg"],
                payload["elevation_deg"],
                payload["height_m"],
                payload["focal_length_px"],
                payload["fov_h_deg"],
                payload["frame_width"],
                payload["frame_height"],
                payload["capture_interval_s"],
                1 if payload.get("active", True) else 0,
                json.dumps(payload.get("classes") or [0, 1, 2, 3, 5, 7]),
                payload["confidence_min"],
                payload.get("detection_profile") or "balanced",
                payload.get("yolo_model") or "",
                payload.get("yolo_imgsz"),
                payload.get("person_confidence_min"),
                payload.get("yolo_iou"),
                payload.get("frame_access") or "all",
                json.dumps(payload.get("frame_access_users") or []),
                payload.get("priority_level", 3),
                payload.get("event_priority_boost", 25),
                payload.get("event_boost_ttl_s", 45),
                json.dumps(payload.get("camera_gateway") or {}),
                payload.get("camera_secret") or "",
                now,
                now,
                actor,
            ),
        )
    item = get_persisted_camera(payload["id"])
    if item is None:
        raise RuntimeError("failed to create camera")
    return item


def update_persisted_camera(camera_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    if not changes:
        return get_persisted_camera(camera_id)
    allowed = {
        "name",
        "url",
        "source_url",
        "original_stream_url",
        "lat",
        "lon",
        "azimuth_deg",
        "elevation_deg",
        "height_m",
        "focal_length_px",
        "fov_h_deg",
        "frame_width",
        "frame_height",
        "capture_interval_s",
        "active",
        "classes",
        "confidence_min",
        "detection_profile",
        "yolo_model",
        "yolo_imgsz",
        "person_confidence_min",
        "yolo_iou",
        "frame_access",
        "frame_access_users",
        "priority_level",
        "event_priority_boost",
        "event_boost_ttl_s",
        "camera_gateway",
        "camera_secret",
    }
    fields = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in allowed:
            continue
        if key == "active":
            fields.append("active = ?")
            params.append(1 if value else 0)
        elif key == "classes":
            fields.append("classes_json = ?")
            params.append(json.dumps(value))
        elif key == "frame_access_users":
            fields.append("frame_access_users_json = ?")
            params.append(json.dumps(value or []))
        elif key == "camera_gateway":
            fields.append("camera_gateway_json = ?")
            params.append(json.dumps(value or {}))
        else:
            fields.append(f"{key} = ?")
            params.append(value)
    if not fields:
        return get_persisted_camera(camera_id)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(camera_id)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE cameras SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_persisted_camera(camera_id)


def delete_persisted_camera(camera_id: str) -> dict[str, Any] | None:
    camera = get_persisted_camera(camera_id)
    if camera is None:
        return None
    with get_connection() as connection:
        connection.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    return camera


def floor_plan_from_row(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "description": row["description"] or "",
        "image_path": row["image_path"] or "",
        "image_url": row["image_path"] or "",
        "image_width_px": int(row["image_width_px"] or 0),
        "image_height_px": int(row["image_height_px"] or 0),
        "scale_m_per_px": float(row["scale_m_per_px"] or 0.05),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"] or "",
    }


def floor_plan_camera_from_row(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "floor_plan_id": int(row["floor_plan_id"]),
        "linked_camera_id": row["linked_camera_id"] or "",
        "url": row["url"] or "",
        "source_url": row["source_url"] or "",
        "name": row["name"],
        "x_px": float(row["x_px"]),
        "y_px": float(row["y_px"]),
        "direction_deg": float(row["direction_deg"]),
        "fov_deg": float(row["fov_deg"]),
        "range_m": float(row["range_m"]),
        "mount_height_m": float(row["mount_height_m"] or 2.8),
        "color": row["color"] or "#9cc7ff",
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"] or "",
    }


def floor_plan_zone_from_row(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "floor_plan_id": int(row["floor_plan_id"]),
        "name": row["name"],
        "zone_type": row["zone_type"] or "area",
        "polygon": load_json(row["polygon_json"], []),
        "color": row["color"] or "#2f3a46",
        "blocks_vision": bool(row["blocks_vision"]),
        "height_m": float(row["height_m"] or 3),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"] or "",
    }


def list_floor_plans() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM floor_plans ORDER BY updated_at DESC, id DESC").fetchall()
    return [floor_plan_from_row(row) for row in rows]


def get_floor_plan(floor_plan_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM floor_plans WHERE id = ?", (floor_plan_id,)).fetchone()
    return floor_plan_from_row(row) if row else None


def create_floor_plan(payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO floor_plans (
                name, description, image_width_px, image_height_px, scale_m_per_px, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["name"],
                payload.get("description", ""),
                payload.get("image_width_px") or 1200,
                payload.get("image_height_px") or 800,
                payload.get("scale_m_per_px") or 0.05,
                now,
                now,
                actor,
            ),
        )
        floor_plan_id = int(cursor.lastrowid)
    item = get_floor_plan(floor_plan_id)
    if item is None:
        raise RuntimeError("failed to create floor plan")
    return item


def update_floor_plan(floor_plan_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    if not changes:
        return get_floor_plan(floor_plan_id)
    allowed = {"name", "description", "image_path", "image_width_px", "image_height_px", "scale_m_per_px"}
    fields = []
    params: list[Any] = []
    for key, value in changes.items():
        if key in allowed and value is not None:
            fields.append(f"{key} = ?")
            params.append(value)
    if not fields:
        return get_floor_plan(floor_plan_id)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(floor_plan_id)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE floor_plans SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_floor_plan(floor_plan_id)


def delete_floor_plan(floor_plan_id: int) -> bool:
    with get_connection() as connection:
        connection.execute("DELETE FROM floor_plan_zones WHERE floor_plan_id = ?", (floor_plan_id,))
        connection.execute("DELETE FROM floor_plan_cameras WHERE floor_plan_id = ?", (floor_plan_id,))
        cursor = connection.execute("DELETE FROM floor_plans WHERE id = ?", (floor_plan_id,))
        return cursor.rowcount > 0


def list_floor_plan_zones(floor_plan_id: int) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM floor_plan_zones WHERE floor_plan_id = ? ORDER BY id ASC",
            (floor_plan_id,),
        ).fetchall()
    return [floor_plan_zone_from_row(row) for row in rows]


def get_floor_plan_zone(zone_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM floor_plan_zones WHERE id = ?", (zone_id,)).fetchone()
    return floor_plan_zone_from_row(row) if row else None


def create_floor_plan_zone(floor_plan_id: int, payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO floor_plan_zones (
                floor_plan_id, name, zone_type, polygon_json, color, blocks_vision, height_m, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                floor_plan_id,
                payload["name"],
                payload.get("zone_type") or "area",
                json.dumps(payload["polygon"]),
                payload.get("color") or "#2f3a46",
                1 if payload.get("blocks_vision", False) else 0,
                payload.get("height_m") or 3,
                now,
                now,
                actor,
            ),
        )
        zone_id = int(cursor.lastrowid)
    item = get_floor_plan_zone(zone_id)
    if item is None:
        raise RuntimeError("failed to create floor plan zone")
    return item


def update_floor_plan_zone(zone_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    if not changes:
        return get_floor_plan_zone(zone_id)
    allowed = {"name", "zone_type", "polygon", "color", "blocks_vision", "height_m"}
    fields = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in allowed or value is None:
            continue
        if key == "polygon":
            fields.append("polygon_json = ?")
            params.append(json.dumps(value))
        elif key == "blocks_vision":
            fields.append("blocks_vision = ?")
            params.append(1 if value else 0)
        else:
            fields.append(f"{key} = ?")
            params.append(value)
    if not fields:
        return get_floor_plan_zone(zone_id)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(zone_id)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE floor_plan_zones SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_floor_plan_zone(zone_id)


def delete_floor_plan_zone(zone_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM floor_plan_zones WHERE id = ?", (zone_id,))
        return cursor.rowcount > 0


def list_floor_plan_cameras(floor_plan_id: int) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM floor_plan_cameras WHERE floor_plan_id = ? ORDER BY id ASC",
            (floor_plan_id,),
        ).fetchall()
    return [floor_plan_camera_from_row(row) for row in rows]


def get_floor_plan_camera(camera_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM floor_plan_cameras WHERE id = ?", (camera_id,)).fetchone()
    return floor_plan_camera_from_row(row) if row else None


def create_floor_plan_camera(floor_plan_id: int, payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO floor_plan_cameras (
                floor_plan_id, linked_camera_id, url, source_url, name, x_px, y_px, direction_deg,
                fov_deg, range_m, mount_height_m, color, enabled, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                floor_plan_id,
                payload.get("linked_camera_id") or "",
                payload.get("url") or "",
                payload.get("source_url") or payload.get("url") or "",
                payload["name"],
                payload.get("x_px", 0),
                payload.get("y_px", 0),
                payload.get("direction_deg", 0),
                payload.get("fov_deg", 80),
                payload.get("range_m", 12),
                payload.get("mount_height_m") or 2.8,
                payload.get("color", "#9cc7ff"),
                1 if payload.get("enabled", True) else 0,
                now,
                now,
                actor,
            ),
        )
        item_id = int(cursor.lastrowid)
    item = get_floor_plan_camera(item_id)
    if item is None:
        raise RuntimeError("failed to create floor plan camera")
    return item


def update_floor_plan_camera(camera_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    if not changes:
        return get_floor_plan_camera(camera_id)
    allowed = {
        "linked_camera_id",
        "url",
        "source_url",
        "name",
        "x_px",
        "y_px",
        "direction_deg",
        "fov_deg",
        "range_m",
        "mount_height_m",
        "color",
        "enabled",
    }
    fields = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in allowed or value is None:
            continue
        if key == "enabled":
            fields.append("enabled = ?")
            params.append(1 if value else 0)
        elif key == "linked_camera_id":
            fields.append("linked_camera_id = ?")
            params.append(value or "")
        else:
            fields.append(f"{key} = ?")
            params.append(value)
    if not fields:
        return get_floor_plan_camera(camera_id)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(camera_id)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE floor_plan_cameras SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_floor_plan_camera(camera_id)


def delete_floor_plan_camera(camera_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM floor_plan_cameras WHERE id = ?", (camera_id,))
        return cursor.rowcount > 0


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def telegram_env_bot_token() -> str:
    return os.getenv(TELEGRAM_BOT_TOKEN_ENV, "").strip()


def telegram_fernet(required: bool = False):
    key = os.getenv(TELEGRAM_SECRET_KEY_ENV, "").strip()
    if not key:
        if required:
            raise RuntimeError(f"{TELEGRAM_SECRET_KEY_ENV} is required to store Telegram bot tokens")
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:  # pragma: no cover - dependency issue
        raise RuntimeError("cryptography is required to protect Telegram bot tokens") from exc
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError(f"{TELEGRAM_SECRET_KEY_ENV} must be a valid Fernet key") from exc


def protect_telegram_bot_token(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    fernet = telegram_fernet(required=True)
    return TELEGRAM_TOKEN_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")


def reveal_telegram_bot_token(secret: str) -> str:
    value = str(secret or "").strip()
    if not value:
        return ""
    if not value.startswith(TELEGRAM_TOKEN_PREFIX):
        return value
    fernet = telegram_fernet(required=False)
    if fernet is None:
        return ""
    try:
        return fernet.decrypt(value.removeprefix(TELEGRAM_TOKEN_PREFIX).encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def telegram_config_from_row(row: sqlite3.Row | None, mask_token: bool = True) -> dict[str, Any]:
    env_token = telegram_env_bot_token()
    if row is None:
        return {
            "enabled": False,
            "bot_token": env_token if not mask_token else None,
            "bot_token_masked": mask_secret(env_token),
            "chat_id": "",
            "parse_mode": "HTML",
            "updated_at": "",
            "updated_by": "",
            "configured": False,
            "bot_token_source": "env" if env_token else "",
        }
    stored_secret = row["bot_token"] or ""
    stored_token = reveal_telegram_bot_token(stored_secret)
    bot_token = env_token or stored_token
    token_source = "env" if env_token else "encrypted" if stored_secret.startswith(TELEGRAM_TOKEN_PREFIX) else "legacy" if stored_secret else ""
    item = {
        "enabled": bool(row["enabled"]),
        "chat_id": row["chat_id"] or "",
        "parse_mode": row["parse_mode"] or "HTML",
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
        "configured": bool(bot_token and (row["chat_id"] or "")),
        "bot_token_masked": mask_secret(bot_token) if bot_token else ("encrypted" if stored_secret.startswith(TELEGRAM_TOKEN_PREFIX) else ""),
        "bot_token_source": token_source,
    }
    if not mask_token:
        item["bot_token"] = bot_token
    return item


def get_telegram_config(mask_token: bool = True) -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM telegram_integration WHERE id = 1").fetchone()
    return telegram_config_from_row(row, mask_token)


def save_telegram_config(payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    with get_connection() as connection:
        current_row = connection.execute("SELECT * FROM telegram_integration WHERE id = 1").fetchone()
    current = telegram_config_from_row(current_row, mask_token=False)
    now = _utc_now()
    bot_token = payload.get("bot_token")
    if bot_token:
        bot_token = protect_telegram_bot_token(bot_token)
    else:
        bot_token = (current_row["bot_token"] if current_row is not None else "") or ""
    parse_mode = payload.get("parse_mode") or current.get("parse_mode") or "HTML"
    if parse_mode not in {"HTML", "Markdown", "MarkdownV2"}:
        parse_mode = "HTML"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO telegram_integration (
                id, enabled, bot_token, chat_id, parse_mode, updated_at, updated_by
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                bot_token = excluded.bot_token,
                chat_id = excluded.chat_id,
                parse_mode = excluded.parse_mode,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (
                1 if payload.get("enabled", False) else 0,
                bot_token,
                payload.get("chat_id", current.get("chat_id", "")) or "",
                parse_mode,
                now,
                actor,
            ),
        )
    return get_telegram_config(mask_token=True)


def create_geofence(payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO geofences (
                name, description, color, priority_level, active, points_json, created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["name"],
                payload.get("description", ""),
                payload.get("color", "#9cc7ff"),
                int(payload.get("priority_level") or 3),
                1 if payload.get("active", True) else 0,
                json.dumps(payload["points"]),
                now,
                now,
                actor,
            ),
        )
        geofence_id = int(cursor.lastrowid)
    item = get_geofence(geofence_id)
    if item is None:
        raise RuntimeError("failed to create geofence")
    return item


def update_geofence(geofence_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    if not changes:
        return get_geofence(geofence_id)
    allowed = {"name", "description", "color", "priority_level", "active", "points"}
    fields = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in allowed:
            continue
        if key == "points":
            fields.append("points_json = ?")
            params.append(json.dumps(value))
        elif key == "active":
            fields.append("active = ?")
            params.append(1 if value else 0)
        else:
            fields.append(f"{key} = ?")
            params.append(value)
    if not fields:
        return get_geofence(geofence_id)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(geofence_id)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE geofences SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_geofence(geofence_id)


def delete_geofence(geofence_id: int) -> bool:
    with get_connection() as connection:
        connection.execute("DELETE FROM geofence_rules WHERE geofence_id = ?", (geofence_id,))
        connection.execute("DELETE FROM geofence_alerts WHERE geofence_id = ?", (geofence_id,))
        cursor = connection.execute("DELETE FROM geofences WHERE id = ?", (geofence_id,))
        return cursor.rowcount > 0


def get_geofence(geofence_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM geofences WHERE id = ?", (geofence_id,)).fetchone()
    return geofence_from_row(row) if row else None


def list_geofences(active_only: bool = False) -> list[dict[str, Any]]:
    clauses = ["active = 1"] if active_only else []
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM geofences {where_sql} ORDER BY id DESC"
        ).fetchall()
    return [geofence_from_row(row) for row in rows]


def geofence_from_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "color": row["color"] or "#9cc7ff",
        "priority_level": int(row["priority_level"]) if "priority_level" in keys and row["priority_level"] is not None else 3,
        "active": bool(row["active"]),
        "points": load_json(row["points_json"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"],
    }


def create_geofence_rule(payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    now = _utc_now()
    object_types = payload.get("object_types")
    if not object_types:
        object_types = [payload.get("object_type", "pessoa")]
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO geofence_rules (
                geofence_id, name, object_type, start_time, end_time, active,
                object_types, max_stationary_seconds, max_presence_seconds,
                min_frames_required, min_confidence_required,
                active_time_start, active_time_end, active_days, severity,
                cooldown_seconds, notification_channels, message_template,
                created_at, updated_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["geofence_id"],
                payload["name"],
                payload.get("object_type", "pessoa"),
                payload.get("start_time", "00:00"),
                payload.get("end_time", "23:59"),
                1 if payload.get("active", True) else 0,
                json.dumps(object_types),
                int(payload.get("max_stationary_seconds") or 0),
                int(payload.get("max_presence_seconds") or 0),
                int(payload.get("min_frames_required") or 3),
                float(payload.get("min_confidence_required") or 0),
                payload.get("active_time_start") or payload.get("start_time", "00:00"),
                payload.get("active_time_end") or payload.get("end_time", "23:59"),
                json.dumps(payload.get("active_days") or []),
                payload.get("severity", "medium"),
                int(payload.get("cooldown_seconds") or 300),
                json.dumps(payload.get("notification_channels") or []),
                payload.get("message_template", ""),
                now,
                now,
                actor,
            ),
        )
        rule_id = int(cursor.lastrowid)
    item = get_geofence_rule(rule_id)
    if item is None:
        raise RuntimeError("failed to create geofence rule")
    return item


def update_geofence_rule(rule_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    if not changes:
        return get_geofence_rule(rule_id)
    allowed = {
        "geofence_id",
        "name",
        "object_type",
        "start_time",
        "end_time",
        "active",
        "object_types",
        "max_stationary_seconds",
        "max_presence_seconds",
        "min_frames_required",
        "min_confidence_required",
        "active_time_start",
        "active_time_end",
        "active_days",
        "severity",
        "cooldown_seconds",
        "notification_channels",
        "message_template",
    }
    fields = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        if key == "active":
            params.append(1 if value else 0)
        elif key in {"object_types", "active_days", "notification_channels"}:
            params.append(json.dumps(value or []))
        else:
            params.append(value)
    if not fields:
        return get_geofence_rule(rule_id)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(rule_id)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE geofence_rules SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_geofence_rule(rule_id)


def delete_geofence_rule(rule_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM geofence_rules WHERE id = ?", (rule_id,))
        return cursor.rowcount > 0


def get_geofence_rule(rule_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT r.*, g.name AS geofence_name
            FROM geofence_rules r
            LEFT JOIN geofences g ON g.id = r.geofence_id
            WHERE r.id = ?
            """,
            (rule_id,),
        ).fetchone()
    return geofence_rule_from_row(row) if row else None


def list_geofence_rules(active_only: bool = False) -> list[dict[str, Any]]:
    clauses = ["r.active = 1", "g.active = 1"] if active_only else []
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT r.*, g.name AS geofence_name
            FROM geofence_rules r
            LEFT JOIN geofences g ON g.id = r.geofence_id
            {where_sql}
            ORDER BY r.id DESC
            """
        ).fetchall()
    return [geofence_rule_from_row(row) for row in rows]


def geofence_rule_from_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    object_types = load_json(row["object_types"], []) if "object_types" in keys else []
    if not object_types:
        object_types = [row["object_type"]]
    return {
        "id": row["id"],
        "geofence_id": row["geofence_id"],
        "geofence_name": row["geofence_name"],
        "name": row["name"],
        "object_type": row["object_type"],
        "object_types": object_types,
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "max_stationary_seconds": int(row["max_stationary_seconds"]) if "max_stationary_seconds" in keys else 0,
        "max_presence_seconds": int(row["max_presence_seconds"]) if "max_presence_seconds" in keys else 0,
        "min_frames_required": int(row["min_frames_required"]) if "min_frames_required" in keys else 3,
        "min_confidence_required": float(row["min_confidence_required"]) if "min_confidence_required" in keys else 0.0,
        "active_time_start": row["active_time_start"] if "active_time_start" in keys else row["start_time"],
        "active_time_end": row["active_time_end"] if "active_time_end" in keys else row["end_time"],
        "active_days": load_json(row["active_days"], []) if "active_days" in keys else [],
        "severity": row["severity"] if "severity" in keys else "medium",
        "cooldown_seconds": int(row["cooldown_seconds"]) if "cooldown_seconds" in keys else 300,
        "notification_channels": load_json(row["notification_channels"], []) if "notification_channels" in keys else [],
        "message_template": row["message_template"] if "message_template" in keys else "",
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"],
    }


def save_geofence_alert(alert: dict[str, Any]) -> int:
    detection = alert.get("detection") or {}
    geo = detection.get("geo") or {}
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO geofence_alerts (
                geofence_id, rule_id, camera_id, object_type, track_id,
                timestamp, lat, lon, status, resolved_at, resolved_by, resolution,
                incident_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert["geofence_id"],
                alert["rule_id"],
                detection.get("camera_id", ""),
                detection.get("object_type", ""),
                detection.get("track_id") or "",
                detection.get("timestamp", alert.get("timestamp", "")),
                float(geo.get("lat") or 0),
                float(geo.get("lon") or 0),
                alert.get("status") or "open",
                alert.get("resolved_at") or "",
                alert.get("resolved_by") or "",
                alert.get("resolution") or "",
                alert.get("incident_id"),
                json.dumps(alert),
            ),
        )
        return int(cursor.lastrowid)


def geofence_alert_from_row(row) -> dict[str, Any]:
    keys = set(row.keys()) if hasattr(row, "keys") else set(row)
    item = load_json(row["payload_json"], {})
    item["id"] = int(row["id"])
    item["status"] = row["status"] if "status" in keys else item.get("status", "open")
    item["resolved_at"] = row["resolved_at"] if "resolved_at" in keys else item.get("resolved_at", "")
    item["resolved_by"] = row["resolved_by"] if "resolved_by" in keys else item.get("resolved_by", "")
    item["resolution"] = row["resolution"] if "resolution" in keys else item.get("resolution", "")
    incident_id = row["incident_id"] if "incident_id" in keys else item.get("incident_id")
    item["incident_id"] = int(incident_id) if incident_id not in (None, "") else None
    item.setdefault("camera_id", row["camera_id"] if "camera_id" in keys else "")
    item.setdefault("object_type", row["object_type"] if "object_type" in keys else "")
    item.setdefault("track_id", row["track_id"] if "track_id" in keys else "")
    item.setdefault("timestamp", row["timestamp"] if "timestamp" in keys else "")
    detection = item.setdefault("detection", {})
    detection.setdefault("camera_id", item.get("camera_id") or "")
    detection.setdefault("object_type", item.get("object_type") or "")
    detection.setdefault("track_id", item.get("track_id") or "")
    detection.setdefault("timestamp", item.get("timestamp") or "")
    detection.setdefault("geo", {"lat": row["lat"], "lon": row["lon"]} if {"lat", "lon"}.issubset(keys) else {})
    return item


def get_geofence_alert(alert_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM geofence_alerts WHERE id = ?", (alert_id,)).fetchone()
    return geofence_alert_from_row(row) if row else None


def list_geofence_alerts(limit: int = 100, include_resolved: bool = False) -> list[dict[str, Any]]:
    where_sql = "" if include_resolved else "WHERE COALESCE(status, 'open') = 'open'"
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM geofence_alerts
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [geofence_alert_from_row(row) for row in rows]


def update_geofence_alert(alert_id: int, changes: dict[str, Any], actor: str = "") -> dict[str, Any] | None:
    current = get_geofence_alert(alert_id)
    if current is None:
        return None
    status = str(changes.get("status") or current.get("status") or "open")
    if status not in {"open", "resolved", "dismissed"}:
        raise ValueError("invalid geofence alert status")
    resolved_at = str(changes.get("resolved_at") if "resolved_at" in changes else current.get("resolved_at") or "")
    resolved_by = str(changes.get("resolved_by") if "resolved_by" in changes else current.get("resolved_by") or "")
    resolution = str(changes.get("resolution") if "resolution" in changes else current.get("resolution") or "")
    incident_id = changes.get("incident_id") if "incident_id" in changes else current.get("incident_id")
    if status in {"resolved", "dismissed"}:
        if not resolved_at:
            resolved_at = _utc_now()
        if not resolved_by:
            resolved_by = actor
    else:
        resolved_at = ""
        resolved_by = ""
        resolution = ""
    payload = deepcopy(current)
    payload.update(
        {
            "status": status,
            "resolved_at": resolved_at,
            "resolved_by": resolved_by,
            "resolution": resolution,
            "incident_id": int(incident_id) if incident_id not in (None, "") else None,
        }
    )
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE geofence_alerts
            SET status = ?, resolved_at = ?, resolved_by = ?, resolution = ?,
                incident_id = ?, payload_json = ?
            WHERE id = ?
            """,
            (
                status,
                resolved_at,
                resolved_by,
                resolution,
                int(incident_id) if incident_id not in (None, "") else None,
                json.dumps(payload),
                alert_id,
            ),
        )
    return get_geofence_alert(alert_id)


def upsert_object_track(track: dict[str, Any]) -> dict[str, Any]:
    now = _utc_now()
    metadata = track.get("metadata") or {
        "risk_level": track.get("risk_level", "none"),
        "evidence_status": track.get("evidence_status", ""),
        "decision_reason": track.get("decision_reason", ""),
        "evidence": track.get("evidence", {}),
    }
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO object_tracks (
                track_id, camera_id, object_type, first_seen_at, first_confirmed_at,
                last_seen_at, current_geofence_id, entered_geofence_at,
                stationary_candidate_started_at, stationary_started_at, status,
                frames_seen_count, consecutive_frames_seen_count, frames_missing_count,
                last_position_x, last_position_y, last_lat, last_lng, metadata_json,
                created_at, updated_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                camera_id = excluded.camera_id,
                object_type = excluded.object_type,
                first_confirmed_at = excluded.first_confirmed_at,
                last_seen_at = excluded.last_seen_at,
                current_geofence_id = excluded.current_geofence_id,
                entered_geofence_at = excluded.entered_geofence_at,
                stationary_candidate_started_at = excluded.stationary_candidate_started_at,
                stationary_started_at = excluded.stationary_started_at,
                status = excluded.status,
                frames_seen_count = excluded.frames_seen_count,
                consecutive_frames_seen_count = excluded.consecutive_frames_seen_count,
                frames_missing_count = excluded.frames_missing_count,
                last_position_x = excluded.last_position_x,
                last_position_y = excluded.last_position_y,
                last_lat = excluded.last_lat,
                last_lng = excluded.last_lng,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at,
                closed_at = excluded.closed_at
            """,
            (
                track["track_id"],
                track.get("camera_id", ""),
                track.get("object_type", ""),
                track.get("first_seen_at", ""),
                track.get("first_confirmed_at") or "",
                track.get("last_seen_at", ""),
                track.get("geofence_id"),
                track.get("entered_geofence_at") or "",
                track.get("stationary_candidate_started_at") or "",
                track.get("stationary_started_at") or "",
                track.get("status", "observed"),
                int(track.get("frames_seen_count") or 0),
                int(track.get("consecutive_frames_seen_count") or 0),
                int(track.get("frames_missing_count") or 0),
                (track.get("current_position_image") or [None, None])[0],
                (track.get("current_position_image") or [None, None])[1],
                track.get("lat"),
                track.get("lng"),
                json.dumps(metadata),
                track.get("created_at") or now,
                now,
                track.get("closed_at") or "",
            ),
        )
    item = get_object_track(track["track_id"])
    if item is None:
        raise RuntimeError("failed to upsert object track")
    return item


def save_track_history(track: dict[str, Any]) -> None:
    history = track.get("history") or []
    if not history:
        return
    latest = history[-1]
    bbox = latest.get("bbox") or {}
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO track_history (
                track_id, camera_id, frame_id, timestamp, bbox_x, bbox_y, bbox_w, bbox_h,
                centroid_x, centroid_y, lat, lng, geofence_id, detection_confidence,
                snapshot_path, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track["track_id"],
                track.get("camera_id", ""),
                latest.get("frame_id"),
                latest.get("timestamp", track.get("last_seen_at", "")),
                float(bbox.get("x") or 0),
                float(bbox.get("y") or 0),
                float(bbox.get("w") or 0),
                float(bbox.get("h") or 0),
                float(latest.get("centroid_image_x") or 0),
                float(latest.get("centroid_image_y") or 0),
                latest.get("lat"),
                latest.get("lng"),
                latest.get("geofence_id"),
                float(latest.get("detection_confidence") or 0),
                latest.get("snapshot_path") or "",
                json.dumps(latest.get("metadata") or {}),
            ),
        )


def get_object_track(track_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM object_tracks WHERE track_id = ?", (track_id,)).fetchone()
    return object_track_from_row(row) if row else None


def list_object_tracks(active_only: bool = True, limit: int = 250) -> list[dict[str, Any]]:
    clauses = ["status != 'closed'"] if active_only else []
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM object_tracks
            {where_sql}
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [object_track_from_row(row) for row in rows]


def object_track_from_row(row: sqlite3.Row) -> dict[str, Any]:
    metadata = load_json(row["metadata_json"], {})
    return {
        "id": row["id"],
        "track_id": row["track_id"],
        "camera_id": row["camera_id"],
        "object_type": row["object_type"],
        "first_seen_at": row["first_seen_at"],
        "first_confirmed_at": row["first_confirmed_at"] or None,
        "last_seen_at": row["last_seen_at"],
        "geofence_id": row["current_geofence_id"],
        "entered_geofence_at": row["entered_geofence_at"] or None,
        "stationary_candidate_started_at": row["stationary_candidate_started_at"] or None,
        "stationary_started_at": row["stationary_started_at"] or None,
        "status": row["status"],
        "frames_seen_count": row["frames_seen_count"],
        "consecutive_frames_seen_count": row["consecutive_frames_seen_count"],
        "frames_missing_count": row["frames_missing_count"],
        "lat": row["last_lat"],
        "lng": row["last_lng"],
        "risk_level": metadata.get("risk_level", "none"),
        "evidence_status": metadata.get("evidence_status", ""),
        "decision_reason": metadata.get("decision_reason", ""),
        "metadata": metadata,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "closed_at": row["closed_at"] or None,
    }


def save_temporal_event(event: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO events (
                event_type, rule_id, track_id, camera_id, geofence_id, object_type,
                severity, message, status, seen_duration_seconds, geofence_duration_seconds,
                stationary_duration_seconds, created_at, first_evidence_at, last_evidence_at,
                snapshot_path, frame_reference, acknowledged, acknowledged_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("event_type", "temporal_geofence_alert"),
                event.get("rule_id"),
                event.get("track_id", ""),
                event.get("camera_id", ""),
                event.get("geofence_id"),
                event.get("object_type", ""),
                event.get("severity", "medium"),
                event.get("message", ""),
                event.get("status", "open"),
                int(event.get("seen_duration_seconds") or 0),
                int(event.get("geofence_duration_seconds") or 0),
                int(event.get("stationary_duration_seconds") or 0),
                event.get("created_at") or _utc_now(),
                event.get("first_evidence_at") or "",
                event.get("last_evidence_at") or "",
                event.get("snapshot_path") or "",
                event.get("frame_reference"),
                1 if event.get("acknowledged") else 0,
                event.get("acknowledged_at") or "",
                json.dumps(event.get("metadata") or {}),
            ),
        )
        event_id = int(cursor.lastrowid)
    item = get_temporal_event(event_id)
    if item is None:
        raise RuntimeError("failed to save temporal event")
    return item


def get_temporal_event(event_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return temporal_event_from_row(row) if row else None


def list_temporal_events(limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM events
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [temporal_event_from_row(row) for row in rows]


def acknowledge_temporal_event(event_id: int, actor: str = "") -> dict[str, Any] | None:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE events
            SET acknowledged = 1, acknowledged_at = ?, status = 'acknowledged'
            WHERE id = ?
            """,
            (now, event_id),
        )
        if cursor.rowcount == 0:
            return None
    item = get_temporal_event(event_id)
    if item is not None:
        item["acknowledged_by"] = actor
    return item


def temporal_event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "event_type": row["event_type"],
        "rule_id": row["rule_id"],
        "track_id": row["track_id"],
        "camera_id": row["camera_id"],
        "geofence_id": row["geofence_id"],
        "object_type": row["object_type"],
        "severity": row["severity"],
        "message": row["message"],
        "status": row["status"],
        "seen_duration_seconds": row["seen_duration_seconds"],
        "geofence_duration_seconds": row["geofence_duration_seconds"],
        "stationary_duration_seconds": row["stationary_duration_seconds"],
        "created_at": row["created_at"],
        "first_evidence_at": row["first_evidence_at"],
        "last_evidence_at": row["last_evidence_at"],
        "snapshot_path": row["snapshot_path"],
        "frame_reference": row["frame_reference"],
        "acknowledged": bool(row["acknowledged"]),
        "acknowledged_at": row["acknowledged_at"],
        "metadata": load_json(row["metadata_json"], {}),
    }


def attention_state_from_row(row) -> dict[str, Any]:
    keys = set(row.keys()) if hasattr(row, "keys") else set(row)
    return {
        "item_key": row["item_key"],
        "status": row["status"],
        "assigned_to": row["assigned_to"],
        "assigned_at": row["assigned_at"],
        "resolved_at": row["resolved_at"],
        "resolution": row["resolution"],
        "quick_action": row["quick_action"] if "quick_action" in keys else "",
        "confirmed_at": row["confirmed_at"] if "confirmed_at" in keys else "",
        "comment": row["comment"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def list_attention_item_states(item_keys: list[str] | None = None) -> dict[str, dict[str, Any]]:
    params: list[Any] = []
    where_sql = ""
    if item_keys:
        placeholders = ", ".join("?" for _ in item_keys)
        where_sql = f"WHERE item_key IN ({placeholders})"
        params.extend(item_keys)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM attention_item_states
            {where_sql}
            """,
            params,
        ).fetchall()
    return {row["item_key"]: attention_state_from_row(row) for row in rows}


def upsert_attention_item_state(item_key: str, changes: dict[str, Any], actor: str = "") -> dict[str, Any]:
    current = list_attention_item_states([item_key]).get(item_key) or {}
    now = _utc_now()
    status = str(changes.get("status") or current.get("status") or "open")
    assigned_to = str(changes.get("assigned_to") if "assigned_to" in changes else current.get("assigned_to") or "")
    assigned_at = str(changes.get("assigned_at") if "assigned_at" in changes else current.get("assigned_at") or "")
    resolved_at = str(changes.get("resolved_at") if "resolved_at" in changes else current.get("resolved_at") or "")
    resolution = str(changes.get("resolution") if "resolution" in changes else current.get("resolution") or "")
    quick_action = str(changes.get("quick_action") if "quick_action" in changes else current.get("quick_action") or "")
    confirmed_at = str(changes.get("confirmed_at") if "confirmed_at" in changes else current.get("confirmed_at") or "")
    comment = str(changes.get("comment") if "comment" in changes else current.get("comment") or "")
    if status == "assigned" and not assigned_to:
        assigned_to = actor
    if status == "assigned" and not assigned_at:
        assigned_at = now
    if status in {"resolved", "dismissed"} and not resolved_at:
        resolved_at = now
    if quick_action and not confirmed_at:
        confirmed_at = now
    if status == "open":
        assigned_to = ""
        assigned_at = ""
        resolved_at = ""
        resolution = ""
        quick_action = ""
        confirmed_at = ""
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO attention_item_states (
                item_key, status, assigned_to, assigned_at, resolved_at,
                resolution, quick_action, confirmed_at, comment, updated_at, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                status = excluded.status,
                assigned_to = excluded.assigned_to,
                assigned_at = excluded.assigned_at,
                resolved_at = excluded.resolved_at,
                resolution = excluded.resolution,
                quick_action = excluded.quick_action,
                confirmed_at = excluded.confirmed_at,
                comment = excluded.comment,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (item_key, status, assigned_to, assigned_at, resolved_at, resolution, quick_action, confirmed_at, comment, now, actor),
        )
    return list_attention_item_states([item_key]).get(item_key) or {}


def incident_from_row(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "summary": row["summary"],
        "status": row["status"],
        "severity": row["severity"],
        "source_kind": row["source_kind"],
        "source_key": row["source_key"],
        "attention_item_key": row["attention_item_key"],
        "camera_id": row["camera_id"],
        "event_id": int(row["event_id"]) if row["event_id"] is not None else None,
        "frame_id": int(row["frame_id"]) if row["frame_id"] is not None else None,
        "track_id": row["track_id"],
        "object_type": row["object_type"],
        "assigned_to": row["assigned_to"],
        "opened_at": row["opened_at"],
        "opened_by": row["opened_by"],
        "closed_at": row["closed_at"],
        "closed_by": row["closed_by"],
        "resolution": row["resolution"],
        "metadata": load_json(row["metadata_json"], {}),
    }


def timeline_from_row(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "incident_id": int(row["incident_id"]),
        "timestamp": row["timestamp"],
        "actor": row["actor"],
        "action": row["action"],
        "message": row["message"],
        "payload": load_json(row["payload_json"], {}),
    }


def create_incident(payload: dict[str, Any], actor: str = "") -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO incidents (
                title, summary, status, severity, source_kind, source_key, attention_item_key,
                camera_id, event_id, frame_id, track_id, object_type, assigned_to,
                opened_at, opened_by, closed_at, closed_by, resolution, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("title") or "Ocorrencia",
                payload.get("summary") or "",
                payload.get("status") or "open",
                payload.get("severity") or "medium",
                payload.get("source_kind") or "",
                payload.get("source_key") or "",
                payload.get("attention_item_key") or "",
                payload.get("camera_id") or "",
                payload.get("event_id"),
                payload.get("frame_id"),
                payload.get("track_id") or "",
                payload.get("object_type") or "",
                payload.get("assigned_to") if "assigned_to" in payload else actor,
                payload.get("opened_at") or now,
                payload.get("opened_by") or actor,
                payload.get("closed_at") or "",
                payload.get("closed_by") or "",
                payload.get("resolution") or "",
                json.dumps(payload.get("metadata") or {}),
            ),
        )
        incident_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO incident_timeline (
                incident_id, timestamp, actor, action, message, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                now,
                actor,
                "incident.created",
                payload.get("timeline_message") or "Ocorrencia aberta.",
                json.dumps(payload.get("timeline_payload") or {}),
            ),
        )
    return get_incident(incident_id) or {}


def get_incident(incident_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return incident_from_row(row) if row else None


def get_incident_by_attention_key(attention_item_key: str, active_only: bool = True) -> dict[str, Any] | None:
    clauses = ["attention_item_key = ?"]
    params: list[Any] = [attention_item_key]
    if active_only:
        clauses.append("status != 'closed'")
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT *
            FROM incidents
            WHERE {" AND ".join(clauses)}
            ORDER BY id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return incident_from_row(row) if row else None


def list_incidents(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM incidents
            {where_sql}
            ORDER BY opened_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [incident_from_row(row) for row in rows]


def add_incident_timeline(incident_id: int, action: str, message: str = "", actor: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO incident_timeline (
                incident_id, timestamp, actor, action, message, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (incident_id, now, actor, action, message, json.dumps(payload or {})),
        )
        timeline_id = int(cursor.lastrowid)
        row = connection.execute("SELECT * FROM incident_timeline WHERE id = ?", (timeline_id,)).fetchone()
    return timeline_from_row(row)


def list_incident_timeline(incident_id: int) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM incident_timeline
            WHERE incident_id = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (incident_id,),
        ).fetchall()
    return [timeline_from_row(row) for row in rows]


def update_incident(incident_id: int, changes: dict[str, Any], actor: str = "") -> dict[str, Any] | None:
    current = get_incident(incident_id)
    if current is None:
        return None
    fields = []
    params: list[Any] = []
    allowed = {"title", "summary", "status", "severity", "assigned_to", "resolution", "closed_at", "closed_by", "metadata"}
    for key, value in changes.items():
        if key not in allowed:
            continue
        if key == "metadata":
            fields.append("metadata_json = ?")
            params.append(json.dumps(value or {}))
        else:
            fields.append(f"{key} = ?")
            params.append(value if value is not None else "")
    if changes.get("status") == "closed" and not changes.get("closed_at"):
        fields.append("closed_at = ?")
        params.append(_utc_now())
    if changes.get("status") == "closed" and not changes.get("closed_by"):
        fields.append("closed_by = ?")
        params.append(actor)
    if not fields:
        return current
    params.append(incident_id)
    with get_connection() as connection:
        connection.execute(f"UPDATE incidents SET {', '.join(fields)} WHERE id = ?", params)
    return get_incident(incident_id)


def save_audit_log(
    actor: str,
    role: str,
    action: str,
    resource: str,
    details: dict[str, Any] | None = None,
    ip: str = "",
) -> int:
    from datetime import datetime, timezone

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO audit_logs (
                timestamp, actor, role, action, resource, ip, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                actor,
                role,
                action,
                resource,
                ip,
                json.dumps(details or {}),
            ),
        )
        return int(cursor.lastrowid)


def list_audit_logs(limit: int = 200, actor: str | None = None, action: str | None = None) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if actor:
        clauses.append("actor = ?")
        params.append(actor)
    if action:
        clauses.append("action = ?")
        params.append(action)
    params.append(limit)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM audit_logs
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [
        {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "actor": row["actor"],
            "role": row["role"],
            "action": row["action"],
            "resource": row["resource"],
            "ip": row["ip"],
            "details": load_json(row["details_json"], {}),
        }
        for row in rows
    ]


def save_chat_message(actor: str, role: str, body: str, mentions: dict[str, Any] | None = None) -> dict[str, Any]:
    timestamp = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO chat_messages (
                timestamp, actor, role, body, mentions_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                actor,
                role,
                body,
                json.dumps(mentions or {}),
            ),
        )
        message_id = int(cursor.lastrowid)
    return {
        "id": message_id,
        "timestamp": timestamp,
        "actor": actor,
        "role": role,
        "body": body,
        "mentions": mentions or {},
    }


def list_chat_messages(limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM chat_messages
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    messages = []
    for row in reversed(rows):
        messages.append(
            {
                "id": int(row["id"]),
                "timestamp": row["timestamp"],
                "actor": row["actor"],
                "role": row["role"],
                "body": row["body"],
                "mentions": load_json(row["mentions_json"], {}),
            }
        )
    return messages


def save_processed_frame(payload: dict[str, Any]) -> int | None:
    frame = payload.get("frame") or {}
    camera = payload.get("camera") or {}
    image = frame.get("image") or ""
    if not image and not payload.get("detections"):
        return None
    payload_for_json = deepcopy(payload)
    payload_for_json.setdefault("frame", {})["image"] = ""

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO processed_frames (
                camera_id, timestamp, mode, width, height, image, image_path, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                camera.get("id", ""),
                payload.get("timestamp", ""),
                frame.get("mode", ""),
                int(frame.get("width") or 0),
                int(frame.get("height") or 0),
                image if STORE_FRAME_IMAGES_IN_DB else "",
                "",
                json.dumps(payload_for_json),
            ),
        )
        frame_id = int(cursor.lastrowid)
        image_path = save_frame_image_file(frame_id, camera.get("id", ""), image)
        if image_path:
            connection.execute(
                "UPDATE processed_frames SET image_path = ? WHERE id = ?",
                (image_path, frame_id),
            )
        for detection in payload.get("detections") or []:
            geo = detection.get("geo") or {}
            connection.execute(
                """
                INSERT INTO detection_events (
                    frame_id, camera_id, timestamp, object_type, track_id, track_score, confidence,
                    distance_m, lat, lon, bbox_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    frame_id,
                    detection.get("camera_id", camera.get("id", "")),
                    detection.get("timestamp", payload.get("timestamp", "")),
                    detection.get("object_type", ""),
                    detection.get("track_id") or "",
                    float(detection.get("track_score") or 1),
                    float(detection.get("confidence") or 0),
                    float(detection.get("distance_m") or 0),
                    float(geo.get("lat") or 0),
                    float(geo.get("lon") or 0),
                    json.dumps(detection.get("bbox") or {}),
                    json.dumps(detection),
                ),
            )
    return frame_id


def save_frame_image_file(frame_id: int, camera_id: str, image: str) -> str:
    if not image:
        return ""
    content_type = "image/jpeg"
    data = image
    if image.startswith("data:"):
        header, _, data = image.partition(",")
        media_type = header.removeprefix("data:").split(";")[0]
        if media_type:
            content_type = media_type
    extension = ".jpg" if content_type in {"image/jpeg", "image/jpg"} else ".png"
    safe_camera_id = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in camera_id or "camera")
    relative = Path(safe_camera_id) / f"{frame_id}{extension}"
    output = FRAME_IMAGE_STORAGE_DIR / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.write_bytes(base64.b64decode(data))
    except Exception:
        return ""
    return str(relative)


def load_frame_image(row: sqlite3.Row) -> str:
    image = row["image"] or ""
    if image:
        return image
    if "image_path" not in row.keys() or not row["image_path"]:
        return ""
    path = FRAME_IMAGE_STORAGE_DIR / row["image_path"]
    if not path.exists():
        return ""
    suffix = path.suffix.lower()
    content_type = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{content_type};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def get_frame_image_blob(frame_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, camera_id, image, image_path FROM processed_frames WHERE id = ?",
            (frame_id,),
        ).fetchone()
    if row is None:
        return None
    image = row["image"] or ""
    if image:
        content_type = "image/jpeg"
        data = image
        if image.startswith("data:"):
            header, _, data = image.partition(",")
            media_type = header.removeprefix("data:").split(";")[0]
            if media_type:
                content_type = media_type
        try:
            return {
                "camera_id": row["camera_id"],
                "content_type": content_type,
                "bytes": base64.b64decode(data),
                "path": None,
            }
        except Exception:
            return None
    if "image_path" not in row.keys() or not row["image_path"]:
        return {"camera_id": row["camera_id"], "content_type": "", "bytes": b"", "path": None}
    path = (FRAME_IMAGE_STORAGE_DIR / row["image_path"]).resolve()
    if not path.exists():
        return {"camera_id": row["camera_id"], "content_type": "", "bytes": b"", "path": None}
    suffix = path.suffix.lower()
    content_type = "image/png" if suffix == ".png" else "image/jpeg"
    return {
        "camera_id": row["camera_id"],
        "content_type": content_type,
        "bytes": None,
        "path": path,
    }


def purge_frames_without_detections() -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM processed_frames
            WHERE id NOT IN (
                SELECT DISTINCT frame_id FROM detection_events
            )
            """
        )
        return int(cursor.rowcount)


def purge_old_frames(max_age_days: int) -> dict[str, int]:
    if max_age_days <= 0:
        return {"frames": 0, "events": 0, "files": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, image_path
            FROM processed_frames
            WHERE timestamp < ?
            """,
            (cutoff,),
        ).fetchall()
        frame_ids = [int(row["id"]) for row in rows]
        image_paths = [row["image_path"] for row in rows if row["image_path"]]
        if not frame_ids:
            return {"frames": 0, "events": 0, "files": 0}
        placeholders = ",".join("?" for _ in frame_ids)
        event_cursor = connection.execute(
            f"DELETE FROM detection_events WHERE frame_id IN ({placeholders})",
            frame_ids,
        )
        frame_cursor = connection.execute(
            f"DELETE FROM processed_frames WHERE id IN ({placeholders})",
            frame_ids,
        )
    files_deleted = delete_frame_image_files(image_paths)
    return {
        "frames": int(frame_cursor.rowcount),
        "events": int(event_cursor.rowcount),
        "files": files_deleted,
    }


def purge_orphan_frame_files() -> int:
    if not FRAME_IMAGE_STORAGE_DIR.exists():
        return 0
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT image_path
            FROM processed_frames
            WHERE image_path <> ''
            """
        ).fetchall()
    referenced = {str(row["image_path"]) for row in rows if row["image_path"]}
    deleted = 0
    for path in FRAME_IMAGE_STORAGE_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = str(path.relative_to(FRAME_IMAGE_STORAGE_DIR))
        except ValueError:
            continue
        if relative in referenced:
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError:
            pass
    prune_empty_frame_dirs()
    return deleted


def delete_frame_image_files(image_paths: list[str]) -> int:
    deleted = 0
    base = FRAME_IMAGE_STORAGE_DIR.resolve()
    for image_path in image_paths:
        candidate = (FRAME_IMAGE_STORAGE_DIR / image_path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        try:
            if candidate.exists() and candidate.is_file():
                candidate.unlink()
                deleted += 1
        except OSError:
            pass
    prune_empty_frame_dirs()
    return deleted


def prune_empty_frame_dirs() -> None:
    if not FRAME_IMAGE_STORAGE_DIR.exists():
        return
    for path in sorted(FRAME_IMAGE_STORAGE_DIR.rglob("*"), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def list_events(limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT e.*, f.timestamp AS frame_timestamp
            FROM detection_events e
            JOIN processed_frames f ON f.id = e.frame_id
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [event_from_row(row) for row in rows]


def get_event(event_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT e.*, f.timestamp AS frame_timestamp
            FROM detection_events e
            JOIN processed_frames f ON f.id = e.frame_id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
    return event_from_row(row) if row else None


def list_events_between(
    start: str,
    end: str,
    limit: int = 2000,
    object_type: str | None = None,
    confidence_min: float | None = None,
    camera_ids: list[str] | None = None,
    track_id: str | None = None,
    upper_color: str | None = None,
    lower_color: str | None = None,
    vehicle_color: str | None = None,
    attribute_confidence_min: float = 0.55,
) -> list[dict[str, Any]]:
    clauses = ["e.timestamp >= ?", "e.timestamp <= ?"]
    params: list[Any] = [start, end]
    if object_type:
        clauses.append("e.object_type = ?")
        params.append(object_type)
    if confidence_min is not None:
        clauses.append("e.confidence >= ?")
        params.append(confidence_min)
    if camera_ids:
        placeholders = ", ".join("?" for _ in camera_ids)
        clauses.append(f"e.camera_id IN ({placeholders})")
        params.extend(camera_ids)
    if track_id:
        clauses.append("e.track_id = ?")
        params.append(track_id)
    has_attribute_filter = any([upper_color, lower_color, vehicle_color])
    query_limit = max(limit, 50_000) if has_attribute_filter else limit
    params.append(query_limit)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT e.*, f.timestamp AS frame_timestamp
            FROM detection_events e
            JOIN processed_frames f ON f.id = e.frame_id
            WHERE {" AND ".join(clauses)}
            ORDER BY e.timestamp ASC, e.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    events = [event_from_row(row) for row in rows]
    return filter_events_by_attributes(events, upper_color, lower_color, vehicle_color, attribute_confidence_min)[:limit]


def filter_events_by_attributes(
    events: list[dict[str, Any]],
    upper_color: str | None = None,
    lower_color: str | None = None,
    vehicle_color: str | None = None,
    attribute_confidence_min: float = 0.55,
) -> list[dict[str, Any]]:
    if not any([upper_color, lower_color, vehicle_color]):
        return events
    output = []
    for event in events:
        attributes = (event.get("detection") or {}).get("attributes") or {}
        if upper_color and (
            attributes.get("upper_color") != upper_color
            or float(attributes.get("upper_color_confidence") or 0) < attribute_confidence_min
        ):
            continue
        if lower_color and (
            attributes.get("lower_color") != lower_color
            or float(attributes.get("lower_color_confidence") or 0) < attribute_confidence_min
        ):
            continue
        if vehicle_color and (
            attributes.get("vehicle_color") != vehicle_color
            or float(attributes.get("vehicle_color_confidence") or 0) < attribute_confidence_min
        ):
            continue
        output.append(event)
    return output


def get_frame(frame_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM processed_frames WHERE id = ?",
            (frame_id,),
        ).fetchone()
        if row is None:
            return None
        event_rows = connection.execute(
            "SELECT * FROM detection_events WHERE frame_id = ? ORDER BY id ASC",
            (frame_id,),
        ).fetchall()

    payload = load_json(row["payload_json"], {})
    payload["frame_id"] = row["id"]
    payload["camera_id"] = row["camera_id"]
    payload.setdefault("frame", {})["image"] = load_frame_image(row)
    payload["detections"] = [load_json(item["payload_json"], {}) for item in event_rows]
    return payload


def get_latest_frame_for_camera(camera_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id
            FROM processed_frames
            WHERE camera_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (camera_id,),
        ).fetchone()
    if row is None:
        return None
    return get_frame(int(row["id"]))


def list_frames_for_camera(
    camera_id: str,
    limit: int = 100,
    before_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    include_empty: bool = True,
) -> list[dict[str, Any]]:
    clauses = ["f.camera_id = ?"]
    params: list[Any] = [camera_id]
    if before_id is not None:
        clauses.append("f.id < ?")
        params.append(before_id)
    if start:
        clauses.append("f.timestamp >= ?")
        params.append(start)
    if end:
        clauses.append("f.timestamp <= ?")
        params.append(end)
    having_sql = "" if include_empty else "HAVING COUNT(e.id) > 0"
    params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                f.id,
                f.camera_id,
                f.timestamp,
                f.mode,
                f.width,
                f.height,
                f.image,
                f.image_path,
                COUNT(e.id) AS detections_count
            FROM processed_frames f
            LEFT JOIN detection_events e ON e.frame_id = f.id
            WHERE {" AND ".join(clauses)}
            GROUP BY f.id, f.camera_id, f.timestamp, f.mode, f.width, f.height, f.image, f.image_path
            {having_sql}
            ORDER BY f.timestamp DESC, f.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "camera_id": row["camera_id"],
            "timestamp": row["timestamp"],
            "mode": row["mode"],
            "width": int(row["width"]),
            "height": int(row["height"]),
            "detections_count": int(row["detections_count"] or 0),
            "has_image": bool(row["image"] or row["image_path"]),
        }
        for row in rows
    ]


def list_recent_frames(
    limit: int = 200,
    start: str | None = None,
    end: str | None = None,
    camera_id: str | None = None,
    include_empty: bool = True,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if camera_id:
        clauses.append("f.camera_id = ?")
        params.append(camera_id)
    if start:
        clauses.append("f.timestamp >= ?")
        params.append(start)
    if end:
        clauses.append("f.timestamp <= ?")
        params.append(end)
    having_sql = "" if include_empty else "HAVING COUNT(e.id) > 0"
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                f.id,
                f.camera_id,
                f.timestamp,
                f.mode,
                f.width,
                f.height,
                f.image,
                f.image_path,
                COUNT(e.id) AS detections_count
            FROM processed_frames f
            LEFT JOIN detection_events e ON e.frame_id = f.id
            {where_sql}
            GROUP BY f.id, f.camera_id, f.timestamp, f.mode, f.width, f.height, f.image, f.image_path
            {having_sql}
            ORDER BY f.timestamp DESC, f.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "camera_id": row["camera_id"],
            "timestamp": row["timestamp"],
            "mode": row["mode"],
            "width": int(row["width"]),
            "height": int(row["height"]),
            "detections_count": int(row["detections_count"] or 0),
            "has_image": bool(row["image"] or row["image_path"]),
        }
        for row in rows
    ]


def event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    detection = load_json(row["payload_json"], {})
    if row["track_id"] and not detection.get("track_id"):
        detection["track_id"] = row["track_id"]
    if "track_score" in row.keys() and not detection.get("track_score"):
        detection["track_score"] = row["track_score"]
    return {
        "id": row["id"],
        "frame_id": row["frame_id"],
        "camera_id": row["camera_id"],
        "timestamp": row["timestamp"],
        "frame_timestamp": row["frame_timestamp"],
        "detection": detection,
    }


def feedback_from_row(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "event_id": int(row["event_id"]) if row["event_id"] is not None else None,
        "frame_id": int(row["frame_id"]) if row["frame_id"] is not None else None,
        "camera_id": row["camera_id"],
        "track_id": row["track_id"],
        "feedback_type": row["feedback_type"],
        "original_value": row["original_value"],
        "corrected_value": row["corrected_value"],
        "confidence": int(row["confidence"]) if row["confidence"] is not None else None,
        "reviewer_id": row["reviewer_id"],
        "reviewer_role": row["reviewer_role"],
        "comment": row["comment"],
        "curation_status": row["curation_status"],
        "deleted_at": row["deleted_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "payload": load_json(row["payload_json"], {}),
    }


def create_feedback_annotation(event_id: int | None, payload: dict[str, Any], reviewer: dict[str, Any]) -> dict[str, Any]:
    event = get_event(event_id) if event_id else None
    detection = (event or {}).get("detection") or {}
    now = _utc_now()
    frame_id = payload.get("frame_id") or (event or {}).get("frame_id")
    camera_id = payload.get("camera_id") or (event or {}).get("camera_id") or detection.get("camera_id") or ""
    track_id = payload.get("track_id") or detection.get("track_id") or ""
    curation_status = payload.get("curation_status") or ("privacy_blocked" if payload.get("feedback_type") == "privacy_sensitive" else "pending")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO feedback_annotations (
                event_id, frame_id, camera_id, track_id, feedback_type, original_value,
                corrected_value, confidence, reviewer_id, reviewer_role, comment,
                curation_status, deleted_at, created_at, updated_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                frame_id,
                camera_id,
                track_id,
                payload["feedback_type"],
                payload.get("original_value") or "",
                payload.get("corrected_value") or "",
                payload.get("confidence"),
                reviewer.get("username", ""),
                reviewer.get("role", ""),
                payload.get("comment") or "",
                curation_status,
                "",
                now,
                now,
                json.dumps(payload.get("payload") or {}),
            ),
        )
        feedback_id = int(cursor.lastrowid)
    return get_feedback_annotation(feedback_id) or {}


def get_feedback_annotation(feedback_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM feedback_annotations WHERE id = ?", (feedback_id,)).fetchone()
    return feedback_from_row(row) if row else None


def list_feedback_annotations(
    event_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    camera_id: str | None = None,
    feedback_type: str | None = None,
    reviewer_id: str | None = None,
    curation_status: str | None = None,
    include_deleted: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if event_id is not None:
        clauses.append("event_id = ?")
        params.append(event_id)
    if start:
        clauses.append("created_at >= ?")
        params.append(start)
    if end:
        clauses.append("created_at <= ?")
        params.append(end)
    if camera_id:
        clauses.append("camera_id = ?")
        params.append(camera_id)
    if feedback_type:
        clauses.append("feedback_type = ?")
        params.append(feedback_type)
    if reviewer_id:
        clauses.append("reviewer_id = ?")
        params.append(reviewer_id)
    if curation_status:
        clauses.append("curation_status = ?")
        params.append(curation_status)
    if not include_deleted:
        clauses.append("deleted_at = ''")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM feedback_annotations
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [feedback_from_row(row) for row in rows]


def update_feedback_annotation(feedback_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "feedback_type",
        "original_value",
        "corrected_value",
        "confidence",
        "comment",
        "curation_status",
        "payload",
        "deleted",
    }
    fields = []
    params: list[Any] = []
    for key, value in changes.items():
        if key not in allowed:
            continue
        if key == "payload":
            fields.append("payload_json = ?")
            params.append(json.dumps(value or {}))
        elif key == "deleted":
            fields.append("deleted_at = ?")
            params.append(_utc_now() if value else "")
        else:
            fields.append(f"{key} = ?")
            params.append(value if value is not None else "")
    if not fields:
        return get_feedback_annotation(feedback_id)
    fields.append("updated_at = ?")
    params.append(_utc_now())
    params.append(feedback_id)
    with get_connection() as connection:
        cursor = connection.execute(f"UPDATE feedback_annotations SET {', '.join(fields)} WHERE id = ?", params)
        if cursor.rowcount == 0:
            return None
    return get_feedback_annotation(feedback_id)


def feedback_summary_for_events(event_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not event_ids:
        return {}
    placeholders = ", ".join("?" for _ in event_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT event_id, feedback_type, curation_status, deleted_at, created_at
            FROM feedback_annotations
            WHERE event_id IN ({placeholders}) AND deleted_at = ''
            ORDER BY created_at DESC, id DESC
            """,
            event_ids,
        ).fetchall()
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        event_id = int(row["event_id"])
        item = output.setdefault(event_id, {"count": 0, "types": [], "curation_status": row["curation_status"], "latest_type": row["feedback_type"]})
        item["count"] += 1
        item["types"].append(row["feedback_type"])
        if row["feedback_type"] == "privacy_sensitive":
            item["privacy_sensitive"] = True
        if item["count"] > 1 and row["feedback_type"] not in item["types"][:1]:
            item["has_conflict"] = True
    return output


def attach_feedback_to_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = feedback_summary_for_events([int(item["id"]) for item in events])
    for event in events:
        summary = summaries.get(int(event["id"]), {"count": 0, "types": []})
        event["feedback"] = summary
        event["review_priority"] = calculate_review_priority(event, summary)
    return events


def calculate_review_priority(event: dict[str, Any], feedback: dict[str, Any] | None = None) -> float:
    detection = event.get("detection") or {}
    confidence = float(detection.get("confidence") or 0)
    bbox = detection.get("bbox") or {}
    geo = detection.get("geo") or {}
    attributes = detection.get("attributes") or {}
    score = max(0.0, 1.0 - confidence) * 40
    if not (feedback or {}).get("count"):
        score += 25
    if detection.get("object_type") in {"bicicleta", "moto", "onibus", "caminhao"}:
        score += 8
    if bbox and (float(bbox.get("w") or 0) * float(bbox.get("h") or 0)) < 6000:
        score += 10
    if geo and float(geo.get("accuracy_m") or 0) > 80:
        score += 6
    if any(str(attributes.get(key) or "").lower() == "desconhecido" for key in ("upper_color", "lower_color", "vehicle_color")):
        score += 6
    return round(score, 3)


def list_review_events(
    start: str,
    end: str,
    limit: int = 200,
    object_type: str | None = None,
    confidence_min: float | None = None,
    confidence_max: float | None = None,
    camera_ids: list[str] | None = None,
    track_id: str | None = None,
    feedback_state: str | None = None,
    feedback_type: str | None = None,
    curation_status: str | None = None,
    upper_color: str | None = None,
    lower_color: str | None = None,
    vehicle_color: str | None = None,
    attribute_confidence_min: float = 0.55,
) -> list[dict[str, Any]]:
    events = list_events_between(start, end, max(limit * 5, 500), object_type, confidence_min, camera_ids, track_id, upper_color, lower_color, vehicle_color, attribute_confidence_min)
    if confidence_max is not None:
        events = [item for item in events if float((item.get("detection") or {}).get("confidence") or 0) <= confidence_max]
    events = attach_feedback_to_events(events)
    if feedback_state == "without":
        events = [item for item in events if not item.get("feedback", {}).get("count")]
    elif feedback_state == "with":
        events = [item for item in events if item.get("feedback", {}).get("count")]
    if feedback_type:
        events = [item for item in events if feedback_type in item.get("feedback", {}).get("types", [])]
    if curation_status:
        events = [item for item in events if item.get("feedback", {}).get("curation_status") == curation_status]
    events.sort(key=lambda item: (-float(item.get("review_priority") or 0), item.get("timestamp") or ""))
    return events[:limit]


def feedback_stats(start: str | None = None, end: str | None = None, camera_id: str | None = None, object_type: str | None = None) -> dict[str, Any]:
    rows = list_feedback_annotations(start=start, end=end, camera_id=camera_id, limit=50_000)
    if object_type:
        filtered_rows = []
        for item in rows:
            event_id = item.get("event_id")
            if not event_id:
                continue
            event = get_event(int(event_id)) or {}
            if ((event.get("detection") or {}).get("object_type")) == object_type:
                filtered_rows.append(item)
        rows = filtered_rows
    by_type: dict[str, int] = {}
    by_camera: dict[str, int] = {}
    by_reviewer: dict[str, int] = {}
    false_positives_by_camera: dict[str, int] = {}
    false_negatives_by_camera: dict[str, int] = {}
    camera_issues: dict[str, int] = {}
    for item in rows:
        by_type[item["feedback_type"]] = by_type.get(item["feedback_type"], 0) + 1
        if item["camera_id"]:
            by_camera[item["camera_id"]] = by_camera.get(item["camera_id"], 0) + 1
            if item["feedback_type"] == "false_positive":
                false_positives_by_camera[item["camera_id"]] = false_positives_by_camera.get(item["camera_id"], 0) + 1
            if item["feedback_type"] == "false_negative":
                false_negatives_by_camera[item["camera_id"]] = false_negatives_by_camera.get(item["camera_id"], 0) + 1
            if item["feedback_type"] == "camera_issue":
                camera_issues[item["camera_id"]] = camera_issues.get(item["camera_id"], 0) + 1
        if item["reviewer_id"]:
            by_reviewer[item["reviewer_id"]] = by_reviewer.get(item["reviewer_id"], 0) + 1
    reviewed_events = len({item["event_id"] for item in rows if item.get("event_id")})
    correct = by_type.get("detection_correct", 0)
    false_positive = by_type.get("false_positive", 0)
    return {
        "total_feedback": len(rows),
        "reviewed_events": reviewed_events,
        "correct_percent": round((correct / max(1, correct + false_positive + by_type.get("wrong_class", 0))) * 100, 2),
        "by_type": by_type,
        "by_camera": by_camera,
        "by_reviewer": by_reviewer,
        "false_positives_by_camera": false_positives_by_camera,
        "false_negatives_by_camera": false_negatives_by_camera,
        "camera_issues": camera_issues,
        "privacy_sensitive": by_type.get("privacy_sensitive", 0),
        "alert_false": by_type.get("alert_false", 0),
    }


def create_feedback_dataset_export(dataset_version: str, created_by: str, filters: dict[str, Any], output_path: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    now = _utc_now()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO feedback_dataset_exports (
                dataset_version, created_at, created_by, filters_json, output_path, items_count, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (dataset_version, now, created_by, json.dumps(filters), output_path, len(items), json.dumps({"items": items})),
        )
        export_id = int(cursor.lastrowid)
    return {"id": export_id, "dataset_version": dataset_version, "created_at": now, "created_by": created_by, "output_path": output_path, "items_count": len(items)}
