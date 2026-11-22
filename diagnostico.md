# Diagnostico tecnico do Kaelis Argos

Data da auditoria: 2026-06-25
Escopo revisado: backend FastAPI, frontend HTML/JS, banco, processamento YOLO/OpenCV, gateway de cameras, plantas indoor, feedback humano, benchmarks, scripts, Docker e documentacao.

## Sumario executivo

O Kaelis Argos e um MVP funcional e relativamente ambicioso: combina cadastro e processamento continuo de cameras, inferencia YOLO, tracking, georreferenciamento, historico de frames/eventos, geofences, chat operacional, feedback humano, exportacao de dataset, plantas baixas e benchmarks. A estrutura central esta clara, mas o sistema ainda esta em nivel de prototipo operacional e nao em nivel de producao segura.

Os maiores riscos encontrados sao:

1. **Credenciais padrao, sessoes, tokens em URL e brute force basico**: seed inseguro, sessoes sem expiracao/revogacao, tokens em URL e rate limit basico de login foram corrigidos; riscos restantes de auth ficam em `localStorage`, MFA, rate limit centralizado e arquitetura cross-site do WebSocket.
2. **CORS e SSRF mitigados, com risco operacional de configuracao**: CORS agora usa allowlist e SSRF bloqueia destinos locais/privados em producao, mas deploys precisam configurar explicitamente origens do frontend e cameras LAN confiaveis.
3. **XSS armazenado/refletido no frontend**: os pontos principais foram mitigados com escape/CSP compativel, mas a CSP ainda depende de `unsafe-inline` ate o frontend deixar de ser monolitico.
4. **Upload sem limite de tamanho**: upload de planta baixa le o arquivo inteiro em memoria e grava no disco antes de validar dimensoes/conteudo.
5. **Health publico expõe estado operacional**: `/health` revela contagem de cameras, degraded cameras, status de worker e backend de banco sem autenticacao.
6. **Ausencia de suite automatizada de testes**: o repositorio nao possui pytest configurado; o unico script `backend/scripts/test_attribute_extractor.py` exige argumentos manuais.
7. **Risco de perda de frames sem deteccoes**: `save_processed_frame` nao persiste frames sem imagem e sem deteccao, apesar do README afirmar historico com ou sem deteccoes.
8. **Monolito grande e acoplado**: `backend/main.py` tem 2903 linhas e concentra API, jobs, processamento, Telegram, benchmarks, uploads, WebSocket e auditoria.

## Evidencias de verificacao

- `python3 -m compileall -q backend ml_training benchmarks scripts`: passou com Python 3.14.5.
- `.venv312/bin/python -m compileall -q backend ml_training benchmarks scripts`: passou com Python 3.12.13.
- `.venv312/bin/python -m pip check`: passou, sem dependencias quebradas.
- `python3 -m pytest -q`: falhou porque `pytest` nao esta instalado.
- `.venv312/bin/python -m pytest -q`: falhou porque `pytest` nao esta instalado.
- `.venv312/bin/python backend/scripts/test_attribute_extractor.py`: falhou por uso incorreto, exige `--image` e `--bbox`.
- Tamanho aproximado: 20.007 linhas auditadas nos principais arquivos.
- Principais arquivos: `backend/main.py` 2903 linhas, `backend/db.py` 2701 linhas, `frontend/index.html` 8421 linhas.

## Stack e arquitetura

### Backend

- **Runtime**: Python 3.11 no Dockerfile, Python 3.12 recomendado no README, FastAPI + Uvicorn.
- **API**: FastAPI em `backend/main.py`, com REST e WebSocket.
- **Processamento de video**: OpenCV, imageio-ffmpeg, subprocess/ffmpeg, captura por polling ou pipe persistente.
- **IA/deteccao**: Ultralytics YOLO, modelos configuraveis por camera, batch opcional, thresholds por perfil.
- **Tracking**: associacao por camera em `backend/tracker.py`.
- **Atributos visuais**: extracao heuristica de cor de roupas/veiculos em `backend/attribute_extractor.py`.
- **Geolocalizacao**: estimativa bbox -> lat/lon em `backend/geoestimator.py`.
- **Geofences**: regras, alertas, prioridade e Telegram em `backend/main.py` + `backend/db.py`.
- **Planta baixa indoor**: CRUD, upload estatico e cobertura aproximada em `backend/floor_plan_coverage.py`.
- **Gateway IP/ONVIF**: descoberta ONVIF, perfis e RTSP em `backend/camera_gateway.py`.
- **Banco**: PostgreSQL/PostGIS quando `DATABASE_URL` existe; fallback SQLite (`spmu.db`).
- **Persistencia de imagens**: por padrao em arquivo (`data/frames`) com opcao de blob no banco.
- **Auditoria**: tabela `audit_logs`, logs de login, buscas, frames, cameras, usuarios e feedback.

### Frontend

- **Aplicacao**: arquivo unico `frontend/index.html`.
- **Mapa**: Leaflet local em `frontend/vendor/leaflet`.
- **Estado/auth**: token ainda salvo em `localStorage` para REST legado, com cookie HttpOnly `argos_session` para WebSocket.
- **Comunicacao**: `fetch` REST com Bearer token/cookie e WebSocket `/ws/detections` autenticado por cookie.
- **UI**: dashboard operacional, cameras, mapa, geofences, chat, plantas, feedback, revisao, auditoria e benchmarks.

### Infra

- **Dockerfile.backend**: `python:3.11-slim`, instala libs OpenCV e dependencias Python.
- **docker-compose.yml**: PostGIS 16-3.4, backend em `8000`, frontend nginx em `8080`.
- **Credenciais compose**: banco `argos/argos` hardcoded.
- **Configuracao**: `.env.example` possui defaults operacionais e usuarios seed.

## Fluxo principal do sistema

1. Startup FastAPI chama `init_db()`, carrega cameras persistidas e inicia loop de processamento.
2. `CameraCaptureManager` cria workers de captura para cameras ativas.
3. Cada camera resolve URL/player via `stream_resolver.resolve_player`.
4. Captura frame por ffmpeg/OpenCV, ou por pipe persistente.
5. `FrameChangeGate` decide se vale rodar YOLO.
6. `PriorityInferenceScheduler` ranqueia cameras por prioridade, foco, geofence, atividade, falhas e mudanca visual.
7. `frame_processor` executa YOLO individual ou batch.
8. `geoestimator` estima posicao; `tracker` atribui track_id; `attribute_extractor` adiciona cores.
9. `save_processed_frame` persiste frame/eventos.
10. `publish_processed_frame` atualiza cache, geofences, Telegram e WebSocket.
11. Frontend renderiza mapa, timeline, revisão e feedback.

## Endpoints

### Publico

- `GET /health`: liveness publico minimo (`{"status":"ok"}`).
- `POST /auth/login`: login.

### Usuario autenticado

- `GET /system/status`: CPU, memoria, GPU, storage, processamento.
- `GET /auth/me`: usuario atual.
- `GET /benchmarks/stress/jobs`, `GET /benchmarks/stress/jobs/{job_id}`.
- `GET /integrations/telegram`: status/config mascarada.
- `GET /cameras`, `GET /api/cameras`, `GET /api/cameras/{camera_id}`.
- `GET /api/cameras/{camera_id}/health`.
- `GET /detections`, `/detections/stats`, `/detections/heatmap`.
- `GET /events`, `/events/range`, `/events/{event_id}`.
- `GET /review/events`, `/review/next`, `/review/events/{event_id}`.
- `GET /events/{event_id}/feedback`.
- `GET /geofences`, `/geofence-rules`, `/geofence-alerts`.
- `GET /api/floor-plans`, `/api/floor-plans/{id}`, cameras, zonas e cobertura.
- `GET /frames/{frame_id}`, `/frames/{frame_id}/metadata`, `/frames/{frame_id}/image`.
- `GET /processed-frames/latest`.
- `GET /cameras/{camera_id}/frames`, `/cameras/{camera_id}/processed-frame`.
- `POST /cameras/{camera_id}/focus`.
- `WS /ws/detections`.

### Operador

- Benchmarks: `POST /benchmarks/stress`.
- Telegram: `PUT /integrations/telegram`, `POST /integrations/telegram/test`.
- Chat: `GET /chat/messages`, `GET /chat/mentions`, `POST /chat/messages`.
- Cameras: `POST /cameras`, `PATCH /cameras/{id}`, `DELETE /cameras/{id}`.
- IP cameras: create/update/delete/test/discovery/profiles/snapshot.
- Calibracao: `POST /cameras/{id}/calibrate-distance`.
- Revisao/feedback: `POST /review/events/{id}/mark`, `POST /events/{id}/feedback`, `GET /feedback`, `GET /feedback/stats`, `PATCH /feedback/{id}`.
- Geofences/regras: criar, editar, excluir.
- Floor plans/cameras/zones/upload: criar, editar, excluir e upload.
- Snapshot/processamento: `POST /cameras/{id}/snapshot`, `POST /processing/scan`.

### Admin

- Usuarios: `GET/POST/PATCH/DELETE /users`.
- Auditoria: `GET /audit-logs`.
- Dataset: `POST /feedback/export-dataset`.
- Campos avancados de inferencia por camera so sao preservados para admin.

## Modulos e funcoes principais

### `backend/auth.py`

- `authenticate`: valida usuario ativo e senha PBKDF2.
- `create_session`: cria token aleatorio, persiste hash em `app_sessions` e define expiracao.
- `get_session`: valida token persistido, TTL, revogacao e usuario ativo.
- `current_user`, `current_operator`, `current_admin`: dependencias de autorizacao por role.
- `websocket_user`: autentica WebSocket por query param `token`.
- `parse_bearer_token`: extrai Bearer token.

Problemas:
- WebSocket usa token na URL.
- Token segue armazenado em `localStorage` no frontend.

### `backend/db.py`

- Cria schema SQLite/PostgreSQL.
- Gerencia usuarios, cameras, floor plans, geofences, regras, alertas, auditoria, chat, frames, eventos e feedback.
- Usa parametros SQL na maior parte das consultas.
- Faz fallback SQLite se `DATABASE_URL` nao existe.

