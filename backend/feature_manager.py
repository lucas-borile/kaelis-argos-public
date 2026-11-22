from __future__ import annotations

from functools import lru_cache
from typing import Any


DEFAULT_FEATURES: list[dict[str, Any]] = [
    {
        "feature_key": "chat_enabled",
        "feature_name": "Chat operacional",
        "category": "operacao",
        "enabled": True,
        "processing_cost_level": "baixo",
        "cpu_cost": "baixo",
        "gpu_cost": "nenhum",
        "memory_cost": "baixo",
        "storage_cost": "baixo",
        "network_cost": "baixo",
        "estimated_processing_impact": "Baixo. Usa principalmente banco, WebSocket e renderizacao de mensagens; nao aciona YOLO.",
        "disable_consequence": "Oculta e bloqueia o chat entre operadores, incluindo mencoes a usuarios, geofences, eventos e frames.",
    },
    {
        "feature_key": "telegram_enabled",
        "feature_name": "Alertas Telegram",
        "category": "integracoes",
        "enabled": True,
        "processing_cost_level": "baixo",
        "cpu_cost": "baixo",
        "gpu_cost": "nenhum",
        "memory_cost": "baixo",
        "storage_cost": "baixo",
        "network_cost": "medio",
        "estimated_processing_impact": "Baixo a medio. Custo aparece quando ha alerta, com encode/envio de imagem e chamada HTTPS externa.",
        "disable_consequence": "Para configuracao, teste e envio de alertas Telegram; alertas internos e WebSocket continuam funcionando.",
    },
    {
        "feature_key": "benchmarks_enabled",
        "feature_name": "Benchmarks de stress",
        "category": "diagnostico",
        "enabled": True,
        "processing_cost_level": "alto",
        "cpu_cost": "alto",
        "gpu_cost": "alto",
        "memory_cost": "medio",
        "storage_cost": "medio",
        "network_cost": "baixo",
        "estimated_processing_impact": "Alto durante execucao. Dispara processos de teste que simulam varias cameras e podem competir com o processamento real.",
        "disable_consequence": "Bloqueia criacao e consulta de jobs de benchmark; relatorios ja gravados permanecem em disco.",
    },
    {
        "feature_key": "human_feedback_enabled",
        "feature_name": "Revisao humana e feedback",
        "category": "qualidade",
        "enabled": True,
        "processing_cost_level": "medio",
        "cpu_cost": "baixo",
        "gpu_cost": "nenhum",
        "memory_cost": "baixo",
        "storage_cost": "medio",
        "network_cost": "baixo",
        "estimated_processing_impact": "Medio em uso intensivo. Le eventos/frames historicos, grava anotacoes e pode aumentar consultas ao historico.",
        "disable_consequence": "Bloqueia fila de revisao, anotacoes de feedback, estatisticas de feedback e correcoes manuais.",
    },
    {
        "feature_key": "heatmap_enabled",
        "feature_name": "Heatmap historico",
        "category": "analise",
        "enabled": True,
        "processing_cost_level": "baixo",
        "cpu_cost": "baixo",
        "gpu_cost": "nenhum",
        "memory_cost": "baixo",
        "storage_cost": "nenhum",
        "network_cost": "baixo",
        "estimated_processing_impact": "Baixo. Agrega eventos recentes e renderiza pontos no frontend; nao altera inferencia.",
        "disable_consequence": "Remove o mapa de calor de deteccoes; eventos, mapa principal e geofences continuam ativos.",
    },
    {
        "feature_key": "floor_plans_enabled",
        "feature_name": "Plantas internas",
        "category": "indoor",
        "enabled": True,
        "processing_cost_level": "medio",
        "cpu_cost": "medio",
        "gpu_cost": "baixo",
        "memory_cost": "medio",
        "storage_cost": "medio",
        "network_cost": "medio",
        "estimated_processing_impact": "Medio quando usado. Upload/decodificacao de imagem, calculo de cobertura e processed-frame indoor podem consumir CPU e memoria.",
        "disable_consequence": "Bloqueia CRUD, upload, cameras indoor, zonas e calculo de cobertura de plantas internas.",
    },
    {
        "feature_key": "camera_discovery_enabled",
        "feature_name": "Descoberta ONVIF",
        "category": "cameras",
        "enabled": True,
        "processing_cost_level": "medio",
        "cpu_cost": "baixo",
        "gpu_cost": "nenhum",
        "memory_cost": "baixo",
        "storage_cost": "nenhum",
        "network_cost": "medio",
        "estimated_processing_impact": "Medio em rede. Faz descoberta ONVIF e consultas a cameras; nao afeta o loop principal.",
        "disable_consequence": "Bloqueia descoberta automatica e consulta de perfis ONVIF; cadastro manual e teste RTSP seguem disponiveis.",
    },
    {
        "feature_key": "visual_attributes_enabled",
        "feature_name": "Cores de roupas e veiculos",
        "category": "analise",
        "enabled": True,
        "processing_cost_level": "medio",
        "cpu_cost": "medio",
        "gpu_cost": "nenhum",
        "memory_cost": "baixo",
        "storage_cost": "baixo",
        "network_cost": "nenhum",
        "estimated_processing_impact": "Medio em cenas com muitas deteccoes. Executa recortes e classificacao HSV/LAB por objeto depois do YOLO, usando CPU.",
        "disable_consequence": "Mantem deteccao de objetos, tracking, geolocalizacao e eventos, mas passa a registrar cores de roupa/carro/moto como desconhecidas.",
    },
    {
        "feature_key": "dataset_export_enabled",
        "feature_name": "Exportacao de dataset",
        "category": "qualidade",
        "enabled": True,
        "processing_cost_level": "medio",
        "cpu_cost": "medio",
        "gpu_cost": "nenhum",
        "memory_cost": "medio",
        "storage_cost": "alto",
        "network_cost": "baixo",
        "estimated_processing_impact": "Medio a alto durante exportacao. Le muitas anotacoes/frames e grava arquivos de dataset.",
        "disable_consequence": "Bloqueia exportacao de datasets para treino; revisao e feedback continuam se `human_feedback_enabled` estiver ativo.",
    },
]

