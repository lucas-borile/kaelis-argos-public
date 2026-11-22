from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass
class CalibrationRecommendation:
    title: str
    reason: str
    action: str
    risk: str = "review_required"


def build_recommendations(feedback_rows: list[dict[str, Any]]) -> list[CalibrationRecommendation]:
    """Generate conservative calibration suggestions from curated feedback.

    This function intentionally does not mutate camera settings or model
    thresholds. It only summarizes patterns for an admin/analyst review step.
    """
    by_type = Counter(row.get("feedback_type") for row in feedback_rows)
    false_positive_by_camera: dict[str, int] = defaultdict(int)
    false_negative_by_camera: dict[str, int] = defaultdict(int)
    issue_by_camera: dict[str, int] = defaultdict(int)

    for row in feedback_rows:
        camera_id = row.get("camera_id") or "camera-desconhecida"
        feedback_type = row.get("feedback_type")
        if feedback_type == "false_positive":
            false_positive_by_camera[camera_id] += 1
        elif feedback_type == "false_negative":
            false_negative_by_camera[camera_id] += 1
        elif feedback_type == "camera_issue":
            issue_by_camera[camera_id] += 1

    recommendations: list[CalibrationRecommendation] = []
    for camera_id, count in sorted(false_positive_by_camera.items(), key=lambda item: item[1], reverse=True)[:5]:
        recommendations.append(
            CalibrationRecommendation(
                title=f"Avaliar sensibilidade em {camera_id}",
                reason=f"{count} falso(s) positivo(s) revisado(s).",
                action="Revisar threshold por classe/camera antes de aplicar qualquer mudanca.",
            )
        )
    for camera_id, count in sorted(false_negative_by_camera.items(), key=lambda item: item[1], reverse=True)[:5]:
        recommendations.append(
            CalibrationRecommendation(
                title=f"Priorizar exemplos nao detectados em {camera_id}",
                reason=f"{count} falso(s) negativo(s) revisado(s).",
                action="Aprovar curadoria e incluir em dataset candidato, sem deploy automatico.",
            )
        )
    for camera_id, count in sorted(issue_by_camera.items(), key=lambda item: item[1], reverse=True)[:5]:
        recommendations.append(
            CalibrationRecommendation(
                title=f"Verificar qualidade da camera {camera_id}",
                reason=f"{count} problema(s) de camera reportado(s).",
                action="Checar foco, angulo, iluminacao, stream e posicionamento.",
            )
        )
    if by_type.get("privacy_sensitive"):
        recommendations.append(
            CalibrationRecommendation(
                title="Bloquear dados sensiveis de exportacao",
                reason=f"{by_type['privacy_sensitive']} item(ns) marcado(s) como sensivel.",
                action="Manter privacy_blocked fora de datasets de treino.",
                risk="privacy_blocked",
            )
        )
    return recommendations