Problemas:
- Usuarios seed com senha fraca por default.
- `purge_frames_without_detections` existe mas nao e chamado.
- Varias queries montam `SET`/`IN` dinamicamente, hoje com campos controlados por allowlist; manter assim.
- `PostgresConnection.executescript` faz split simples por `;`, fragil para SQL mais complexo.
- `save_processed_frame` retorna `None` quando nao ha imagem nem deteccao, quebrando a promessa de historico completo.

### `backend/main.py`

- Ponto central da aplicacao: app FastAPI, middlewares, endpoints, loop de processamento, retencao, benchmark, Telegram, geofence, uploads, auditoria, WebSocket.

Problemas:
- Arquivo excessivamente grande e acoplado.
- CORS aberto.
- Health publico.
- Mistura IO bloqueante, jobs CPU/GPU, persistencia e HTTP no mesmo processo.
- Rate limit de login era ausente; foi mitigado no item 9 com limite local em memoria.
- Benchmark executa subprocesso a partir de payload autenticado, com validacao parcial.

### `backend/frame_processor.py`

- Resolve player, captura frame, executa YOLO, extrai atributos, tracking e codifica imagem.
- Usa subprocess `ffmpeg` com lista de argumentos.

Problemas:
- URLs arbitrarias permitem SSRF.
- `fetch_poster` baixa conteudo sem limite de tamanho.
- Lock global `_model_lock` cobre toda predicao YOLO, reduzindo paralelismo.
- Modelo YOLO por camera pode ser caminho arbitrario para admin; isso pode induzir downloads/carregamentos inesperados.

### `backend/capture_manager.py`

- Mantem worker por camera, captura por polling ou pipe persistente.
- Usa buffer maximo de 8 MB para pipe.

Problemas:
- Um worker por camera pode escalar mal.
- Sem limite global explicito de cameras ativas.
- `stderr=PIPE` em ffmpeg persistente pode bloquear se stderr crescer, embora loglevel esteja `error`.

### `backend/camera_gateway.py`

- ONVIF discovery, perfis, RTSP health e snapshot.

Problemas:
- Operadores conseguem acionar conexoes internas arbitrarias.
- Descoberta ONVIF permite mapear rede local a partir do servidor.
- Senha de camera so e criptografada se `ARGOS_CAMERA_SECRET_KEY` existir; se vazio, senha e descartada silenciosamente.

### `backend/stream_resolver.py`

- Resolve players HTML, YouTube/yt-dlp, webcamera.pl, player.im/yayin.

Problemas:
- `requests.get(player_url)` sem allowlist ou bloqueio de IP privado.
- Cache LRU pode manter URLs assinadas expiradas.
- `resolve_player_im` testa multiplas URLs externas por camera.

### `backend/attribute_extractor.py`

- Heuristicas de cor por crop, qualidade, historico por track, debug opcional.

Problemas:
- Estado de historico em memoria nao e persistido.
- Debug de imagem pode gerar arquivos sensiveis se habilitado.

### `backend/tracker.py`

- Tracking por IoU, distancia, tamanho e assinatura visual.

Problemas:
- Estado em memoria por processo; reinicio perde identidade.
- Nao ha reconciliacao multi-camera.

### `backend/floor_plan_coverage.py`

- Calcula cone de camera, cobertura aproximada e colisao 2D com obstaculos.

Problemas:
- Amostragem de cobertura pode ficar custosa em plantas grandes.
- Modelo e aproximado; deve ser rotulado como estimativa.

### `frontend/index.html`

- Toda UI em arquivo unico: auth, mapa, cameras, geofence, review, feedback, chat, plantas, benchmark, auditoria.

Problemas:
- XSS: muitos `innerHTML` interpolam `name`, `description`, `camera_name`, `rule_name`, `geofence_name`, `user.name`, etc. sem `escapeHtml`.
- Token em `localStorage`, vulneravel a exfiltracao via XSS.
- Token em URL para frame image e WebSocket foi corrigido no item 6; ainda resta risco por token em `localStorage`.
- Arquivo muito grande, dificil de testar e revisar.

### `ml_training`

- Scripts iniciais/scaffolds: exportacao de dataset e calibracao.

Problemas:
- `train_candidate.py` e `evaluate_candidate.py` parecem placeholders.
- Pipeline de ML ainda nao fecha treino/avaliacao real.

### `benchmarks`

- Stress client, runner YOLO, relatorios HTML/PNG.

Problemas:
- Relatorios gerados dentro do repo podem poluir versionamento.
- Benchmark via API roda processo local e pode consumir CPU/GPU por longos periodos.

## Falhas e vulnerabilidades encontradas

### Criticas

#### 1. Credenciais padrao em producao

Status: **corrigido em 2026-06-26**.

Evidencia:
- Antes da correcao, `backend/db.py` definia usuarios padrao com `admin/admin123`, `operator/operator123`, `viewer/viewer123`.
- Antes da correcao, `.env.example` repetia `ARGOS_USERS_JSON` com as mesmas senhas.

Impacto:
- Comprometimento imediato se deploy for exposto sem troca de senha.

Correcao:
- `backend/db.py` agora trata esses usuarios como `DEVELOPMENT_SEED_USERS`, usados apenas como fallback local quando `ARGOS_ENV`/`ENVIRONMENT` nao indica producao e `ARGOS_USERS_JSON` nao foi definido.
- Em `ARGOS_ENV=production` ou `ARGOS_ENV=prod`, o backend agora recusa iniciar sem `ARGOS_USERS_JSON`.
- Em producao, o backend tambem recusa iniciar se `ARGOS_USERS_JSON` usar as senhas padrao de desenvolvimento ou os placeholders publicos do `.env.example`.
- `.env.example` e `README.md` foram atualizados para usar placeholders de senha e documentar a exigencia de senhas fortes em producao.
- Testes executados: validacao manual de `load_seed_users` para desenvolvimento sem env, producao sem `ARGOS_USERS_JSON`, producao com senha padrao, producao com placeholder publico e producao com senha customizada; guarda de `init_db` em producao; `compileall`; `pip check`.

#### 2. Sessoes em memoria sem expiracao

Status: **corrigido em 2026-06-25**.

Evidencia:
- Antes da correcao, `backend/auth.py` usava `_sessions: dict`.
- Antes da correcao, `create_session` nao definia expiracao.
- Antes da correcao, `get_session` nao validava TTL, usuario ainda ativo ou revogacao.

Impacto:
- Token roubado vale indefinidamente ate restart.
- Desativar/deletar usuario nao invalida sessoes existentes.
- Multi-worker quebra login porque sessoes nao sao compartilhadas.

Correcao:
- Foi criada a tabela `app_sessions` no schema SQLite/PostgreSQL, armazenando apenas hash SHA-256 do token, `username`, perfil, `created_at`, `expires_at`, `last_seen_at` e `revoked_at`.
- `backend/auth.py` agora usa sessoes persistidas no banco, com TTL configuravel por `ARGOS_SESSION_TTL_SECONDS` e minimo de 300 segundos.
- `get_session` valida expiracao, revogacao e se o usuario continua ativo; tokens expirados, revogados ou de usuarios desativados deixam de autenticar.
- Ao emitir uma nova sessao para o mesmo usuario, sessoes anteriores sao revogadas, reduzindo janela de reutilizacao de tokens antigos.
- Foi adicionado `POST /auth/logout`, que revoga a sessao server-side e audita a acao.
- O frontend agora chama `/auth/logout` antes de limpar `localStorage` e recarregar.
- `LoginResponse` e `/auth/me` passaram a incluir `expires_at`.
- `.env.example` e `README.md` documentam `ARGOS_SESSION_TTL_SECONDS`.
- Testes executados: criacao/consulta/revogacao de sessao em SQLite temporario; expiracao por banco; invalidacao apos desativar usuario; rotacao ao emitir nova sessao para o mesmo usuario; `compileall`; `pip check`.

#### 3. SSRF/scan de rede via cameras e resolvers

Status: **corrigido em 2026-06-25**.

Evidencia:
- Antes da correcao, `POST /api/cameras/test-rtsp` chamava `test_rtsp_connection(payload.rtsp_url)` sem validacao de destino.
- Antes da correcao, `POST /api/cameras` e update resolviam ONVIF/RTSP sem validar host/rede.
- Antes da correcao, `stream_resolver.resolve_player_cached` fazia `requests.get(player_url)` sem bloquear destinos internos.
- Antes da correcao, `frame_processor.fetch_poster` baixava `poster_url` sem validar destino.

Impacto:
- Operador pode fazer servidor conectar em `127.0.0.1`, metadata cloud, rede privada, cameras internas ou portas arbitrarias.
- Pode virar scanner interno e bypass de segmentacao de rede.

Correcao:
- Foi criado `backend/network_security.py` com validacao central de URL/host, resolucao DNS e bloqueio de loopback, link-local, multicast, unspecified e metadata `169.254.169.254`.
- Em producao (`ARGOS_ENV=production`/`prod`), redes privadas tambem sao bloqueadas por padrao; `ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS=true` habilita acesso LAN quando necessario.
- `backend/stream_resolver.py` agora valida `player_url`, streams diretos, playlists, URLs derivadas de HTML e posters/streams do YouTube antes de `requests.get` ou retorno ao pipeline.
- `backend/frame_processor.py` agora valida URL de captura ffmpeg e poster antes de abrir rede.
- `backend/camera_gateway.py` agora valida RTSP e ONVIF host antes de `cv2.VideoCapture`/ONVIF.
- `backend/main.py` valida `rtsp_url_manual`, host ONVIF e URI retornada pelo ONVIF antes de persistir/usar.
- `.env.example` e `README.md` documentam `ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS`.
- Testes executados: validacao de URLs publicas, bloqueio de loopback, bloqueio de metadata, bloqueio de private range em producao, permissao de private range em desenvolvimento/opt-in, erro de `test_rtsp_connection` sem abrir OpenCV, `resolve_player` bloqueando loopback, `compileall`, `pip check`.

#### 4. XSS armazenado no frontend

Status: **corrigido parcialmente/mitigado em 2026-06-26**.

Evidencia:
- Antes da correcao, `frontend/index.html` continha dezenas de `innerHTML`.
- Antes da correcao, havia exemplos sem escape uniforme: plantas (`plan.name`, `plan.description`), geofences (`geofence.name`, `description`), regras (`rule.name`), alertas (`alert.rule_name`, `geofence_name`), cameras (`camera.name`), usuarios (`user.name`), auditoria, popups Leaflet e cards de revisao.

