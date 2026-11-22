from __future__ import annotations

import numpy as np

import attribute_extractor
from attribute_extractor import classify_region_color
from frame_processor import safe_extract_attributes


def test_classify_region_color_handles_neutral_region_without_chromatic_pixels():
    region = np.full((24, 24, 3), 120, dtype=np.uint8)

    result = classify_region_color(region, region_quality=1.0, ignore_skin=False)

    assert result["color"] in {"cinza", "desconhecido"}
    assert "top_colors" in result


def test_classify_region_color_handles_skin_filter_removing_all_chromatic_pixels():
    region = np.zeros((24, 24, 3), dtype=np.uint8)
    region[:, :] = [80, 120, 180]

    result = classify_region_color(region, region_quality=1.0, ignore_skin=True)

    assert result["color"] == "desconhecido"
    assert result["warning"] in {"not_enough_valid_pixels", "no_classifiable_pixels", "low_color_confidence"}


def test_visual_attributes_feature_can_be_disabled_without_blocking_detection(admin_client):
    response = admin_client.patch(
        "/api/admin/features/visual_attributes_enabled",
        json={"enabled": False, "reason": "teste"},
    )
    assert response.status_code == 200
    frame = np.full((64, 64, 3), 120, dtype=np.uint8)

    attrs = safe_extract_attributes(frame, "pessoa", {"x": 8, "y": 8, "w": 24, "h": 40})

    assert attrs["upper_color"] == "desconhecido"
    assert attrs["attribute_warning"] == "visual_attributes_disabled"


def test_visual_attribute_errors_do_not_block_detection(monkeypatch, admin_client):
    admin_client.patch("/api/admin/features/visual_attributes_enabled", json={"enabled": True, "reason": "teste"})

    def boom(*args, **kwargs):
        raise RuntimeError("broken extractor")

    monkeypatch.setattr(attribute_extractor, "extract_attributes", boom)
    frame = np.full((64, 64, 3), 120, dtype=np.uint8)

    attrs = safe_extract_attributes(frame, "carro", {"x": 8, "y": 8, "w": 24, "h": 24})

    assert attrs["vehicle_color"] == "desconhecido"
    assert attrs["attribute_warning"] == "visual_attribute_extraction_failed"
