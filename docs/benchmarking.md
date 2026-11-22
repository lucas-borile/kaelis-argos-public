# Benchmarking do Kaelis Argos

Este guia descreve como medir latência, throughput, fila, recursos e custo estimado do pipeline de inferência do Kaelis Argos.

## Objetivo

Os benchmarks respondem perguntas práticas:

- Quantos frames por segundo o ambiente processa?
- A fila cresce quando muitas câmeras são simuladas?
- Qual é a latência média, P95 e P99?
- Qual é a estimativa mensal de frames processados, armazenamento e horas de inferência?
- O hardware atual é viável para uma quantidade alvo de câmeras e intervalo de captura?

## Instalação

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -r backend/requirements.txt
```

Dependências usadas pelos benchmarks:

- `ultralytics`
- `opencv-python-headless`
- `psutil`
- `matplotlib`
- `tqdm`

## Preparar Frames De Teste

Crie uma pasta com imagens reais ou frames extraídos de câmeras:

```text
benchmarks/frames_teste/
  cam01_0001.jpg
  cam01_0002.jpg
  cam02_0001.jpg
```

Use imagens parecidas com os streams reais. Frames muito pequenos ou artificiais distorcem a latência e a taxa de detecção.

## Benchmark Local De Inferência

Executa YOLO sobre uma pasta de imagens e gera CSV, resumo JSON e HTML:

```bash
./.venv312/bin/python benchmarks/bench_argos_inference.py \
  --source benchmarks/frames_teste \
  --model yolov8n.pt \
  --device cpu \
  --imgsz 640 \
  --conf 0.25 \
  --repeat 3 \
  --out benchmarks/results/inference_results.csv \
  --report benchmarks/reports/inference_report.html
```

Saídas:

- `benchmarks/results/inference_results.csv`
- `benchmarks/results/inference_results_summary.json`
- `benchmarks/reports/inference_report.html`

## Stress Test Com Câmeras Simuladas

Simula N câmeras, cada uma enviando 1 frame a cada X segundos:

```bash
./.venv312/bin/python benchmarks/stress_argos_client.py \
  --source benchmarks/frames_teste \
  --cameras 15 \
  --interval 30 \
  --duration-minutes 30 \
  --model yolov8n.pt \
  --device cpu \
  --imgsz 640 \
  --conf 0.25 \
  --workers 2 \
  --out benchmarks/results/stress_results.jsonl \
  --summary benchmarks/results/stress_summary.json \
  --report benchmarks/reports/stress_report.html
```

Exemplo de demanda:

```text
15 câmeras / 30 segundos = 0,5 FPS demandado
```

Para 1600 câmeras com 1 frame a cada 10 segundos:

```text
1600 / 10 = 160 FPS demandado
```

## Analisar Resultados Existentes

O analisador aceita CSV ou JSONL:

```bash
./.venv312/bin/python benchmarks/analyze_benchmark_results.py \
  --input benchmarks/results/stress_results.jsonl \
  --cameras 15 \
  --interval 30 \
  --out benchmarks/results/analysis_summary.json \
  --report benchmarks/reports/analysis_report.html
```

## Logs Do Pipeline Real

O backend pode gravar um JSONL com métricas de processamento real. Por padrão isso fica desligado.

Ative com:

```bash
ARGOS_BENCHMARK_LOGS=true \
ARGOS_BENCHMARK_LOG_PATH=./logs/argos_processing.jsonl \
ARGOS_BENCHMARK_REPORTS_PATH=./benchmarks/reports \
../.venv312/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Execute o comando acima dentro da pasta `backend/`, como no fluxo local do projeto.

Depois analise:

```bash
./.venv312/bin/python benchmarks/analyze_benchmark_results.py \
  --input backend/logs/argos_processing.jsonl \
  --out benchmarks/results/real_pipeline_summary.json \
  --report benchmarks/reports/real_pipeline_report.html
```

Campos principais do JSONL real:

- `camera_id`
- `frame_id`
- `frame_mode`
- `source_type`
- `resolve_latency_ms`
- `capture_latency_ms`
- `inference_latency_ms`
- `storage_latency_ms`
- `total_latency_ms`
- `detections_count`
- `classes`
- `cpu_percent`
- `ram_mb`
- `saved_to_storage`

## Fórmulas Usadas

Demanda de processamento:

```text
demand_fps = cameras / interval_seconds
```

Capacidade medida:

```text
measured_fps = frames_processed / duration_seconds
```

Capacidade segura:

```text
safe_capacity_fps = measured_fps * 0.5
```

Estimativas mensais:

```text
frames_per_day = cameras * 86400 / interval_seconds
frames_per_month = frames_per_day * 30
processed_gb_per_month = frames_per_month * avg_image_mb / 1024
stored_gb_per_month = processed_gb_per_month * detection_frame_rate
inference_hours_per_month = frames_per_month * avg_inference_seconds / 3600
```

## Como Interpretar O Resultado

Status `viável`:

```text
safe_capacity_fps >= demand_fps
```

Status `limite`:

```text
measured_fps >= demand_fps
safe_capacity_fps < demand_fps
```

Status `não viável`:

```text
measured_fps < demand_fps
```

Sinais de gargalo:

- P95 ou P99 muito acima do intervalo entre frames.
- `queue_growth_detected=true`.
- CPU sustentada próxima de 100%.
- RAM crescendo ao longo do teste.
- `frames_error` alto.
- `capture_latency_ms` maior que `inference_latency_ms`, indicando gargalo de rede/player.

## Observações

Os scripts simulam ingestão usando imagens locais. Isso mede bem a parte de inferência e fila, mas não substitui um teste com streams reais, onde resolução, disponibilidade do player e rede podem dominar a latência.

Para produção em muitas câmeras, use workers separados de captura, fila/event bus, workers YOLO com GPU, storage externo e banco com índices temporais/geoespaciais.
