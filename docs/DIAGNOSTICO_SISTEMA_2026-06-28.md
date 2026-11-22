# Diagnostico do Sistema Kaelis Argos

Data: 2026-06-28
Escopo: backend FastAPI, frontend operacional, banco SQLite/Postgres, processamento YOLO/OpenCV, tracking temporal, geofences, ocorrencias, feature manager, feedback humano, treinamento, scripts e testes.

## Resumo executivo

O Argos esta em um bom ponto de MVP operacional: possui autenticacao, cameras, processamento YOLO, fila de atencao, ocorrencias com timeline, geofences, alertas, plantas indoor, feedback humano, anotacao para treino, feature manager, auditoria, chat e benchmark. A suite automatizada esta funcionando e passou integralmente.

O principal risco hoje nao e uma falha unica, mas **acoplamento e complexidade acumulada**: `frontend/index.html`, `backend/main.py` e `backend/db.py` concentram regras demais. Isso aumenta a chance de regressao em features que compartilham mapa, ocorrencias, geofences, frames e WebSocket.

## Validacoes executadas

- `.venv312/bin/pytest -q`: **40 passed**.
- `.venv312/bin/python -m compileall backend`: passou.
- `node --check` do JavaScript extraido de `frontend/index.html`: passou.
- Dados locais: `backend/data` com aproximadamente **280 MB** e **2.176 arquivos de frame**.
- Hotspots por tamanho:
  - `frontend/index.html`: **10.135 linhas**.
  - `backend/db.py`: **4.289 linhas**.
  - `backend/main.py`: **4.046 linhas**.
  - Total dos principais hotspots medidos: **20.326 linhas**.

## Stack atual

- Backend: Python, FastAPI, Uvicorn, Pydantic, OpenCV, Ultralytics YOLO, NumPy, psutil, cryptography, psycopg.
- Banco: SQLite local por padrao; Postgres/PostGIS quando `DATABASE_URL` existe.
- Frontend: HTML unico com CSS/JS embutido, Leaflet local, REST, WebSocket.
- Video: OpenCV, FFmpeg/imageio-ffmpeg, yt-dlp para fontes web/player.
- IA: YOLO por camera/global, tracking temporal, extracao heuristica de atributos, georreferenciamento.
- Operacao: fila de atencao, ocorrencias, geofences, alertas, plantas indoor, chat, Telegram, auditoria.
- Treinamento: anotacao de frames, exportacao YOLO, jobs de treino.
- Infra: Dockerfile backend, docker-compose com PostGIS e frontend nginx.

## Principais superficies funcionais

- Auth e usuarios: `/auth/login`, `/auth/me`, `/auth/logout`, `/users`, `/audit-logs`.
- Cameras: APIs legadas `/cameras...` e novas `/api/cameras...`.
- Processamento: `/processing/scan`, `/processed-frames/latest`, `/cameras/{id}/processed-frame`.
- Frames: `/frames/{id}`, `/frames/{id}/metadata`, `/frames/{id}/image`.
- Eventos/revisao: `/events`, `/review/events`, `/feedback`, `/feedback/stats`.
- Anotacao/treino: `/annotation/frames`, `/annotation/export-yolo`, `/annotation/train-yolo`.
- Geofences: `/geofences`, `/geofence-rules`, `/geofence-alerts`.
- Tracking/temporal: `/api/tracks/active`, `/api/events/{id}/acknowledge`.
- Fila de atencao: `/api/attention`, `/api/attention/{item_key}`, confirmacao rapida.
- Ocorrencias: `/api/incidents`, timeline, PDF, criacao por mapa/frame/geofence.
- Plantas indoor: `/api/floor-plans`, cameras internas, zonas, cobertura.
- Feature Manager: `/api/admin/features`, audit log.
- Chat/Telegram/benchmarks: `/chat/*`, `/integrations/telegram`, `/benchmarks/stress`.

## Falhas e riscos encontrados

