# Kaelis Argos ML Training Scaffold

Este diretorio documenta o fluxo seguro para usar feedback humano na melhoria da IA.

Fluxo recomendado:

1. Operadores revisam frames na aba Revisao.
2. Feedbacks ficam em `feedback_annotations`.
3. Admin/analista faz curadoria e aprova somente itens validos.
4. Dataset versionado e exportado por `/feedback/export-dataset`.
5. Treino candidato roda fora da producao.
6. Avaliacao compara candidato contra baseline.
7. Deploy exige aprovacao manual e plano de rollback.

O MVP nao treina automaticamente o modelo principal em producao.

Scripts previstos:

- `export_feedback_dataset.py`: gera dataset YOLO/COCO a partir de feedback aprovado.
- `train_candidate.py`: treina modelo candidato fora do ambiente operacional.
- `evaluate_candidate.py`: compara modelo candidato com baseline.
- `calibration.py`: gera recomendacoes conservadoras de thresholds e cameras para revisao.
