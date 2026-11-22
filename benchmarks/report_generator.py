from __future__ import annotations

import base64
import html
import io
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmark_utils import class_counts, ensure_parent

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None


def generate_html_report(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    report_path: str | Path,
    test_type: str,
) -> Path:
    output = ensure_parent(report_path)
    charts = build_charts(rows, summary)
    diagnostic = diagnostic_text(summary)
    html_text = render_report(rows, summary, charts, diagnostic, test_type)
    output.write_text(html_text, encoding="utf-8")
    return output


def build_charts(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, str]:
    if plt is None:
        return {}
    x = list(range(1, len(rows) + 1))
    inference = [float(row.get("inference_latency_ms") or 0) for row in rows]
    queue_sizes = [float(row.get("queue_size_at_process") or row.get("queue_size_at_enqueue") or 0) for row in rows]
    cpu = [float(row.get("cpu_percent") or row.get("cpu_after_percent") or 0) for row in rows]
    ram = [float(row.get("ram_mb") or row.get("ram_after_mb") or 0) for row in rows]
    classes = class_counts(rows)
    camera_events = camera_detection_counts(rows)
    processing = summary.get("processing_summary", {})

    return {
        "latency_over_time": line_chart(x, inference, "Latência de Inferência", "frame", "ms"),
        "latency_histogram": histogram(inference, "Distribuição de Latência", "ms"),
        "queue_over_time": line_chart(x, queue_sizes, "Tamanho da Fila", "frame", "fila"),
        "cpu_over_time": line_chart(x, cpu, "CPU ao Longo do Tempo", "frame", "%"),
        "ram_over_time": line_chart(x, ram, "RAM ao Longo do Tempo", "frame", "MB"),
        "detections_by_class": bar_chart(classes, "Detecções por Classe", "classe", "quantidade"),
        "events_by_camera": bar_chart(camera_events, "Eventos por Câmera", "camera", "detecções"),
        "fps_compare": bar_chart(
            Counter(
                {
                    "demanda FPS": float(processing.get("demand_fps") or 0),
                    "FPS medido": float(processing.get("measured_fps") or 0),
                    "capacidade segura": float(processing.get("safe_capacity_fps") or 0),
                }
            ),
            "FPS Demandado vs Medido",
            "métrica",
            "FPS",
        ),
    }


def line_chart(x: list[int], y: list[float], title: str, xlabel: str, ylabel: str) -> str:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(x, y, linewidth=1.6, color="#2457a6")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    return fig_to_base64(fig)