### 1. Frontend monolitico e fragil

`frontend/index.html` passou de 10 mil linhas e concentra layout, estado global, mapa, camera viewer, ocorrencias, geofences, review, treinamento e configuracoes.

Risco:
- Regressao visual ou funcional ao alterar uma feature.
- Dificuldade de testar unidade de UI.
- Estado global compartilhado entre mapa, modal, ocorrencias e alertas.

Evidencia:
- Variaveis globais como `incidents`, `geofenceAlerts`, `activeIncidentDetail`, `activeIncidentTarget`, `latestFrameByCamera`, `review*`.
- Muitos `innerHTML`, ainda que em boa parte com `escapeHtml`.

Impacto: alto.
Esforco: alto.

### 2. Backend centralizado em `main.py`

`backend/main.py` tem mais de 4 mil linhas e mistura rotas, regra de negocio, processamento, PDF, Telegram, benchmark, WebSocket, geofences, ocorrencias e helpers.

Risco:
- Dificil isolar falhas.
- Mudancas em uma area podem afetar outra.
- Testes ficam integrados demais.

Impacto: alto.
Esforco: alto.

### 3. Camada de banco muito grande e com migracoes manuais

`backend/db.py` tem mais de 4 mil linhas e gerencia schema SQLite/Postgres, migracoes, repositorios e serializacao.

Risco:
- Divergencia entre SQLite e Postgres.
- Migracoes manuais podem falhar silenciosamente.
- Dificil versionar alteracoes de schema.

Impacto: alto.
Esforco: medio/alto.

### 4. Ocorrencias de geofence antigas sao encerradas apenas em estado local quando nao ha `incident_id`

Hoje alertas novos de geofence viram ocorrencia, mas alertas antigos carregados do historico podem nao ter `incident_id`. O frontend permite ocultar/encerrar visualmente esses pulsantes na sessao, mas isso nao persiste no banco para alertas legados.

Risco:
- Ao recarregar a pagina, um alerta historico antigo sem ocorrencia vinculada pode reaparecer.

Impacto: medio.
Esforco: medio.

Correcao recomendada:
- Criar coluna/status para `geofence_alerts` ou tabela `geofence_alert_states`.
- Endpoint `PATCH /geofence-alerts/{id}` com `resolved_at`, `resolved_by`, `resolution`.
- Migrar alertas antigos para ocorrencias quando necessario.

Correcao realizada:
- `geofence_alerts` agora possui estado persistente no banco: `status`, `resolved_at`, `resolved_by`, `resolution` e `incident_id`.
- A migracao foi adicionada para SQLite e Postgres/PostGIS, preservando bases existentes.
- `GET /geofence-alerts` passa a retornar apenas alertas abertos por padrao; `include_resolved=true` permite auditoria/historico.
- Novo endpoint `PATCH /geofence-alerts/{id}` encerra ou reabre o alerta com persistencia.
- Ao encerrar alerta vinculado a ocorrencia, a ocorrencia associada tambem e encerrada, ganha entrada na timeline e a fila de atencao e atualizada.
- Alertas novos que geram ocorrencia automatica agora gravam o `incident_id` no registro de `geofence_alerts`, nao apenas no estado em memoria.
- O frontend passou a usar o endpoint persistente ao clicar em "Encerrar alerta" e reage a `geofence_alert_updated` via WebSocket para remover marcadores/listas em outras sessoes.
- Testes adicionados em `tests/test_geofence_alerts.py` validam alertas legados sem ocorrencia e alertas com ocorrencia vinculada.

### 5. PDF de ocorrencia e gerado por mecanismo simples

O PDF atual e gerado sem dependencia externa, por um gerador PDF minimo. Funciona e passou validacao, mas tem layout limitado.

Risco:
- Relatorios longos podem truncar.
- Sem paginacao sofisticada, imagens, tabelas ricas ou anexos.

Impacto: medio.
Esforco: medio.