FEATURE_BY_KEY = {item["feature_key"]: item for item in DEFAULT_FEATURES}


@lru_cache(maxsize=1)
def feature_flags_by_key() -> dict[str, dict[str, Any]]:
    from db import list_feature_flags

    return {item["feature_key"]: item for item in list_feature_flags()}


def invalidate_feature_cache() -> None:
    feature_flags_by_key.cache_clear()


def list_effective_features() -> list[dict[str, Any]]:
    flags = feature_flags_by_key()
    return [effective_feature(feature["feature_key"], flags) for feature in DEFAULT_FEATURES]


def effective_feature(feature_key: str, flags: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if feature_key not in FEATURE_BY_KEY:
        raise KeyError(feature_key)
    base = dict(FEATURE_BY_KEY[feature_key])
    persisted = (flags or feature_flags_by_key()).get(feature_key)
    if persisted:
        base.update(
            {
                "id": persisted.get("id"),
                "enabled": persisted.get("enabled", base["enabled"]),
                "updated_at": persisted.get("updated_at", ""),
                "updated_by": persisted.get("updated_by", ""),
                "reason": persisted.get("reason", ""),
            }
        )
    else:
        base.update({"id": None, "updated_at": "", "updated_by": "", "reason": ""})
    return base


def is_feature_enabled(feature_key: str) -> bool:
    if feature_key not in FEATURE_BY_KEY:
        return True
    return bool(effective_feature(feature_key).get("enabled", True))


def disabled_response_detail(feature_key: str) -> dict[str, Any]:
    feature = effective_feature(feature_key)
    return {
        "message": "feature disabled by administrator",
        "feature_key": feature_key,
        "feature_name": feature.get("feature_name", feature_key),
        "disable_consequence": feature.get("disable_consequence", ""),
        "estimated_processing_impact": feature.get("estimated_processing_impact", ""),
    }
