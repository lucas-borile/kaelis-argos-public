# Kaelis Argos

> Intelligent camera monitoring with geospatial context, operational alerts, and human review.
>
> Monitoramento inteligente de câmeras com contexto geoespacial, alertas operacionais e revisão humana.

[English](#english) · [Português](#português) · [Documentation](#documentation--documentação)

---

## English

### What is Kaelis Argos?

Kaelis Argos is an experimental video-monitoring platform that turns camera streams into operational information. It captures frames, runs object detection, tracks activity over time, estimates positions on a map, evaluates geofence rules, and gives operators a web interface for reviewing events and incidents.

The project is currently an **MVP for research, demonstrations, and controlled environments**. It is not a certified security, emergency-response, or public-safety system.

### Main capabilities

- Live camera monitoring on an interactive Leaflet map.
- Object detection with Ultralytics YOLO.
- Per-camera temporal tracking and trajectory reconstruction.
- Historical event and frame review with filters.
- Human feedback and dataset-curation workflows.
- Geofences, schedules, severity levels, and alert rules.
- Operational attention queue and incident management.
- Indoor floor plans with cameras, zones, fields of view, and approximate coverage.
- Camera sources through HTTP, RTSP, ONVIF, public players, and supported video pages.
- Roles for administrators, operators, and viewers.
- Camera-level frame access controls and persistent audit logs.
- Optional Telegram alerts.
- PostgreSQL/PostGIS storage with a local SQLite fallback.
- REST API and WebSocket updates through FastAPI.
- Offline/LAN operation for the main interface.

Kaelis Argos does **not** implement facial recognition, biometric identification, license-plate OCR, or automatic nominal identification.

### How it works

```text
Camera or player URL
        │
        ▼
Stream resolution and frame capture
        │
        ▼
YOLO detection ──► visual attributes
        │
        ├────────► temporal tracking
        ├────────► approximate geolocation
        └────────► geofence and alert rules
                         │
                         ▼
              PostgreSQL/PostGIS or SQLite
                         │
                         ▼
              FastAPI REST + WebSocket
                         │
                         ▼
                  Web operator console
```

The backend includes a frame-change gate and a priority scheduler to avoid unnecessary inference and favor cameras with recent activity, relevant geofences, or operator focus.

### Technology stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Pydantic |
| Computer vision | Ultralytics YOLO, OpenCV |
| Database | PostgreSQL/PostGIS or SQLite |
| Frontend | HTML, CSS, JavaScript, Leaflet |
| Streaming | HTTP, RTSP, ONVIF, FFmpeg |
| Runtime | Docker Compose or Python 3.12 |

### Quick start with Docker

Requirements: Docker with Docker Compose, plus enough CPU, memory, and disk space for the selected YOLO model and retained frames.

```bash
cp .env.example .env
docker compose up --build
```

Open `http://127.0.0.1:8080`.

Before exposing the application to any network, replace all development credentials and configure the security-related variables in `.env`.

### Local development

Python 3.12 is recommended.

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -r backend/requirements.txt
```

Start the backend:

```bash
cd backend
../.venv312/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

In another terminal, start the frontend:

```bash
cd frontend
../.venv312/bin/python -m http.server 8080
```

For development tools and tests:

```bash
pip install -r backend/requirements-dev.txt
python -m pytest
```

### Configuration

Copy `.env.example` to `.env` and review at least:

- `ARGOS_ENV` and `ARGOS_USERS_JSON`
- `DATABASE_URL` and `ARGOS_CORS_ORIGINS`
- `ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS`
- `ARGOS_CAMERA_SECRET_KEY`
- `ARGOS_TELEGRAM_SECRET_KEY` and `ARGOS_TELEGRAM_BOT_TOKEN`
- `YOLO_MODEL`, `YOLO_DEVICE`, and `YOLO_IMGSZ`
- frame retention and image-storage settings

Production mode rejects known development passwords and placeholder credentials. The local `.env`, databases, captured frames, logs, model weights, and benchmark results are excluded by `.gitignore`.

### Security and responsible use

Video monitoring can affect privacy and civil rights. Deployers are responsible for obtaining authorization, defining retention periods, limiting access, protecting credentials, documenting lawful purposes, and complying with applicable privacy and surveillance laws.

Recommended minimum controls:

- Run behind HTTPS and a trusted reverse proxy.
- Use strong, unique passwords and external secret management.
- Restrict CORS, network access, and camera permissions.
- Keep private-camera network access disabled unless explicitly required.
- Define short retention periods for images and events.
- Review audit logs and remove sensitive exports before sharing.
- Keep a human responsible for operational decisions.

The repository contains public demonstration camera sources. Availability and terms of third-party streams may change.

---

## Português

### O que é o Kaelis Argos?

Kaelis Argos é uma plataforma experimental de monitoramento por vídeo que transforma transmissões de câmeras em informação operacional. O sistema captura frames, detecta objetos, acompanha atividades ao longo do tempo, estima posições no mapa, avalia regras de geofence e oferece uma interface web para revisão de eventos e ocorrências.

O projeto está no estágio de **MVP para pesquisa, demonstrações e ambientes controlados**. Ele não é um sistema certificado de segurança, resposta a emergências ou segurança pública.

### Principais recursos

- Monitoramento de câmeras em mapa interativo com Leaflet.
- Detecção de objetos com Ultralytics YOLO.
- Tracking temporal por câmera e reconstrução de trajetos.
- Revisão histórica de eventos e frames com filtros.
- Feedback humano e curadoria de datasets.
- Geofences, horários, severidades e regras de alerta.
- Fila operacional de atenção e gestão de ocorrências.
- Plantas internas com câmeras, zonas, campos de visão e cobertura aproximada.
- Fontes HTTP, RTSP, ONVIF, players públicos e páginas de vídeo compatíveis.
- Perfis de administrador, operador e visualizador.
- Controle de acesso aos frames por câmera e auditoria persistente.
- Alertas opcionais pelo Telegram.
- PostgreSQL/PostGIS com fallback local para SQLite.
- API REST e atualizações WebSocket com FastAPI.
- Operação da interface principal em modo offline ou LAN.

O Kaelis Argos **não** implementa reconhecimento facial, identificação biométrica, OCR de placas ou identificação nominal automática.

### Como funciona

```text
URL da câmera ou player
          │
          ▼
Resolução do stream e captura de frames
          │
          ▼
Detecção YOLO ──► atributos visuais
          │
          ├─────► tracking temporal
          ├─────► geolocalização aproximada
          └─────► regras de geofence e alertas
                          │
                          ▼
               PostgreSQL/PostGIS ou SQLite
                          │
                          ▼
                FastAPI REST + WebSocket
                          │
                          ▼
                 Console web operacional
```

O backend possui um filtro de mudança de frames e um agendador de prioridades. Esses componentes reduzem inferências desnecessárias e favorecem câmeras com atividade recente, geofences relevantes ou foco do operador.

### Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | Python, FastAPI, Pydantic |
| Visão computacional | Ultralytics YOLO, OpenCV |
| Banco de dados | PostgreSQL/PostGIS ou SQLite |
| Frontend | HTML, CSS, JavaScript, Leaflet |
| Transmissão | HTTP, RTSP, ONVIF, FFmpeg |
| Execução | Docker Compose ou Python 3.12 |

### Início rápido com Docker

Requisitos: Docker com Docker Compose, além de CPU, memória e espaço em disco suficientes para o modelo YOLO e os frames armazenados.

```bash
cp .env.example .env
docker compose up --build
```

Acesse `http://127.0.0.1:8080`.

Antes de expor a aplicação em qualquer rede, substitua todas as credenciais de desenvolvimento e configure as variáveis de segurança no `.env`.

### Desenvolvimento local

Python 3.12 é recomendado.

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -r backend/requirements.txt
```

Inicie o backend:

```bash
cd backend
../.venv312/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Em outro terminal, inicie o frontend:

```bash
cd frontend
../.venv312/bin/python -m http.server 8080
```

Para ferramentas de desenvolvimento e testes:

```bash
pip install -r backend/requirements-dev.txt
python -m pytest
```

### Configuração

Copie `.env.example` para `.env` e revise pelo menos:

- `ARGOS_ENV` e `ARGOS_USERS_JSON`
- `DATABASE_URL` e `ARGOS_CORS_ORIGINS`
- `ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS`
- `ARGOS_CAMERA_SECRET_KEY`
- `ARGOS_TELEGRAM_SECRET_KEY` e `ARGOS_TELEGRAM_BOT_TOKEN`
- `YOLO_MODEL`, `YOLO_DEVICE` e `YOLO_IMGSZ`
- configurações de retenção e armazenamento de imagens

O modo de produção recusa senhas de desenvolvimento conhecidas e credenciais placeholder. O `.env` local, bancos de dados, frames capturados, logs, pesos de modelos e resultados de benchmark são excluídos pelo `.gitignore`.

### Segurança, privacidade e uso responsável

O monitoramento por vídeo pode afetar a privacidade e direitos civis. Quem implanta o sistema é responsável por obter autorização, definir prazos de retenção, limitar acessos, proteger credenciais, documentar finalidades legítimas e cumprir a legislação aplicável sobre privacidade e vigilância.

Controles mínimos recomendados:

- Executar atrás de HTTPS e de um proxy reverso confiável.
- Usar senhas fortes, exclusivas e gerenciamento externo de segredos.
- Restringir CORS, acesso de rede e permissões das câmeras.
- Manter o acesso a redes privadas de câmeras desativado, salvo necessidade explícita.
- Definir retenção curta para imagens e eventos.
- Revisar logs de auditoria e remover dados sensíveis antes de compartilhar exportações.
- Manter uma pessoa responsável pelas decisões operacionais.

O repositório contém fontes públicas de câmeras demonstrativas. A disponibilidade e os termos dos streams de terceiros podem mudar.

---

## Project structure / Estrutura do projeto

```text
.
├── backend/                  # FastAPI, processing, tracking, storage
├── frontend/                 # Web operator console
├── ml_training/              # Dataset export, evaluation, and training helpers
├── benchmarks/               # Stress and inference benchmark tools
├── tests/                    # Automated tests
├── docs/                     # Technical and operational documentation
├── docker-compose.yml
├── Dockerfile.backend
└── .env.example
```

## Documentation / Documentação

- [Operational guide / Guia operacional](docs/GUIA_OPERACIONAL_KAELIS_ARGOS.html)
- [Camera gateway](docs/camera_gateway.md)
- [Benchmarking](docs/benchmarking.md)
- [Human feedback learning guide](docs/HUMAN_FEEDBACK_LEARNING_GUIDE.md)
- [Operational security backlog](docs/FEATURES_SEGURANCA_OPERACIONAL_BACKLOG.md)
- [System diagnostic](docs/DIAGNOSTICO_SISTEMA_2026-06-28.md)
- [Visual identity patterns](docs/VISUAL_IDENTITY_PATTERNS.md)

## Project status / Estado do projeto

Kaelis Argos is under active MVP development. Interfaces, database schemas, endpoints, and configuration options may change without backward compatibility.

O Kaelis Argos está em desenvolvimento ativo como MVP. Interfaces, schemas do banco, endpoints e opções de configuração podem mudar sem compatibilidade retroativa.

## License / Licença

No open-source license is currently included. Unless a license is added, copyright law reserves all rights to the project owner.

Nenhuma licença open source está incluída atualmente. Enquanto uma licença não for adicionada, a legislação de direitos autorais reserva todos os direitos ao proprietário do projeto.