Melhoria:
- Usar ReportLab ou WeasyPrint para relatorio profissional com paginacao, logo, evidencias e miniaturas.

### 6. Dependencia de APIs externas no frontend para contexto do mapa

O chip de bairro/temperatura usa Open-Meteo e Nominatim diretamente do navegador.

Risco:
- Falha por CORS, rate limit ou indisponibilidade.
- Exposicao de padroes de uso/localizacao do mapa para terceiros.
- Nominatim tem politicas de uso que podem restringir uso intensivo.

Impacto: medio.
Esforco: baixo/medio.

Melhoria:
- Proxy/cache no backend.
- TTL local maior.
- Configuracao para desligar contexto externo em ambiente privado.

### 7. Retencao de frames precisa ser mais visivel e controlavel

O ambiente local ja tem ~280 MB e 2.176 arquivos em `backend/data/frames`.

Risco:
- Crescimento de disco em operacao continua.
- Backup e limpeza ficam caros.
- Raspberry Pi/SD card pode sofrer desgaste.

Impacto: alto em edge/Raspberry.
Esforco: medio.

Melhoria:
- Painel de retencao por camera.
- Limite por dias/GB.
- Job de limpeza com metricas no dashboard.

### 8. Duplicidade de APIs de camera

Existem rotas legadas `/cameras` e novas `/api/cameras`, com comportamentos sobrepostos.

Risco:
- Divergencia de contrato.
- Frontend pode chamar endpoint diferente dependendo do tipo de camera.
- Mais superficie para bugs e seguranca.

Impacto: medio.
Esforco: medio.

### 9. WebSocket e estado em tempo real ainda sao best-effort

Eventos de ocorrencia, geofence, frames, atencao e cameras sao propagados por WebSocket, mas o frontend ainda recarrega listas inteiras em varios casos.

Risco:
- Perda de evento temporaria.
- Duplicidade visual.
- Carga desnecessaria conforme numero de cameras/operadores cresce.

Impacto: medio.
Esforco: medio.

### 10. Feature Manager pode desligar dependencias implicitamente

O Feature Manager e util, mas ainda precisa modelar dependencias entre features.

Exemplo:
- Desligar tracking temporal pode afetar alertas de permanencia, fila de atencao e ocorrencias automaticas.

Impacto: medio.
Esforco: medio.

### 11. Testes cobrem bem fluxos recentes, mas ainda faltam testes de UI

Foram validados 40 testes backend/integracao. Ainda nao ha Playwright ou testes automatizados de frontend.

Risco:
- Regressao visual nos modais, toolbar, mapa e viewer.
- Bugs em botoes Leaflet/modais so aparecem manualmente.

Impacto: medio/alto.
Esforco: medio.

### 12. CSP ainda depende de `unsafe-inline`

A CSP existe, mas o frontend monolitico usa scripts e estilos inline.

Risco:
- Mitigacao incompleta contra XSS se algum escape falhar.

Impacto: medio.
Esforco: alto, porque exige modularizar frontend.

### 13. Credenciais de desenvolvimento continuam documentadas

Usuarios seed `admin/admin123`, `operator/operator123`, `viewer/viewer123` ainda aparecem em README/testes/config de desenvolvimento. Ha protecao para recusar defaults em producao, mas o risco operacional de deploy mal configurado permanece.

Impacto: medio.
Esforco: baixo.

Melhoria:
- Banner de ambiente quando `ARGOS_ENV` nao for production.
- Checklist de deploy bloqueante no startup.

### 14. YouTube/yt-dlp continua instavel como fonte de teste

O sistema ja registrou falhas de anti-bot para fontes YouTube.

Impacto: medio para demos/testes; baixo para cameras IP reais.
Esforco: medio.

Melhoria:
- Marcar fonte YouTube como experimental.
- Suportar cookies opcionais via configuracao segura.
- Preferir RTSP/HLS direto para testes longos.