Impacto:
- Um operador pode cadastrar nome/descricao com HTML/script e roubar tokens de outros usuarios.
- Como tokens ficam em `localStorage`, XSS vira tomada de conta.

Correcao:
- `frontend/index.html` passou a escapar aspas simples em `escapeHtml`, reduzindo risco tambem em atributos HTML.
- Dados cadastraveis/interpolados em eventos, plantas, cameras indoor, geofences, regras, alertas, usuarios, auditoria, cards de revisao, benchmark e popups/tooltips Leaflet foram migrados para `escapeHtml` ou APIs seguras (`textContent`, `Option`, handlers via `onclick` atribuido em DOM criado).
- Cores vindas do backend agora passam por `sanitizeCssColor` antes de entrar em atributos `style`.
- O botao do popup de camera deixou de usar `onclick` inline e passou a registrar handler no evento `popupopen`.
- O `onerror` inline gerado em imagens da revisao foi substituido por `image.onerror` em elemento criado via DOM.
- Foi adicionada CSP em meta tag bloqueando `object-src`, `frame-src` e restringindo origens de imagem/conexao. Como o frontend ainda e um HTML monolitico com scripts/estilos inline, a CSP ainda precisa de `unsafe-inline`; ver item 33.
- Testes executados: parse HTML, `node --check` nos dois scripts inline extraidos e buscas por handlers inline/interpolacoes remanescentes.

### Altas

#### 5. CORS aberto

Status: **corrigido em 2026-06-26**.

Evidencia:
- Antes da correcao, `backend/main.py` usava `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`.

Impacto:
- Qualquer site pode chamar a API do browser do usuario. Com Bearer token manual isso depende de acesso ao token, mas combinado com XSS ou extensoes maliciosas aumenta o dano.

Correcao:
- Foi criado `backend/cors_config.py` para parsear `ARGOS_CORS_ORIGINS` como lista separada por virgula ou JSON array.
- Em desenvolvimento, quando a variavel esta vazia, o backend permite apenas origens locais comuns (`localhost`/`127.0.0.1` em 8080, 5173 e 3000).
- Em producao (`ARGOS_ENV=production`/`prod`), ausencia da variavel resulta em allowlist vazia/same-origin e `*` e rejeitado com erro de inicializacao.
- `backend/main.py` passou a usar a allowlist e metodos/headers explicitos em vez de `*`.
- `.env.example` e `README.md` documentam `ARGOS_CORS_ORIGINS`.
- Testes executados: parse de lista por virgula, parse de JSON array, rejeicao de wildcard em producao, rejeicao de origem invalida, defaults dev/prod, preflight CORS direto via ASGI, `compileall`, `pip check`.

#### 6. Token em URL

Status: **corrigido/mitigado em 2026-06-26**.

Evidencia:
- Antes da correcao, `frontend/index.html` usava `/ws/detections?token=...`.
- Antes da correcao, `reviewFrameImageUrl` montava `/frames/{id}/image?token=...`.
- Antes da correcao, backend aceitava `token` query em `/frames/{frame_id}/image`.

Impacto:
- Token pode aparecer em logs HTTP, historico do navegador, proxies, relatorios, screenshots e referer.

Correcao:
- `POST /auth/login` agora define cookie HttpOnly `argos_session`, com `SameSite=Lax`, `Secure` em producao e TTL alinhado a `ARGOS_SESSION_TTL_SECONDS`.
- `POST /auth/logout` revoga tokens recebidos por cookie/header e remove o cookie de sessao.
- `current_user` e `/frames/{frame_id}/image` autenticam por cookie ou bearer header; o endpoint de imagem nao aceita mais `?token=`.
- `websocket_user` autentica apenas pelo cookie HttpOnly; suporte a token query foi removido.
- `frontend/index.html` abre `/ws/detections` sem query string.
- Thumbnails/imagens historicas protegidas agora sao buscadas com `fetch` autenticado (`Authorization` + `credentials: include`) e renderizadas por object URL/blob, sem token em URL.
- `README.md` documenta o novo comportamento.
- Testes executados: busca por `?token=`/`token=` em rotas de frame/WS, validacao de cookie/header em funcoes de auth, remocao de query token em `/frames/{frame_id}/image`, `compileall`, `pip check`.

#### 7. Upload de planta baixa sem limite de tamanho

Status: **corrigido/mitigado em 2026-06-26**.

Evidencia:
- Antes da correcao, `floor_plan_upload` fazia `data = await file.read()` e `target_path.write_bytes(data)`.
- Antes da correcao, validava extensao/content-type antes, mas validava imagem so depois de gravar.

Impacto:
- DoS de memoria/disco.
- Imagens gigantes podem causar custo alto no `cv2.imread`.

Correcao:
- `backend/main.py` agora define `FLOOR_PLAN_UPLOAD_MAX_BYTES`, `FLOOR_PLAN_MAX_PIXELS` e `FLOOR_PLAN_MAX_SIDE_PX`.
- O endpoint rejeita rapidamente `Content-Length` claramente acima do limite e tambem conta bytes durante a leitura em chunks de 1 MiB.
- O upload vazio retorna 400 e upload acima do limite retorna 413.
- A validacao passou a usar magic bytes PNG/JPEG antes de qualquer gravacao final.
- A imagem e decodificada com `cv2.imdecode` em memoria limitada e dimensoes/area sao rejeitadas antes de gravar em `static`.
- A escrita final usa arquivo temporario e `replace`, evitando arquivo final parcialmente escrito.
- `.env.example` e `README.md` documentam os limites.
- Testes executados: upload vazio, magic bytes invalido, limite de bytes, mismatch content-type/dados, dimensoes acima do limite, PNG valido, escrita temporaria, `compileall`, `pip check`.

#### 8. Health publico revela informacao sensivel

Status: **corrigido em 2026-06-26**.

Evidencia:
- Antes da correcao, `GET /health` nao exigia auth e retornava cameras, degraded cameras, backend DB, status de workers e scheduler.

Impacto:
- Facilita fingerprinting e inteligencia operacional.

Correcao:
- `GET /health` agora retorna apenas `{"status": "ok"}`.
- Os detalhes operacionais permanecem em `GET /system/status`, que ja exige usuario autenticado.
- Foi corrigido tambem o acesso a `camera["active"]` no status detalhado para `camera.get("active", True)`, evitando erro com registros antigos.
- `README.md` e a lista de endpoints deste diagnostico foram atualizados.
- Testes executados: chamada direta de `health`, chamada direta de `system_status` com camera sem chave `active`, verificacao de que `system_status` continua protegido por `Depends(current_user)`, `compileall`, `pip check`.

#### 9. Falta rate limit e protecao contra brute force

Status: **corrigido/mitigado em 2026-06-26**.

Evidencia:
- Antes da correcao, `/auth/login` nao tinha rate limit, lockout ou delay incremental.

Impacto:
- Brute force contra senhas padrao/fracas.

Correcao:
- `backend/main.py` agora aplica rate limit em memoria antes de autenticar.
- O limite combina chave por IP e par IP+usuario, reduzindo brute force sem bloquear globalmente um usuario por ataques vindos de outro IP.
- Falhas de login registram tentativa e, ao exceder limite, novas tentativas recebem HTTP 429 com `Retry-After`.
- Login valido limpa as tentativas daquele IP/par usuario.
- Eventos `auth.login_rate_limited` sao auditados.
- As variaveis `LOGIN_RATE_LIMIT_MAX_ATTEMPTS`, `LOGIN_RATE_LIMIT_IP_MAX_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_S`, `LOGIN_RATE_LIMIT_LOCKOUT_S` e `LOGIN_RATE_LIMIT_MAX_KEYS` foram adicionadas.
- `request_ip` passou a ignorar `X-Forwarded-For` por padrao; `ARGOS_TRUST_PROXY_HEADERS=true` habilita uso desse header apenas atras de proxy confiavel.
- `.env.example` e `README.md` documentam os controles.
- Testes executados: bloqueio por par IP+usuario, bloqueio por IP, limpeza apos login valido, `Retry-After`, `ARGOS_TRUST_PROXY_HEADERS`, `compileall`, `pip check`.

Risco residual:
- MFA para admin permanece recomendado, mas nao foi implementado neste item.

#### 10. Segredos de Telegram no banco em claro

Status: **corrigido/mitigado em 2026-06-26**.

Evidencia:
- Antes da correcao, `save_telegram_config` persistia `bot_token` diretamente.
- `get_telegram_config(mask_token=False)` recuperava token para envio.

Impacto:
- Vazamento do banco compromete bot Telegram.

Correcao:
- `backend/db.py` agora suporta `ARGOS_TELEGRAM_SECRET_KEY` para criptografar novos tokens de bot com Fernet antes de persistir no banco.
- `ARGOS_TELEGRAM_BOT_TOKEN` pode fornecer o token por ambiente/secret manager sem gravar o segredo no banco.
- Sem `ARGOS_TELEGRAM_SECRET_KEY`, o backend rejeita novos tokens enviados pela UI, evitando nova persistencia em claro.
- `get_telegram_config(mask_token=True)` continua mascarando token; `mask_token=False` retorna o token revelado apenas para envio interno.
- Configuracoes antigas em claro ainda podem ser lidas para compatibilidade/migracao, mas novos saves com token usam criptografia.
- `PUT /integrations/telegram` agora retorna erro 400 explicito se tentar salvar token sem chave externa.
- `.env.example` e `README.md` documentam `ARGOS_TELEGRAM_SECRET_KEY` e `ARGOS_TELEGRAM_BOT_TOKEN`.
- Testes executados: criptografia/decriptografia com Fernet, rejeicao sem chave, token via ambiente, preservacao de token armazenado ao atualizar outros campos, leitura de legado em claro, `compileall`, `pip check`.

Risco residual:
- Tokens ja persistidos em claro antes da correcao continuam no banco ate serem rotacionados ou salvos novamente com `ARGOS_TELEGRAM_SECRET_KEY`.

### Medias

#### 11. Senha de camera descartada silenciosamente sem chave

Evidencia:
- `protect_camera_password` retorna vazio se `ARGOS_CAMERA_SECRET_KEY` nao existe.

Impacto:
- Camera criada com credenciais pode funcionar inicialmente, mas futuras leituras de perfil/update falham.

Correcao:
- Se senha for enviada e nao ha chave, retornar erro 400 com instrucao.

#### 12. `save_processed_frame` nao grava frame sem imagem e sem deteccao

