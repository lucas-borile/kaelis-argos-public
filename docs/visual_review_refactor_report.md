# Relatorio Tecnico: Revisao Visual De Eventos

## Telas Criadas Ou Refatoradas

- **Revisao Visual**: a aba Revisao agora funciona como galeria/carrossel de frames/eventos.
- **Detalhe do frame**: ao selecionar um card, o frame abre em um painel com imagem, caixas de deteccao e acoes rapidas.
- **Modo rapido**: confirma, marca falso positivo, falso alerta, irrelevante e pula para o proximo item.
- **Modo detalhado**: permite classe correta, area corrigida, objeto nao detectado, atributos opcionais, comentario e status de curadoria.
- **Configuracoes > Calibracao da IA**: mostra recomendacoes conservadoras geradas a partir de feedback humano.
- **Acoes rapidas do mapa**: os botoes de scan, limpar eventos, centralizar, regras, heatmap, grid e satelite foram movidos para overlay dentro do frame do mapa e ficam sempre visiveis.

## Componentes Visuais Criados

- `review-gallery`: grade de frames/eventos.
- `review-card`: card de frame com status visual.
- `review-gallery-controls`: navegacao por frame mais recente e mais antigo.
- `review-frame-stage`: area de imagem com overlay de caixas.
- `review-detection-card`: lista amigavel de deteccoes no frame.
- `review-mode-switch`: alternancia entre modo rapido e detalhado.
- `review-draw-box`: caixa temporaria para ajustar area ou registrar falso negativo.

## Tabelas Ou Modelos

Nenhuma migracao destrutiva foi adicionada nesta etapa.

A implementacao reutiliza `feedback_annotations`, que ja aceita:

- `feedback_type`;
- `frame_id`;
- `event_id`;
- `camera_id`;
- `track_id`;
- `corrected_value`;
- `curation_status`;
- `payload_json`.

As correcoes visuais estruturadas sao salvas em `payload_json`, incluindo:

- indice da deteccao;
- bbox original;
- bbox corrigida;
- atributos corrigidos;
- modo de revisao;
- confianca original.

## Endpoints Usados

- `GET /review/events`
- `GET /review/next`
- `GET /review/events/{event_id}`
- `POST /events/{event_id}/feedback`
- `GET /frames/{frame_id}`
- `GET /feedback/stats`
- `POST /feedback/export-dataset`

## Fluxo De Revisao

1. Operador abre Revisao.
2. Escolhe periodo, camera, classe, confianca e status.
3. Clica em **Carregar galeria**.
4. Seleciona um frame na grade.
5. O sistema carrega imagem, deteccoes e status de revisao.
6. Operador usa modo rapido ou detalhado.
7. Feedback e salvo auditavelmente no backend.

## Fluxo De Correcao

- **Correto** salva `detection_correct`.
- **Falso positivo** salva `false_positive`.
- **Classe errada** salva `wrong_class` com classe corrigida.
- **Ajustar area** salva `bad_bbox` com `corrected_bbox`.
- **Objeto nao detectado** salva `false_negative` com caixa desenhada e classe.
- **Irrelevante** salva `event_irrelevant`.
- **Sensivel** salva `privacy_sensitive` e bloqueia exportacao automatica.

## Metricas

As correcoes alimentam `GET /feedback/stats`, usado por:

- Relatorios;
- Configuracoes > Calibracao da IA;
- futuras rotinas de avaliacao de modelo.

Feedbacks de captura como `false_negative`, `bad_bbox`, `wrong_class`, `camera_issue`, `false_positive` e `alert_false` nao alteram a prioridade da camera nem antecipam capturas. Eles sao registrados como sinais de curadoria para dataset, avaliacao e treino controlado fora do loop operacional.

Na interface, clicar em **Corrigir** abre uma tela in-app dedicada com o frame em tamanho amplo, overlay das deteccoes, lista de objetos e acoes de correcao/calibracao. Correcoes visuais incluem `scheduler_boost=false` e `training_signal=<tipo>` no payload, deixando explicito que o feedback deve alimentar adaptacao de modelo e nao um boost temporario de captura.

Metricas imediatas incluem:

- total de feedbacks;
- eventos revisados;
- falsos positivos por camera;
- problemas por camera;
- sensiveis;
- percentual de acerto validado.

## Dataset E Fine-Tuning

O fluxo seguro permanece:

```text
feedback humano
  -> curadoria
  -> /feedback/export-dataset
  -> ml_training/export_feedback_dataset.py
  -> treino candidato fora da producao
  -> avaliacao contra baseline
  -> aprovacao manual
  -> deploy controlado
```

Scaffold criado em `ml_training/`:

- `README.md`
- `export_feedback_dataset.py`
- `train_candidate.py`
- `evaluate_candidate.py`
- `calibration.py`
- `model_registry.json`

## Calibracao

A calibracao inicial e somente recomendativa.

Ela pode sugerir:

- revisar threshold por camera;
- priorizar exemplos de falso negativo;
- verificar camera com baixa qualidade;
- bloquear dados sensiveis de exportacao.

Nenhuma calibracao e aplicada automaticamente no MVP.

## Limitacoes Conhecidas

- A edicao de bbox e visual e salva como payload estruturado; ainda nao altera o frame original.
- Falsos negativos sao vinculados a um evento/frame existente; revisao de frames completamente sem evento ainda depende de timeline futura dedicada.
- Ainda nao ha exportacao YOLO completa com imagens e labels finais.
- Aplicar/desfazer calibracao segue desativado por seguranca.
- Playwright nao esta instalado neste ambiente, entao a validacao visual automatizada nao foi executada.

## Proximos Passos

- Criar endpoint dedicado para `manual_detections`.
- Exportar labels YOLO a partir de `corrected_bbox` e `false_negative`.
- Adicionar editor de bbox com handles de redimensionamento.
- Criar pagina tecnica de curadoria em lote.
- Adicionar testes end-to-end da galeria e do fluxo de feedback.