### 15. Treinamento/fine-tuning ainda precisa trilha operacional

Ha anotacao/exportacao/treino, mas falta uma jornada clara de dataset aprovado, modelo por camera, rollback e avaliacao automatica.

Impacto: medio.
Esforco: alto.

## Gargalos provaveis

1. **Inferencia YOLO**: custo escala com cameras, resolucao, `imgsz`, modelo e frequencia.
2. **Captura de streams**: FFmpeg/OpenCV podem bloquear ou acumular processos em cameras instaveis.
3. **Persistencia de frames**: crescimento de disco e I/O.
4. **Frontend Leaflet**: muitos marcadores pulsantes, tracks, heatmap e geofences podem degradar renderizacao.
5. **SQLite em operacao concorrente**: ok para MVP/local; limitado para multiplos operadores/cameras.
6. **WebSocket/list reload**: recarregar listas inteiras apos eventos frequentes pode ficar caro.
7. **Banco sem particionamento**: eventos, frames, tracks e feedback tendem a crescer rapido.

## Melhorias por esforco/ganho

### Alto ganho / baixo esforco

1. Persistir encerramento de `geofence_alerts` antigos.
2. Adicionar painel de retencao de frames com limite por dias/GB.
3. Criar endpoint backend cacheado para contexto do mapa.
4. Exibir alerta de ambiente quando usando credenciais/dev mode.
5. Adicionar testes para endpoints de PDF e estados de geofence alert resolvido.

### Alto ganho / medio esforco

1. Separar `backend/main.py` em routers: auth, cameras, frames, geofences, incidents, training, floor plans, admin.
2. Separar `frontend/index.html` em modulos JS por dominio.
3. Criar testes Playwright para fluxo de ocorrencia, geofence e camera viewer.
4. Criar tabela/endpoint de estado para geofence alerts.
5. Melhorar PDF com ReportLab/HTML profissional e anexos de evidencia.

### Alto ganho / alto esforco

1. Migracoes versionadas com Alembic.
2. Pipeline completo de modelo por camera: dataset, treino, avaliacao, ativacao e rollback.
3. Arquitetura de eventos em fila interna para WebSocket/processamento.
4. Observabilidade: metricas Prometheus, traces, dashboards e alertas.
5. Multi-tenant/perfis avancados de permissao.

## Prioridade sugerida de correcao

1. Persistir resolucao de geofence alerts antigos.
2. Implementar retencao configuravel de frames.
3. Modularizar `backend/main.py` por routers.
4. Criar testes Playwright para UI critica.
5. Modularizar `frontend/index.html`.
6. Cache backend para contexto do mapa.
7. Melhorar PDF de ocorrencia.
8. Versionar schema com Alembic.
9. Consolidar `/cameras` e `/api/cameras`.
10. Formalizar dependencias do Feature Manager.

## Qualidade geral

Nota tecnica atual: **7/10 para MVP operacional**, **5/10 para producao critica**.

Pontos fortes:
- Testes automatizados passando.
- Boa evolucao funcional recente: ocorrencias, fila, geofences, feature flags, treinamento.
- Auth, CORS e protecoes basicas ja existem.
- Arquitetura suporta SQLite e Postgres/PostGIS.
- Sistema ja tem auditoria e trilha operacional.

Pontos fracos:
- Acoplamento alto.
- Frontend sem testes automatizados.
- Retencao/observabilidade ainda insuficientes.
- Algumas features novas ainda precisam persistencia completa de estado historico.
- Dependencias externas no navegador para contexto operacional.

## Conclusao

O Argos esta funcional e pronto para validacao operacional controlada, mas antes de uso prolongado em ambiente real deve priorizar: persistencia de estados operacionais, retencao de frames, modularizacao, testes de UI e observabilidade. A base esta boa; o proximo salto de qualidade e reduzir acoplamento e transformar fluxos que hoje sao "estado de sessao" em estado persistente auditavel.