Evidencia:
- `if not image and not payload.get("detections"): return None`.

Impacto:
- Contradiz README sobre persistir frames com ou sem deteccoes.
- Dificulta auditoria de periodos sem deteccao.

Correcao:
- Persistir metadados de frame vazio, talvez sem imagem dependendo da politica de storage.

#### 13. `processed_frame(refresh=True)` nao persiste frame manual

Evidencia:
- `load_camera_processed_frame` retorna `process_camera(... include_image=True)` direto em refresh.
- `process_camera` persiste, mas se frame sem imagem/deteccao retorna `frame_id=None`.

Impacto:
- Refresh de camera sem deteccao pode nao entrar no historico.

Correcao:
- Alinhar politica de persistencia de frames vazios.

#### 14. Monolito operacional em um unico processo

Evidencia:
- `backend/main.py` concentra API, captura, inferencia, jobs, Telegram e benchmark.

Impacto:
- Uma inferencia lenta, benchmark ou captura travada prejudica API.
- Escalar Uvicorn com multiplos workers duplicaria loops de processamento.

Correcao:
- Separar API, worker de captura/inferencia, jobs/benchmark e notificacoes.
- Usar fila (Redis/RQ/Celery/Arq) ou outro coordenador.

#### 15. Lock global no YOLO reduz paralelismo

Evidencia:
- `frame_processor.run_yolo_batch` envolve `model.predict` em `_model_lock`.

Impacto:
- Mesmo com batch/concurrency, predicao fica serializada por processo.

Correcao:
- Lock apenas para carregamento/warmup do modelo.
- Controlar concorrencia por device/model com semaforos.

#### 16. Possivel path traversal logico em imagens de frames

Evidencia:
- `get_frame_image_blob` resolve `FRAME_IMAGE_STORAGE_DIR / row["image_path"]`, mas nao verifica se o resultado fica dentro de `FRAME_IMAGE_STORAGE_DIR`.

Impacto:
- Hoje `image_path` e gerado internamente, mas se banco for adulterado, `FileResponse` pode servir arquivo fora da pasta.

Correcao:
- Verificar `path.is_relative_to(FRAME_IMAGE_STORAGE_DIR.resolve())`.

#### 17. Benchmarks via API podem consumir recursos excessivos

Evidencia:
- `POST /benchmarks/stress` permite ate 12 cenarios, duracao ate 1440 min, workers ate 64.

Impacto:
- Operador pode ocupar CPU/GPU/disco por muito tempo.

Correcao:
- Restringir a admin.
- Limites menores por ambiente.
- Fila com cancelamento e quota.

#### 18. Relatorios/artefatos estaticos expostos

Evidencia:
- `app.mount("/benchmark-artifacts", StaticFiles(directory=BENCHMARKS_DIR))`.

Impacto:
- Qualquer pessoa que conheca URL acessa artefatos se backend exposto; mount nao exige auth.

Correcao:
- Servir artefatos por endpoint autenticado.
- Separar diretorio publico de resultados privados.

#### 19. Falta politica clara de privacidade para frames

Evidencia:
- `frame_access` restringe visualizacao de frames, mas eventos/deteccoes seguem visiveis.
- Export dataset inclui caminhos de imagem.

Impacto:
- Metadados e localizacao podem continuar sensiveis.

Correcao:
- Definir escopos: evento, frame, imagem, dataset e auditoria.
- Aplicar `frame_access` ou politica equivalente em eventos associados.

#### 20. Auditoria depende de header `x-forwarded-for` sem trusted proxy

Evidencia:
- `request_ip` usa primeiro valor de `x-forwarded-for` diretamente.

Impacto:
- Cliente pode falsificar IP em logs se nao houver proxy confiavel limpando headers.

Correcao:
- Aceitar forwarded headers apenas de proxies confiaveis.

### Baixas e melhorias tecnicas

#### 21. Placeholders de ML

Evidencia:
- `ml_training/train_candidate.py` e `evaluate_candidate.py` possuem `main()` vazio/placeholder.

Impacto:
- Documenta capacidade ainda nao entregue.

Correcao:
- Implementar pipeline real ou marcar explicitamente como futuro.

#### 22. Arquivos HTML de benchmark versionados

Evidencia:
- `benchmarks/reports/*.html` estao no repo.

Impacto:
- Ruido em diffs, arquivos grandes e dados operacionais versionados.

Correcao:
- Adicionar `benchmarks/results/` e `benchmarks/reports/` gerados ao `.gitignore`, mantendo templates/scripts.

#### 23. Dependencias sem pin exato

Evidencia:
- `requirements.txt` usa `>=` para muitas libs.

Impacto:
- Builds nao reprodutiveis, regressao por atualizacao upstream.

Correcao:
- Usar lockfile (`pip-tools`, `uv lock` ou Poetry) para deploy.

#### 24. Docker roda como root

Evidencia:
- Dockerfile nao cria usuario nao-root.

Impacto:
- Aumenta impacto de RCE ou escape parcial.

Correcao:
- Criar usuario app e ajustar permissoes.

#### 25. Banco Postgres exposto no host

Evidencia:
- compose publica `5432:5432`.

Impacto:
- Banco fica acessivel local/rede dependendo ambiente Docker.

Correcao:
- Remover port mapping por padrao ou limitar a `127.0.0.1`.

#### 26. Nomes de cameras/geofences sem limites consistentes

Evidencia:
- `CameraCreate.name` nao tem `max_length`; `GeoFenceCreate.name` tambem nao.

Impacto:
- Dados gigantes podem degradar UI/logs.

Correcao:
- Adicionar limites Pydantic em todos os campos textuais.

#### 27. `public_frame_payload` pode expor metadados de fonte

Evidencia:
- Remove imagem e scrubba algumas URLs, mas ainda devolve dados de source.

Impacto:
- Pode vazar estrutura de streams/titulos/IDs.

Correcao:
- Definir DTO publico por role.

#### 28. Tratamento de erros silencioso

Evidencia:
- Varias capturas retornam `None` ou `""` sem log estruturado.

Impacto:
- Dificulta observabilidade.

Correcao:
- Padronizar erro com codigo, origem, duracao e camera_id.

#### 29. Retencao nao cobre todos os artefatos

Evidencia:
- Retencao remove frames antigos e orfaos de `data/frames`, mas nao benchmark reports/datasets/debug attributes.

Impacto:
- Crescimento de disco.

Correcao:
- Politicas separadas para datasets, logs, reports e debug.

#### 30. Sem migracoes versionadas

Evidencia:
- Schema criado/alterado manualmente em `init_db`.

Impacto:
- Evolucao de banco arriscada.

Correcao:
- Alembic para Postgres e estrategia clara para SQLite.

#### 31. `ARGOS_SESSION_TTL_SECONDS` invalido podia quebrar import do auth

Status: **corrigido em 2026-06-25**.

Evidencia:
- Durante a correcao do item 2, foi adicionada leitura de `ARGOS_SESSION_TTL_SECONDS` em `backend/auth.py`.
- Se o valor viesse invalido, `int(...)` poderia levantar `ValueError` no import do modulo.

Impacto:
- Uma variavel de ambiente mal preenchida impediria o backend de iniciar.

Correcao:
- `backend/auth.py` agora aplica fallback para `28800` segundos quando `ARGOS_SESSION_TTL_SECONDS` nao e inteiro valido, mantendo minimo de 300 segundos para valores validos baixos.

#### 32. Bloqueio SSRF pode impedir cameras LAN em producao sem opt-in

Status: **mitigado/documentado em 2026-06-26**.

Evidencia:
- A correcao do item 3 bloqueia redes privadas por padrao quando `ARGOS_ENV=production`/`prod`.
- O projeto tambem suporta cameras LAN/ESP32CAM, que normalmente usam IPs privados.

Impacto:
- Um deploy de producao que dependa de cameras em `192.168.0.0/16`, `10.0.0.0/8` ou `172.16.0.0/12` pode falhar ate configurar explicitamente a permissao.

Correcao:
- Foi adicionada a variavel `ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS`.
- O default e permissivo em desenvolvimento e restritivo em producao.
- `.env.example` e `README.md` documentam que `ARGOS_ALLOW_PRIVATE_CAMERA_NETWORKS=true` deve ser usado apenas quando o servidor precisar acessar cameras LAN confiaveis.

#### 33. CSP ainda depende de `unsafe-inline` por frontend monolitico

Status: **registrado em 2026-06-26**.

Evidencia:
- A mitigacao do item 4 adicionou CSP em `frontend/index.html`, mas a aplicacao ainda concentra CSS e JavaScript inline no mesmo arquivo.
- Remover `unsafe-inline` agora quebraria a inicializacao do frontend sem uma refatoracao para arquivos externos, hashes ou nonces.

Impacto:
- A CSP reduz algumas classes de abuso (`object-src`, `frame-src`, `base-uri`, origens), mas ainda nao funciona como uma barreira forte contra todo XSS caso algum novo `innerHTML` vulneravel seja introduzido.

Correcao proposta:
- Extrair CSS e JavaScript inline de `frontend/index.html` para arquivos estaticos versionados.
- Remover handlers inline remanescentes de compatibilidade e qualquer `document.write`.
- Alterar a CSP para `script-src 'self'` e `style-src 'self'` ou usar nonces/hashes gerados pelo backend.

#### 34. Frontend separado pode perder acesso CORS em producao sem allowlist

Status: **mitigado/documentado em 2026-06-26**.

Evidencia:
- A correcao do item 5 remove o wildcard e, em producao sem `ARGOS_CORS_ORIGINS`, deixa CORS sem origens cross-origin.
- Deploys que servem frontend e backend em dominios/portas diferentes dependem de CORS para chamadas do navegador.

Impacto:
- Um deploy de producao com frontend separado pode parecer "offline" no navegador ate configurar a origem correta.

Correcao:
- `.env.example` e `README.md` documentam `ARGOS_CORS_ORIGINS`.
- O parser aceita formato simples separado por virgula e JSON array, facilitando multiplas origens controladas.

#### 35. `fastapi.testclient` indisponivel por dependencia ausente

Status: **registrado em 2026-06-26**.

Evidencia:
- Ao testar a correcao do item 5, importar `fastapi.testclient.TestClient` falhou com `RuntimeError: The starlette.testclient module requires the httpx2 package to be installed`.
- O teste de CORS precisou ser executado diretamente via ASGI.