def histogram(values: list[float], title: str, xlabel: str) -> str:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    clean = [value for value in values if value >= 0]
    ax.hist(clean, bins=min(30, max(5, len(clean) // 2 or 5)), color="#2457a6", alpha=0.82)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("frames")
    ax.grid(True, alpha=0.2)
    return fig_to_base64(fig)


def bar_chart(values: Counter | dict[str, float], title: str, xlabel: str, ylabel: str) -> str:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    items = list(values.items()) or [("sem dados", 0)]
    labels = [str(key) for key, _ in items]
    data = [float(value) for _, value in items]
    ax.bar(labels, data, color="#2457a6")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=25)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    return fig_to_base64(fig)


def fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def camera_detection_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        camera_id = str(row.get("camera_id") or "local")
        counter[camera_id] += int(float(row.get("detections_count") or 0))
    return counter


def diagnostic_text(summary: dict[str, Any]) -> str:
    processing = summary.get("processing_summary", {})
    queue_summary = summary.get("queue_summary", {})
    config = summary.get("test_config", {})
    status = processing.get("viability_status", "indefinido")
    cameras = config.get("cameras", "-")
    interval = config.get("interval_seconds") or config.get("interval") or "-"
    demand = processing.get("demand_fps", 0)
    safe = processing.get("safe_capacity_fps", 0)
    measured = processing.get("measured_fps", 0)
    queue_growth = queue_summary.get("queue_growth_detected", False)
    if status == "viável":
        first = f"O ambiente testado é viável para {cameras} câmera(s) com intervalo de {interval}s."
    elif status == "limite":
        first = f"O ambiente está no limite para {cameras} câmera(s) com intervalo de {interval}s."
    else:
        first = f"O ambiente não é viável para {cameras} câmera(s) com intervalo de {interval}s."
    queue = "A fila permaneceu estável." if not queue_growth else "Foi detectado crescimento crítico de fila."
    return (
        f"{first} A demanda estimada é de {demand} FPS, o FPS medido foi {measured} "
        f"e a capacidade segura calculada foi {safe} FPS. {queue}"
    )


def render_report(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    charts: dict[str, str],
    diagnostic: str,
    test_type: str,
) -> str:
    timing = summary.get("timing", {})
    config = summary.get("test_config", {})
    processing = summary.get("processing_summary", {})
    latency = summary.get("latency_summary", {})
    queue = summary.get("queue_summary", {})
    resources = summary.get("resource_summary", {})
    monthly = summary.get("monthly_estimates", {})
    detections = summary.get("detections_summary", {})

    metrics = {
        "Status": processing.get("viability_status", "indefinido"),
        "FPS demandado": processing.get("demand_fps", 0),
        "FPS medido": processing.get("measured_fps", 0),
        "Capacidade segura": processing.get("safe_capacity_fps", 0),
        "Frames processados": processing.get("frames_processed", 0),
        "Erro percentual": f"{processing.get('error_rate_percent', 0)}%",
        "Latência média": f"{latency.get('avg_latency_ms', 0)} ms",
        "P95": f"{latency.get('p95_latency_ms', 0)} ms",
        "P99": f"{latency.get('p99_latency_ms', 0)} ms",
        "Maior fila": queue.get("max_queue_size", 0),
        "CPU média": f"{resources.get('avg_cpu_percent', 0)}%",
        "RAM máxima": f"{resources.get('max_ram_mb', 0)} MB",
        "GB/mês processado": monthly.get("processed_gb_per_month", 0),
        "GB/mês armazenado": monthly.get("stored_gb_per_month", 0),
        "Horas inferência/mês": monthly.get("inference_hours_per_month", 0),
    }
    class_total = sum((detections.get("detections_by_class") or {}).values()) or 1
    class_rows = [
        {"classe": key, "quantidade": value, "percentual": f"{round(value / class_total * 100, 2)}%"}
        for key, value in sorted((detections.get("detections_by_class") or {}).items())
    ]

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Kaelis Argos Benchmark Report</title>
  <style>
    body {{ margin:0; font-family: Inter, Segoe UI, Arial, sans-serif; background:#f5f7fb; color:#17202c; }}
    header {{ background:#101722; color:#f5f7fb; padding:28px 36px; }}
    main {{ padding:28px 36px 48px; max-width:1180px; margin:0 auto; }}
    h1,h2 {{ margin:0 0 12px; }}
    section {{ margin:24px 0; background:#fff; border:1px solid #dce3ee; border-radius:8px; padding:18px; box-shadow:0 8px 26px rgba(16,23,34,.06); }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
    .card {{ background:#f8fafc; border:1px solid #dce3ee; border-radius:6px; padding:12px; }}
    .label {{ font-size:12px; color:#667085; text-transform:uppercase; letter-spacing:.06em; }}
    .value {{ font-size:22px; font-weight:800; margin-top:4px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #e6ebf2; vertical-align:top; }}
    th {{ background:#f2f5f9; color:#344054; }}
    .charts {{ display:grid; grid-template-columns:1fr; gap:18px; }}
    .chart img {{ max-width:100%; height:auto; border:1px solid #e6ebf2; border-radius:6px; background:#fff; }}
    .diagnostic {{ font-size:16px; line-height:1.55; }}
  </style>
</head>
<body>
<header>
  <h1>Kaelis Argos Benchmark Report</h1>
  <div>Tipo de teste: {escape(test_type)} · gerado em {escape(datetime.now().isoformat(timespec="seconds"))}</div>
</header>
<main>
  <section>
    <h2>Cabeçalho</h2>
    {table_from_dict({
        "Início": timing.get("started_at", ""),
        "Fim": timing.get("ended_at", ""),
        "Duração real": f"{timing.get('duration_seconds', 0)}s",
        "Modelo YOLO": config.get("model", ""),
        "Device": config.get("device", ""),
        "imgsz": config.get("imgsz", ""),
        "Câmeras": config.get("cameras", config.get("camera_id", "local")),
        "Intervalo": config.get("interval_seconds", "-"),
        "Workers": config.get("workers", "-"),
    })}
  </section>
  <section>
    <h2>Resumo Executivo</h2>
    <div class="cards">{cards(metrics)}</div>
  </section>
  <section>
    <h2>Diagnóstico</h2>
    <p class="diagnostic">{escape(diagnostic)}</p>
  </section>
  <section>
    <h2>Gráficos</h2>
    <div class="charts">{chart_images(charts)}</div>
  </section>
  <section>
    <h2>Configuração do Teste</h2>
    {table_from_dict(config)}
  </section>
  <section>
    <h2>Métricas Principais</h2>
    {table_from_dict(metrics)}
  </section>
  <section>
    <h2>Estimativas Mensais</h2>
    {table_from_dict(monthly)}
  </section>
  <section>
    <h2>Por Câmera</h2>
    {table_from_rows(summary.get("camera_summary", []))}
  </section>
  <section>
    <h2>Por Classe Detectada</h2>
    {table_from_rows(class_rows)}
  </section>
</main>
</body>
</html>"""


def escape(value: Any) -> str:
    return html.escape(str(value))


def cards(metrics: dict[str, Any]) -> str:
    return "".join(
        f"<div class='card'><div class='label'>{escape(key)}</div><div class='value'>{escape(value)}</div></div>"
        for key, value in metrics.items()
    )


def chart_images(charts: dict[str, str]) -> str:
    if not charts:
        return "<p>Matplotlib não está disponível; gráficos não gerados.</p>"
    return "".join(
        f"<div class='chart'><h3>{escape(key.replace('_', ' ').title())}</h3><img src='data:image/png;base64,{value}'></div>"
        for key, value in charts.items()
    )


def table_from_dict(data: dict[str, Any]) -> str:
    rows = [{"parâmetro": key, "valor": value} for key, value in data.items()]
    return table_from_rows(rows)


def table_from_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>Sem dados.</p>"
    columns = list(rows[0].keys())
    head = "".join(f"<th>{escape(column)}</th>" for column in columns)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{escape(row.get(column, ''))}</td>" for column in columns) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
