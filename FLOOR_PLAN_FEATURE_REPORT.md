# Argos Floor Plan Mode

## O Que Foi Implementado

Foi criado um MVP de modo indoor para plantas baixas, integrado ao Kaelis Argos sem reescrever a arquitetura existente.

No backend:

- Novas tabelas persistidas em PostgreSQL/PostGIS, com fallback SQLite:
  - `floor_plans`
  - `floor_plan_cameras`
- Upload de imagem PNG/JPG da planta em `backend/static/floor_plans/{id}/`.
- Criacao de planta sem imagem, usando largura/altura em pixels e caixas retangulares.
- Endpoints REST em `/api/floor-plans`.
- CRUD de plantas internas.
- CRUD de cameras posicionadas na planta.
- Cameras indoor com URL/source URL propria, alem do vinculo opcional com camera externa existente.
- Vinculo opcional de uma camera interna com uma camera real ja cadastrada.
- Calculo de cobertura 2D aproximado em `backend/floor_plan_coverage.py`, com ray casting para cortar cones em paredes/obstaculos.
- Auditoria de criacao, upload, edicao e exclusao de plantas/cameras internas.

No frontend:

- Nova aba `Ambientes`.
- Mode rail na HUD principal:
  - `M`: modo mapa externo.
  - `▣`: modo ambientes fechados.
  - `+`: cria/acessa fluxo de planta interna.
- Renderizacao de planta baixa usando Leaflet com `CRS.Simple`.
- Upload e exibicao da planta como `imageOverlay`.
- Marcadores de cameras internas arrastaveis.
- Cones de campo de visao em 2D.
- Caixas/zonas retangulares para desenhar salas, corredores, estoque e areas de interesse sem imagem.
- Desenho de poligonos internos para paredes/obstaculos.
- Clique direito na planta para posicionar camera.
- Clique direito em camera indoor para rotacionar em incrementos de `15°`.
- Metricas de cobertura e ponto cego aproximado.
- Formulario para nome, camera vinculada, posicao X/Y, direcao, FoV, alcance, cor e status.

## Endpoints

```http
GET    /api/floor-plans
POST   /api/floor-plans
GET    /api/floor-plans/{floor_plan_id}
PUT    /api/floor-plans/{floor_plan_id}
DELETE /api/floor-plans/{floor_plan_id}
POST   /api/floor-plans/{floor_plan_id}/upload

GET    /api/floor-plans/{floor_plan_id}/cameras
POST   /api/floor-plans/{floor_plan_id}/cameras
PUT    /api/floor-plan-cameras/{camera_id}
DELETE /api/floor-plan-cameras/{camera_id}

GET    /api/floor-plans/{floor_plan_id}/zones
POST   /api/floor-plans/{floor_plan_id}/zones
PUT    /api/floor-plan-zones/{zone_id}
DELETE /api/floor-plan-zones/{zone_id}

GET    /api/floor-plans/{floor_plan_id}/coverage
```

## Como Testar

1. Instale as dependencias:

```bash
source .venv312/bin/activate
pip install -r backend/requirements.txt
```

2. Inicie o backend:

```bash
cd backend
../.venv312/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

3. Inicie o frontend:

```bash
cd frontend
../.venv312/bin/python -m http.server 8080
```

4. Acesse:

```text
http://127.0.0.1:8080
```

5. Entre com um usuario `admin` ou `operator`.

6. Clique no icone `▣` ou na aba `Ambientes`.

7. Crie uma planta. O upload de imagem PNG/JPG e opcional; sem imagem, o sistema usa uma tela vazia com grade.

8. Use `Caixas da planta` para desenhar salas/corredores por retangulos.

9. Clique em `Adicionar clicando` para posicionar cameras internas.

10. Ajuste direcao, FoV e alcance. O cone e as metricas de cobertura devem atualizar.

## Decisoes Tecnicas

- A renderizacao usa Leaflet `CRS.Simple` para reaproveitar o stack visual atual.
- Coordenadas internas usam pixels da imagem:
  - `x_px`: eixo horizontal.
  - `y_px`: eixo vertical.
- Convencao angular:
  - `0°`: cima.
  - `90°`: direita.
  - `180°`: baixo.
  - `270°`: esquerda.
- O calculo de cobertura do MVP usa amostragem em grade e setores circulares, sem depender de Shapely.
- Imagens de plantas ficam fora do banco; o banco guarda o caminho estatico.
- Caixas sem imagem ficam em `floor_plan_zones` como poligonos JSON em coordenadas de pixel.
- Zonas com `blocks_vision=true` e `height_m` suficiente bloqueiam o FoV da camera em 2D.

## Limitacoes Do MVP

- Nao considera paredes, portas, vidro, prateleiras ou oclusoes.
- Nao importa DXF, BIM ou CAD.
- Nao possui editor vetorial completo do interior.
- Nao calcula volume 3D nem altura real da camera.
- Pontos cegos sao aproximados por percentual e pela ausencia de cones, nao por poligonos exatos de diferenca geometrica.
- A colisao de FoV e 2D e conservadora; nao substitui simulacao optica/3D real.
- A planta precisa ser calibrada manualmente com `scale_m_per_px`.
- O vinculo com camera real e opcional; o modo indoor nao depende de stream ativo.

## Melhorias Futuras

- Usar PostGIS/Shapely para diferenca geometrica exata entre planta e cobertura.
- Desenhar paredes/zonas internas e considerar oclusao.
- Importar DXF/BIM/SVG.
- Calibrar escala por dois pontos conhecidos.
- Simular altura, tilt e lente da camera.
- Sugerir reposicionamento automatico para reduzir pontos cegos.
- Associar eventos de YOLO a coordenadas indoor quando a camera vinculada for interna.