Impacto:
- Testes automatizados de API com `TestClient` nao rodam no ambiente atual.
- Isso reduz a cobertura pratica para endpoints HTTP e middlewares.

Correcao proposta:
- Adicionar a dependencia de teste esperada (`httpx2` conforme erro do Starlette instalado, ou alinhar versoes FastAPI/Starlette/httpx) em um arquivo de dependencias de desenvolvimento.
- Criar suite minima de testes HTTP para auth, CORS, sessoes, cameras e frames.

#### 36. WebSocket autenticado por cookie exige cuidado em deploy cross-site

Status: **registrado em 2026-06-26**.

Evidencia:
- A correcao do item 6 removeu token em query string e passou a autenticar `/ws/detections` por cookie HttpOnly `argos_session`.
- O cookie foi configurado como `SameSite=Lax`, adequado para mesma origem/site e desenvolvimento local.
- Navegadores podem nao enviar esse cookie para WebSocket quando frontend e backend estiverem em sites totalmente diferentes.

Impacto:
- Deploy com frontend em um dominio e API em outro site pode falhar no WebSocket mesmo com CORS HTTP correto.

Correcao proposta:
- Preferir servir frontend e backend sob o mesmo site/origem em producao.
- Se arquitetura cross-site for obrigatoria, adicionar configuracao explicita para cookie `SameSite=None; Secure` e revisar CSRF/origin checks do WebSocket.
- Alternativamente, negociar autenticacao WebSocket por subprotocol curto e de uso unico emitido por endpoint autenticado.

#### 37. Limite de upload no handler nao substitui limite no proxy/ASGI

Status: **registrado em 2026-06-26**.

Evidencia:
- A correcao do item 7 limita bytes durante a leitura do `UploadFile`, mas a camada multipart/ASGI pode receber e preparar parte do corpo antes do handler executar.
- O proprio item 7 recomenda tambem limite no reverse proxy.

Impacto:
- Um atacante ainda pode consumir banda, conexoes e buffers temporarios antes da validacao da aplicacao, dependendo do servidor/proxy usado em producao.

Correcao proposta:
- Configurar limite de corpo no reverse proxy/load balancer, por exemplo `client_max_body_size` no Nginx.
- Adicionar middleware ASGI de limite de corpo antes do parser multipart caso o backend seja exposto diretamente.
- Criar teste de integracao real com servidor ASGI para validar resposta 413 antes de processamento pesado.

#### 38. Rate limit em memoria nao e compartilhado entre workers

Status: **registrado em 2026-06-26**.

Evidencia:
- A correcao do item 9 usa estruturas em memoria (`login_rate_attempts`/`login_rate_lockouts`) no processo Python.
- Em deploy com multiplos workers/processos ou apos restart, cada processo tera seu proprio contador.

Impacto:
- Um atacante pode aumentar tentativas distribuindo requisicoes entre workers ou aguardando restart.

Correcao proposta:
- Migrar rate limit de login para Redis/Postgres ou mecanismo centralizado no proxy/API gateway.
- Manter o limite local como defesa complementar de baixo custo.
- Adicionar metricas/alertas para `auth.login_failed` e `auth.login_rate_limited`.

#### 39. Tokens Telegram legados em claro exigem rotacao/migracao

Status: **registrado em 2026-06-26**.

Evidencia:
- A correcao do item 10 impede novos tokens em claro, mas preserva leitura de valores antigos sem prefixo `fernet:` para nao quebrar uma instalacao existente.

Impacto:
- Um banco que ja continha `telegram_integration.bot_token` em claro continua expondo esse segredo ate a proxima rotacao/migracao.

Correcao proposta:
- Definir `ARGOS_TELEGRAM_SECRET_KEY`, reenviar/salvar o token pela UI para persistir a versao criptografada e rotacionar o token no BotFather.
- Opcionalmente executar migracao administrativa que leia o token legado e o regrave criptografado quando a chave estiver configurada.

#### 40. Dependencia `cryptography` ausente no requirements principal

Status: **corrigido em 2026-06-26**.

Evidencia:
- Ao testar a correcao do item 10, `.venv312` nao tinha `cryptography` instalado.
- `cryptography>=42` existia em `backend/requirements-camera-gateway.txt`, mas nao em `backend/requirements.txt`.

Impacto:
- Criptografia de tokens Telegram e qualquer uso de Fernet no backend principal falharia em ambientes instalados apenas com `backend/requirements.txt`.

Correcao:
- `cryptography>=42` foi adicionado a `backend/requirements.txt`.
- A dependencia foi instalada no `.venv312` atual para executar os testes de criptografia.

## Bugs e inconsistencias funcionais

1. README promete persistencia de frames com ou sem deteccoes, mas `save_processed_frame` ignora frame sem imagem e sem deteccao.
2. `ARGOS_CAMERA_SECRET_KEY` vazio causa descarte silencioso de senha de camera.
3. `broadcast` so remove clientes em `RuntimeError`; outros erros de WebSocket podem vazar cliente morto.
4. `ws_detections` espera `receive_text()` eternamente; cliente que nao envia nada fica ok, mas nao ha ping/pong/timeout explicito.
5. `camera_focus` permite viewer influenciar scheduler de inferencia, talvez aceitavel, mas e uma alteracao operacional feita por role baixo.
6. `get_model` cacheia por nome do modelo, mas warmup usa `imgsz` da primeira camera; chamadas posteriores com outro imgsz do mesmo modelo nao re-warmam.
8. `resolve_player_cached` pode cachear erro ou URL expirada por ate 64 entradas sem TTL.
9. `feedback_export_dataset` busca ate 50.000 feedbacks e depois faz `get_event/get_frame` por item, padrao N+1.
10. `feedback_stats(object_type=...)` tambem faz N+1 chamando `get_event` por feedback.
11. `list_review_events` busca `max(limit * 5, 500)` antes dos filtros de feedback; filtros podem retornar menos itens mesmo existindo mais no banco.
12. `events_range` e `review_events` com filtro de atributos aumentam query para 50.000 em memoria; isso pode ser pesado.
13. `floor_plan_upload` aceita content-type/extensao mas nao limita dimensoes reais antes de decodificar.
14. `public_benchmark_job` devolve `source_path` dentro do job, expondo caminho local do servidor para usuarios autenticados.

## Gargalos de performance

1. **YOLO serializado por `_model_lock`**: gargalo principal em multi-camera.
2. **Um worker de captura por camera**: escalabilidade limitada quando houver muitas cameras.
3. **`main.py` mistura API e processamento**: API compete com inferencia, benchmark, Telegram e captura.
4. **N+1 em feedback/export/stats**: consultas repetitivas por evento/frame.
5. **Filtros de atributos em memoria**: carregam ate 50.000 eventos.
6. **Frontend monolitico**: 8421 linhas, muita renderizacao manual e re-render integral de listas.
7. **Base64 de imagens no JSON**: quando usado, aumenta payload e memoria; endpoint de imagem separado e melhor.
8. **Upload inteiro em memoria**.
9. **PostgresConnection abre nova conexao por operacao**: sem pool.
10. **Geofence point-in-polygon em Python por deteccao/regra**: ok para baixo volume, mas pode crescer mal.

## Funcoes/processos possivelmente inutilizados ou redundantes

- `purge_frames_without_detections` aparece apenas na propria definicao; nao e chamado.
- `ml_training/train_candidate.py` e `ml_training/evaluate_candidate.py` sao placeholders.
- Relatorios HTML em `benchmarks/reports` parecem artefatos gerados, nao codigo-fonte.
- Duplicidade conceitual de endpoints `/cameras` e `/api/cameras`: ambos gerenciam cameras, mas o segundo trata IP camera/gateway. Pode confundir API publica.
- `STORE_FRAME_IMAGES_IN_DB` existe, mas padrao e arquivo; manter ambos aumenta complexidade operacional.
- `ENABLE_MOCK_FALLBACK` mantem detector mock no pipeline; util para demo, perigoso se ativado sem sinalizacao clara.

## Qualidade de codigo

Pontos positivos:

- Uso amplo de Pydantic para payloads.
- Parametrizacao SQL na maioria das consultas.
- Senhas de usuarios com PBKDF2 e `hmac.compare_digest`.
- Scrub parcial de URLs com credenciais.
- Separacao de modulos para captura, frame processing, scheduler, gateway, atributos, tracker e DB.
- Auditoria persistente para muitas operacoes.
- Retencao de frames antigos e limpeza de arquivos orfaos.
- Fallback SQLite facilita desenvolvimento local.

Pontos negativos:

- Arquivos centrais grandes demais.
- Autenticacao ainda usa token em `localStorage`, nao tem MFA e o rate limit de login ainda e local ao processo.
- Frontend teve XSS mitigado nos pontos principais, mas ainda precisa sair de scripts/estilos inline para CSP forte.
- Poucos testes automatizados.
- Sem migracoes de banco.
- Sem observabilidade estruturada robusta.
- Sem politica clara de secrets.
- Sem CI/lint/typecheck visivel.

## Recomendações por esforço/ganho

### Alto ganho, baixo esforço

1. Remover `unsafe-inline` da CSP extraindo JS/CSS inline.
2. Adicionar `pytest`/dependencias de teste ao dev requirements e criar smoke tests de auth/endpoints.
3. Centralizar rate limit em Redis/proxy e aplicar tambem a endpoints caros.
4. Adicionar `.gitignore` para reports/results/logs/datasets/debug generated.
9. Corrigir `protect_camera_password`: erro se chave ausente e senha enviada.
10. Validar max_length em campos textuais principais.

### Alto ganho, esforço medio

1. Mitigar SSRF com validacao de URL/IP/rede e allowlist.
2. Trocar `innerHTML` inseguro por DOM/textContent/escape sistematico.
3. Centralizar rate limit de login e implementar limites para endpoints caros.
4. Criar pool de conexoes Postgres.
5. Refatorar `main.py` em routers: auth, cameras, frames, review, geofences, floor_plans, integrations, benchmarks.
6. Criar Alembic migrations.
7. Persistir frames vazios conforme politica documentada.
8. Otimizar feedback/export com joins/batch queries.
9. Proteger benchmark artifacts com auth.
10. Adicionar CSP e headers de seguranca.

### Alto ganho, alto esforço

1. Separar API e workers de processamento.
2. Introduzir fila/job queue para captura, inferencia, Telegram, benchmark e export.
3. Redesenhar auth com cookies HttpOnly ou OIDC/JWT curto + refresh.
4. Modularizar frontend ou migrar para framework com componentes e escape por padrao.
5. Criar observabilidade: logs estruturados, metricas Prometheus, tracing de pipeline.
6. Criar pipeline real de ML training/evaluation com versionamento de dataset/modelo.
7. Politica completa de privacidade por camera/evento/frame/dataset.

## Plano de correção sugerido

### Semana 1: seguranca basica

- Remover senhas padrao em producao.
- CORS allowlist.
- TTL de sessao e logout.
- Rate limit login.
- Health publico minimo.
- Limite upload.
- Bloqueio SSRF inicial para loopback/private/link-local.

### Semana 2: XSS e tokens

- Revisar todos os `innerHTML`.
- Renderizar dados de usuario com `textContent`.
- Migrar imagens autenticadas para fetch blob.
- Remover token de WebSocket URL ou trocar para cookie seguro.
- Adicionar CSP.

### Semana 3: testes e banco

- Adicionar pytest, httpx TestClient e fixtures SQLite temporarias.
- Testes de auth/roles.
- Testes de permissao de frame.
- Testes de upload.
- Testes SSRF validator.
- Iniciar Alembic.

### Semana 4: performance e arquitetura

- Reduzir lock YOLO.
- Pool Postgres.
- Otimizar consultas N+1.
- Separar routers.
- Planejar worker/fila.

## Testes recomendados

### Unitarios

- `auth`: login valido/invalido, roles, sessao expirada, usuario desativado.
- `db`: seed, hash, CRUD cameras, CRUD geofences, frame storage, feedback.
- `stream_resolver`: URL direta, YouTube mockado, HTML mockado, bloqueio SSRF.
- `frame_change_gate`: first frame, unchanged, changed, force interval.
- `priority_scheduler`: foco, falha, geofence, starvation.
- `floor_plan_coverage`: cobertura, obstaculos, limites.
- `attribute_extractor`: crops invalidos, cor dominante, qualidade baixa.

### Integracao

- FastAPI TestClient com SQLite temporario.
- Fluxo login -> criar camera -> processar snapshot mockado -> listar frame/evento.
- Permissao `frame_access` para viewer/operator/admin/users.
- Upload de planta dentro/fora do limite.
- Feedback/export sem incluir sensiveis.
- WebSocket autenticado/nao autenticado.

### Seguranca

- Brute force login.
- XSS em nomes/descriptions/chat/geofences.
- SSRF com `127.0.0.1`, `localhost`, `169.254.169.254`, `10.0.0.0/8`, IPv6 loopback, DNS rebinding.
- Upload de imagem gigante/arquivo falso.
- Token query leakage.

### Performance

- Stress com 1/5/10/15 cameras.
- Latencia p95/p99 por etapa: resolve, capture, inference, db, websocket.
- Uso CPU/RAM/GPU.
- Tamanho de banco e pasta `data/frames` por dia.

## Conclusao

O sistema tem boa base funcional para MVP e demonstracao operacional, mas os controles de seguranca, testes e isolamento de processamento ainda nao sustentam exposicao em rede nao confiavel. A prioridade deve ser: credenciais/sessoes, CORS, SSRF, XSS, tokens em URL, upload limits e testes automatizados. Depois disso, separar workers e modularizar backend/frontend trara ganhos fortes de confiabilidade e manutencao.

---

# Diagnostico consolidado adicional - 2026-06-26

Esta secao registra uma nova passada pelo sistema apos as correcoes incrementais dos itens 1 a 10. O objetivo aqui e separar o que ja melhorou do que ainda limita seguranca, confiabilidade, performance, manutencao e evolucao do Kaelis Argos.

## Evidencias desta nova revisao

- Arquivos principais medidos: `backend/main.py` com 3070 linhas, `backend/db.py` com 2929 linhas, `frontend/index.html` com 8503 linhas.
- O backend ainda concentra grande parte da aplicacao em um monolito FastAPI unico, com 88 decorators de rota em `backend/main.py`.
- `python -m compileall backend` executou com sucesso durante a rodada de correcoes anterior.
- `pip check` executou com sucesso no ambiente `.venv312`.
- `pytest` ainda nao executa porque o ambiente atual nao tem o pacote instalado: `.venv312/bin/python: No module named pytest`.
- Foram identificados artefatos pesados locais: `backend/spmu.db` com cerca de 33 MB, `backend/yolov8s.pt` com cerca de 22 MB, `backend/yolov8n.pt` e `yolov8n.pt` com cerca de 6,2 MB cada.
- O launcher local `Abrir Kaelis Argos.command` aciona `scripts/start_kaelis_argos.sh`, que sobe backend em `127.0.0.1:8000` e frontend em `127.0.0.1:8080`. Isto e adequado para desenvolvimento local, nao para producao.

## Fontes externas usadas para Raspberry Pi e Hailo

- Raspberry Pi AI HATs: https://www.raspberrypi.com/documentation/accessories/ai-hat.html
- Raspberry Pi AI software: https://www.raspberrypi.com/documentation/computers/ai.html
- Ultralytics Hailo integration: https://docs.ultralytics.com/integrations/hailo/

## Estado atual da stack

### Backend

- Python com FastAPI/Uvicorn.
- Autenticacao propria em `backend/auth.py`, agora com sessoes persistidas em banco e TTL.
- Persistencia centralizada em `backend/db.py`, com SQLite local por padrao e suporte a Postgres via `DATABASE_URL`.
- Processamento de video/imagem em `backend/frame_processor.py`, `backend/capture_manager.py`, `backend/camera_gateway.py` e `backend/stream_resolver.py`.
- Inferencia com Ultralytics/YOLO via modelos `.pt`; nao ha caminho Hailo/HEF implementado.
- Controles recentes adicionados: endurecimento de producao, CORS configuravel, protecao SSRF, cookies de sessao, rate limit de login, upload validado e criptografia opcional para token Telegram.

### Frontend

- SPA monolitica em `frontend/index.html`.
- Estado, HTML, CSS e JavaScript concentrados em um unico arquivo.
- CSP foi adicionada, mas ainda depende de `unsafe-inline`, principalmente por causa do frontend monolitico.
- A autenticacao migrou para cookie, reduzindo exposicao de token em URL, mas ainda existem riscos de deploy cross-site se SameSite/CORS/proxy forem configurados incorretamente.

### Infra e execucao

- `docker-compose.yml` sobe Postgres, backend e frontend Nginx, mas ainda usa credenciais de desenvolvimento no compose.
- `Dockerfile.backend` usa Python 3.11 slim; o ambiente local usa `.venv312`, gerando risco de divergencia.
- O script de abertura local e muito util para desktop, mas nao substitui service manager, reverse proxy, TLS, backup, metricas e limites de recurso.

## Problemas, bugs e gargalos atuais por ordem logica

### P0 - Bloqueadores antes de producao real

1. **Testes automatizados nao executaveis no ambiente atual**
   - Impacto: cada correcao critica fica dependente de validacao manual ou compilacao superficial.
   - Evidencia: `pytest` nao esta instalado no ambiente `.venv312`.
   - Correcao recomendada: adicionar `pytest`, `httpx` compativel com FastAPI TestClient, fixtures temporarias e uma suite minima de smoke/security tests.
   - Esforco/ganho: baixo esforco, altissimo ganho.
   - Status da correcao: ambiente pytest criado com `backend/requirements-dev.txt`, `pytest.ini` e suite inicial em `tests/` cobrindo health publico, status autenticado, login/cookie, rate limit, CORS, SSRF e validacoes basicas de upload. A dependencia de teste usa `httpx2`, alinhada ao aviso atual do `fastapi.testclient`. Validacao executada em 2026-06-26: `.venv312/bin/python -m pytest` com 9 testes aprovados.

2. **Sem migracoes versionadas de banco**
   - Impacto: as mudancas de schema feitas em `ensure_schema()` reduzem controle sobre rollback, auditoria e deploy multiambiente.
   - Evidencia: nao ha Alembic ou mecanismo equivalente.
   - Correcao recomendada: introduzir Alembic, congelar schema atual como baseline e migrar novas alteracoes para scripts versionados.
   - Esforco/ganho: medio esforco, alto ganho.

3. **Inferencia atual nao aproveita Raspberry Pi AI HAT+**
   - Impacto: no Raspberry Pi 5, YOLO `.pt` tende a rodar em CPU se nao houver pipeline Hailo, limitando FPS e quantidade de cameras.
   - Evidencia: o projeto usa pesos `.pt`; a documentacao Ultralytics indica que Hailo usa artefatos `.hef`, normalmente passando por ONNX e Dataflow Compiler.
   - Correcao recomendada: criar backend de inferencia abstrato com implementacoes `ultralytics_cpu` e `hailo_hef`, detectando disponibilidade do HailoRT.
   - Esforco/ganho: medio/alto esforco, alto ganho em edge.

4. **Credenciais de camera podem continuar incompletas sem chave de criptografia**
   - Impacto: cameras RTSP autenticadas podem falhar silenciosamente ou exigir recadastro depois de habilitar criptografia.
   - Evidencia: item 11 historico ainda permanece como risco operacional.
   - Correcao recomendada: bloquear salvamento de credenciais de camera sem chave em producao, criar rotina de validacao/migracao e alertar no painel.
   - Esforco/ganho: baixo/medio esforco, alto ganho.

5. **Frontend monolitico impede CSP forte**
   - Impacto: a mitigacao de XSS melhorou, mas `unsafe-inline` enfraquece a politica.
   - Evidencia: `frontend/index.html` ainda concentra HTML, CSS e JavaScript inline.
   - Correcao recomendada: extrair JS/CSS para arquivos estaticos com hashes ou nonces, removendo `unsafe-inline`.
   - Esforco/ganho: medio esforco, alto ganho.

### P1 - Alta prioridade de confiabilidade e escala

6. **Backend monolitico com rotas, estado, streaming e inferencia no mesmo processo**
   - Impacto: falha ou lentidao em captura/inferencia afeta API, UI, WebSocket e operacao administrativa.
   - Correcao recomendada: separar routers FastAPI, worker de captura/inferencia e fila de eventos.
   - Esforco/ganho: alto esforco, alto ganho.

7. **`backend/main.py`, `backend/db.py` e `frontend/index.html` estao grandes demais**
   - Impacto: aumenta custo de manutencao, risco de regressao e dificuldade de teste.
   - Correcao recomendada: quebrar por dominios: auth, cameras, frames, users, settings, telegram, benchmarks, floor plans e reports.
   - Esforco/ganho: medio/alto esforco, alto ganho continuo.

8. **Rate limit de login ainda e em memoria**
   - Impacto: em multiplos workers/containers o limite pode ser contornado alternando instancias.
   - Correcao recomendada: usar Redis ou tabela dedicada com janela temporal e limpeza.
   - Esforco/ganho: medio esforco, alto ganho para producao.

9. **Sem limites de recurso no proxy/ASGI**
   - Impacto: limites no handler de upload ajudam, mas requisicoes grandes ainda podem consumir memoria antes de chegar na logica.
   - Correcao recomendada: configurar Nginx `client_max_body_size`, timeouts, rate limits e limites de workers.
   - Esforco/ganho: baixo esforco, alto ganho.

10. **Docker/backend roda com risco de divergencia de versao Python**
    - Impacto: bugs podem aparecer em producao usando Python 3.11 enquanto desenvolvimento usa 3.12.
    - Correcao recomendada: escolher versao alvo unica, ajustar Dockerfile, `.python-version`/docs e CI.
    - Esforco/ganho: baixo esforco, medio/alto ganho.

### P2 - Performance e operacao

11. **YOLO lock/global model limita paralelismo**
    - Impacto: multiplas cameras competem pelo mesmo caminho de inferencia.
    - Correcao recomendada: fila por prioridade, batch quando fizer sentido, pool controlado por backend de inferencia.
    - Esforco/ganho: medio esforco, alto ganho.

12. **Armazenamento de frames pode crescer sem politica operacional completa**
    - Impacto: Raspberry Pi e discos pequenos podem saturar rapidamente.
    - Correcao recomendada: retencao por idade/tamanho, compressao, thumbnails, quotas por camera e painel de uso.
    - Esforco/ganho: medio esforco, alto ganho.

13. **Banco SQLite local pode virar gargalo em edge com muitos writes**
    - Impacto: processamento de frames, eventos e auditoria podem competir por lock.
    - Correcao recomendada: para Pi, considerar Postgres local em SSD USB/NVMe ou SQLite WAL bem configurado com batch writes.
    - Esforco/ganho: medio esforco, medio/alto ganho.

14. **Dependencias sem pin exato**
    - Impacto: builds podem variar ao longo do tempo.
    - Correcao recomendada: gerar lock file ou constraints por ambiente (`prod`, `dev`, `pi-hailo`).
    - Esforco/ganho: baixo/medio esforco, alto ganho.

15. **Artefatos grandes no workspace**
    - Impacto: modelos e bancos locais pesados dificultam clone, backup e deploy.
    - Correcao recomendada: garantir que `.pt`, `.db`, frames e logs nao sejam versionados; mover modelos para etapa de download/cache.
    - Esforco/ganho: baixo esforco, medio ganho.

### P3 - Melhorias de produto e qualidade

16. **Observabilidade ainda limitada**
    - Impacto: dificil responder por que uma camera falhou, por que FPS caiu ou por que a IA atrasou.
    - Correcao recomendada: metricas por camera, fila, inferencia, DB, WS, captura, memoria e temperatura.
    - Esforco/ganho: medio esforco, alto ganho operacional.

17. **Sem health/readiness separados**
    - Impacto: `/health` minimalista e bom para publico, mas deploy precisa readiness autenticado ou interno.
    - Correcao recomendada: adicionar `/ready` interno com banco, modelo, storage, fila e camera gateway.
    - Esforco/ganho: baixo esforco, medio ganho.

18. **Sem modo degradado claro**
    - Impacto: se IA falhar, o sistema pode perder valor em vez de continuar capturando/mostrando cameras.
    - Correcao recomendada: expor estados `capture_only`, `inference_down`, `db_degraded`, `storage_full`.
    - Esforco/ganho: medio esforco, alto ganho.

19. **Permissoes e auditoria precisam de testes dedicados**
    - Impacto: roles existem, mas regressao em endpoint sensivel pode passar despercebida.
    - Correcao recomendada: matriz de testes por role e endpoint.
    - Esforco/ganho: baixo/medio esforco, alto ganho.

20. **Experiencia de configuracao inicial pode melhorar**
    - Impacto: deploy em Pi, Docker ou desktop ainda exige conhecimento tecnico.
    - Correcao recomendada: wizard de primeiro acesso, check de ambiente e checklist de producao.
    - Esforco/ganho: medio esforco, alto ganho de adocao.

21. **FastAPI `@app.on_event` esta depreciado**
    - Impacto: a suite pytest passou, mas emitiu aviso de depreciacao para os handlers de startup/shutdown em `backend/main.py`.
    - Correcao recomendada: migrar startup/shutdown para lifespan async context manager do FastAPI, mantendo controle claro de `producer_task`, `retention_task` e `capture_manager.stop_all()`.
    - Esforco/ganho: baixo/medio esforco, medio ganho.

## Melhorias e features de valor

1. **Perfil Edge/Raspberry Pi**
   - Pacote de configuracao `pi`: limites de FPS/resolucao, HailoRT, retencao agressiva, monitor de temperatura e watchdog.

2. **Backend de inferencia plugavel**
   - Interface unica para CPU/GPU/Hailo, permitindo trocar motor sem alterar rotas e UI.

3. **Painel de saude operacional**
   - Status por camera, latencia de captura, inferencia, fila, ultimo frame, erros recentes, uso de disco e temperatura.

4. **Gerenciamento de retencao**
   - Politicas por camera: guardar somente eventos, snapshots periodicos, thumbnails, expiracao e limite de GB.

5. **Alertas inteligentes**
   - Telegram/webhook/email com deduplicacao, janela silenciosa, severidade, cooldown e resumo diario.

6. **Modo privacidade**
   - Mascaramento de regioes, blur de faces/placas e politica de exportacao.

7. **Calibracao assistida de cameras e geofences**
   - Ferramenta visual para validar angulo, zonas, cobertura e areas cegas.

8. **Treinamento/feedback fechado**
   - Fila de revisao humana, export COCO/YOLO, metricas antes/depois e promocao controlada de modelos.

9. **Instalador local guiado**
   - Script unico que valida Python, portas, disco, camera, HailoRT, Postgres/SQLite e cria `.env`.

10. **Backups e restore**
    - Backup agendado de banco/configuracoes, exclusao de frames pesados por politica e restore testavel.

## Ordem por esforco/ganho

### Baixo esforco, alto ganho

- Instalar e fixar `pytest`/`httpx`, criar smoke tests de auth, health, CORS, upload e SSRF.
- Configurar limites no Nginx/proxy: body size, timeouts e rate limit basico.
- Unificar versao Python entre Docker e ambiente local.
- Adicionar `.env.production.example` com flags obrigatorias e checklist.
- Validar que modelos, bancos, frames, logs e videos nao entram no Git.
- Adicionar `/ready` interno ou autenticado.

### Medio esforco, alto ganho

- Introduzir Alembic.
- Extrair CSS/JS do frontend e fortalecer CSP.
- Quebrar `backend/main.py` em routers.
- Criar backend de inferencia plugavel.
- Implementar retencao por tamanho/idade.
- Migrar rate limit para Redis/Postgres.
- Painel de observabilidade por camera e inferencia.

### Alto esforco, alto ganho

- Separar API, worker de captura, worker de inferencia e fila.
- Criar perfil completo para Raspberry Pi 5 + AI HAT+ com HailoRT/HEF.
- Reestruturar frontend em componentes ou framework leve.
- CI completo com lint, testes, build Docker e smoke E2E.
- Modelo operacional multi-camera com escalonamento, backpressure e modo degradado.

### Baixo esforco, ganho medio

- Documentar `Abrir Kaelis Argos.command` como modo desktop/dev.
- Adicionar comando de limpeza de dados locais.
- Registrar tamanho do banco e pasta de frames no painel admin.
- Adicionar logs estruturados em JSON opcional.

### Alto esforco, ganho incerto ou futuro

- Treinamento automatico completo dentro do produto.
- Multi-tenant complexo.
- Clusterizacao de edge nodes.
- Analytics historico avancado antes de estabilizar captura/inferencia.

## Compatibilidade: Raspberry Pi 5 8GB + Raspberry Pi AI HAT+

### Veredito

Compatibilidade **parcial hoje** e **boa apos adaptacao especifica**. O sistema provavelmente sobe no Raspberry Pi 5 8GB como aplicacao web/API se dependencias forem instaladas corretamente, mas a inferencia atual com modelos YOLO `.pt` nao usa automaticamente o AI HAT+. Para usar o acelerador Hailo e preciso converter/compilar modelos para `.hef`, instalar HailoRT e criar um caminho de inferencia especifico.

### O que e compativel

- FastAPI/Uvicorn, frontend estatico e SQLite/Postgres podem rodar no Raspberry Pi 5 8GB.
- O AI HAT+ e feito para Raspberry Pi 5 e usa NPU Hailo.
- Existem variantes AI HAT+ com Hailo-8L de 13 TOPS e Hailo-8 de 26 TOPS.
- O Raspberry Pi OS 64-bit e o caminho recomendado pela documentacao Raspberry Pi para a stack AI.
- Com SSD USB/NVMe e limites adequados, o Pi 5 pode ser um bom node edge para poucas cameras.

### O que nao e compativel automaticamente

- Pesos YOLO `.pt` nao rodam diretamente no Hailo NPU.
- O AI HAT+ nao acelera decodificacao RTSP, OpenCV generico, banco, frontend ou WebSocket.
- O AI HAT+ comum nao e o mesmo que AI HAT+ 2; a linha HAT+ 2 tem Hailo-10H, memoria propria e foco maior em LLM/VLM. O AI HAT+ tradicional depende da memoria do Pi.
- O projeto nao contem instalador HailoRT, deteccao de Hailo, modelos `.hef`, nem fallback operacional por backend.

### Riscos no Raspberry Pi

- CPU pode saturar com decodificacao de multiplos streams, mesmo com NPU acelerando deteccao.
- RAM de 8 GB e suficiente para API + UI + algumas cameras, mas pode ficar apertada com buffers, frames grandes, Postgres, browser local e processos de inferencia.
- Cartao SD nao e ideal para banco e frames; SSD e altamente recomendado.
- Temperatura e throttling podem reduzir FPS em carga continua.
- Retencao de imagens/videos pode consumir disco rapidamente.
- Compilacao de modelos Hailo geralmente nao deve ser feita no Pi; o fluxo mais seguro e compilar em maquina x86/Linux adequada e copiar `.hef` para o Pi.

### Arquitetura recomendada para Pi

1. Rodar o Pi como node edge, nao como servidor central de muitos clientes.
2. Usar SSD USB/NVMe para banco, modelos e frames.
3. Usar Raspberry Pi OS 64-bit.
4. Instalar HailoRT e validar `hailortcli`/runtime antes do Argos.
5. Criar pasta de modelos `models/hailo/*.hef`.
6. Implementar `InferenceBackend` com:
   - `UltralyticsBackend` para desenvolvimento e fallback.
   - `HailoBackend` para `.hef`.
   - selecao por `ARGOS_INFERENCE_BACKEND=auto|ultralytics|hailo`.
7. Reduzir stream por camera: resolucao e FPS de inferencia configuraveis.
8. Separar FPS de visualizacao de FPS de IA.
9. Monitorar temperatura, memoria, fila e tempo de inferencia.
10. Usar retencao agressiva e thumbnails por padrao.

### Capacidade esperada por perfil

- **Dev/demo local:** 1 a 2 cameras, SQLite, YOLO CPU ou Hailo se implementado, retencao curta.
- **Edge leve com Hailo:** algumas cameras com FPS de inferencia reduzido, captura continua e deteccao em intervalos.
- **Producao multi-camera pesada:** nao recomendado somente em um Pi 5; usar servidor central, NVR dedicado ou multiplos nodes edge.

## Proximos passos recomendados

1. Criar suite minima de testes e instalar dependencias de teste.
2. Introduzir Alembic antes de novas mudancas de schema.
3. Criar interface de inferencia plugavel.
4. Implementar perfil `pi` com limites, monitoramento e storage seguro.
5. Converter um modelo pequeno para Hailo `.hef` e validar uma camera real.
6. Separar worker de inferencia do processo da API.
7. Fortalecer CSP removendo inline scripts/styles.
8. Adicionar painel de saude operacional.

## Conclusao desta nova revisao

O Kaelis Argos evoluiu bastante nos dez primeiros itens de seguranca, especialmente em sessoes, CORS, SSRF, upload, health, rate limit e segredos Telegram. O maior risco atual deixou de ser uma vulnerabilidade isolada e passou a ser maturidade operacional: falta de testes, falta de migracoes, frontend/backend monoliticos e inferencia ainda acoplada ao caminho CPU/Ultralytics. Para Raspberry Pi 5 + AI HAT+, o projeto e promissor como edge node, mas precisa de um perfil proprio e de suporte Hailo real antes de prometer aceleracao por NPU.

---

# Implementacao adicional - Analise temporal de permanencia - 2026-06-26

Foi implementada uma camada completa inicial de inteligencia temporal para pessoas e veiculos, seguindo a regra central: deteccao isolada nao vira evento operacional.

## O que foi implementado

- Novo motor `backend/temporal_tracking.py` com maquina de estados: `observed`, `confirmed`, `moving`, `stationary_candidate`, `stationary`, `alert`, `stale`, `closed`.
- Confirmacao temporal por multiplos frames, idade minima do track, mesma area aproximada e baixa movimentacao.
- Calculo separado de `seen_duration_seconds`, `geofence_duration_seconds` e `stationary_duration_seconds`.
- Associacao conservadora para deteccoes sem `track_id`; em caso ambiguo cria novo track `observed`.
- Tolerancia a perda curta de deteccao e encerramento por `stale`/`closed` quando excede limite.
- Motor de regras temporais por geofence com thresholds de tempo parado, permanencia, frames minimos, confianca minima, severidade e cooldown.
- Persistencia em tabelas novas/expandidas: `object_tracks`, `track_history`, `events` e novos campos em `geofence_rules`.
- Endpoints adicionados: `GET /api/tracks/active`, `GET/POST /api/geofences/{id}/rules`, `PUT/DELETE /api/geofence-rules/{id}`, `GET /api/events`, `POST /api/events/{id}/acknowledge`.
- Integracao no processamento: cada payload processado passa a incluir `detections[].temporal`, `tracks` e `temporal_events`.
- Frontend atualizado para exibir status temporal, tempos no mapa, chips/boxes do viewer e configuracao de regra temporal no painel de geofences.
- Alertas antigos baseados em geofence por frame unico foram desativados no fluxo de criacao; eventos novos exigem evidencia temporal.

## Testes criados

- `tests/test_temporal_tracking.py` cobre os 11 casos obrigatorios:
  - um frame fica `observed`;
  - poucos frames nao viram `stationary`;
  - mesma area por pouco tempo vira `stationary_candidate`;
  - mesma area com baixa movimentacao acima do minimo vira `stationary`;
  - movimento dentro da geofence nao gera alerta parado;
  - carro parado fora da geofence nao gera evento;
  - carro parado dentro da geofence acima do limite gera evento;
  - perda curta preserva track;
  - perda longa marca `stale`/`closed`;
  - cooldown evita duplicidade;
  - reassociacao ambigua cria novo `observed`.

## Validacao executada

- `.venv312/bin/python -m pytest`: 20 testes aprovados.
- `.venv312/bin/python -m compileall backend`: aprovado.
- `.venv312/bin/python -m pip check`: aprovado.
- Checagem de sintaxe do JavaScript inline em `frontend/index.html`: aprovada.

## Riscos e proximas melhorias

1. **Estado temporal ainda e em memoria**
   - Em multi-worker/multi-container, cada worker teria seu proprio `TemporalTracker`.
   - Proxima correcao: mover estado ativo/cooldown para Redis ou Postgres com locks leves.

2. **Sem migracoes Alembic**
   - As tabelas novas foram adicionadas via `init_db()`/`ensure_schema`.
   - Proxima correcao: criar baseline Alembic antes de evoluir mais o schema.

3. **Eventos temporais e alertas legados coexistem na UI**
   - A UI reaproveita a lista de alertas para mostrar eventos temporais.
   - Proxima melhoria: painel dedicado de eventos temporais com acknowledge e evidencias.

4. **Geofence exibida como id nos tooltips vivos**
   - O payload temporal traz `geofence_id`; a UI ainda nao resolve sempre para nome na tooltip de deteccao.
   - Proxima melhoria: enriquecer payload com `geofence_name` ou resolver localmente em todos os pontos.

5. **Persistencia de historico e retencao**
   - `track_history` pode crescer em ambientes com muitas cameras.
   - Proxima melhoria: politica de retencao por idade/tamanho e compactacao de evidencias.

---

# Implementacao adicional - Feature Manager conservador - 2026-06-26

Foi implementado um Feature Manager administrativo restrito a features ja existentes e de baixo risco operacional. Esta versao nao desliga YOLO, tracking, autenticacao, auditoria de escrita nem o loop principal de processamento.

## Features controladas

| Feature | Custo estimado | Consequencia ao desativar |
|---|---|---|
| `chat_enabled` | Baixo; banco, WebSocket e renderizacao, sem GPU | Bloqueia chat operacional, mensagens e mencoes |
| `telegram_enabled` | Baixo/medio; rede externa e envio de imagem em alertas | Bloqueia configuracao, teste e envio de alertas Telegram |
| `benchmarks_enabled` | Alto durante execucao; CPU/GPU/processos auxiliares | Bloqueia jobs de stress benchmark |
| `human_feedback_enabled` | Medio; consultas historicas e gravacao de anotacoes | Bloqueia revisao humana, feedback, estatisticas e correcoes |
| `heatmap_enabled` | Baixo; agregacao/renderizacao historica | Bloqueia endpoint e uso do heatmap |
| `floor_plans_enabled` | Medio; upload/decodificacao/cobertura/processed-frame indoor | Bloqueia plantas internas, cameras indoor, zonas e cobertura |
| `camera_discovery_enabled` | Medio em rede; ONVIF/perfis | Bloqueia descoberta ONVIF e consulta de perfis |
| `visual_attributes_enabled` | Medio; recortes e classificacao HSV/LAB por deteccao em CPU | Mantem objetos/tracking/eventos, mas cores de roupa/carro/moto ficam como desconhecidas |
| `dataset_export_enabled` | Medio/alto em storage; leitura de anotacoes e escrita de dataset | Bloqueia exportacao de dataset para treino |

## Componentes adicionados

- Novo modulo `backend/feature_manager.py` com catalogo fixo, custo estimado, consequencia de desligamento, cache simples e helpers de validacao.
- Tabelas novas:
  - `feature_flags`
  - `feature_flag_audit_log`
- Seed idempotente em SQLite/PostgreSQL via `init_db()`.
- Endpoints administrativos:
  - `GET /api/admin/features`
  - `PATCH /api/admin/features/{feature_key}`
  - `GET /api/admin/features/audit-log`
- Tela frontend **Feature Manager**, visivel apenas para admin, com toggles, impacto, custo e auditoria recente.

## Gates aplicados

- Chat: `/chat/messages`, `/chat/mentions`.
- Telegram: configuracao, teste e envio automatico em alertas.
- Benchmarks: criacao e consulta de jobs.
- ONVIF: descoberta e perfis.
- Heatmap: `/detections/heatmap`.
- Atributos visuais: deteccao de cores de roupas, carros e motos agora e opcional e isolada do YOLO.
- Feedback humano: revisao, feedback, estatisticas e alteracoes.
- Dataset: exportacao de dataset.
- Plantas internas: CRUD, upload, zonas, cameras indoor, processed-frame e cobertura.

## Validacao esperada

- O processamento principal de cameras, YOLO, tracking, geofences, eventos, frames, login e auditoria continuam operando quando qualquer uma dessas features e desligada.
- A deteccao de objetos nao depende da deteccao de cor: se `visual_attributes_enabled` estiver desligada ou o extrator falhar, os atributos ficam como `desconhecido` e o objeto continua sendo publicado.
- Features desligadas retornam HTTP 403 com `feature_key`, nome, consequencia e impacto estimado.
- Alteracoes sao registradas em auditoria especifica e tambem na auditoria geral da aplicacao.
