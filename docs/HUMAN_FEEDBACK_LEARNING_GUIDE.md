# Human Feedback Learning Guide

Guia tecnica para evoluir o Kaelis Argos com aprendizado orientado por feedback humano, tambem chamado de human-in-the-loop learning, active learning ou revisao assistida por operador.

Este documento nao implementa codigo. Ele define uma referencia pratica para futuras implementacoes de feedback, curadoria, metricas, dataset interno, avaliacao de modelos e melhoria operacional.

## Visao Geral

No contexto do Kaelis Argos, aprendizado com feedback humano significa registrar a avaliacao do operador sobre eventos detectados pelo sistema e usar essa informacao para melhorar o comportamento futuro.

Hoje o sistema detecta objetos em cameras, salva eventos com classe, confianca, bbox, posicao estimada, `track_id`, atributos visuais e historico de revisao. A proxima evolucao e permitir que operadores validem, corrijam ou rejeitem essas inferencias.

O feedback humano deve ser tratado como um dado operacional auditavel. Ele nao altera automaticamente o modelo em producao. Primeiro, o feedback entra em uma fila de curadoria, gera metricas, ajuda a priorizar revisao e, quando apropriado, vira parte de datasets internos versionados.

O ciclo recomendado e:

```text
Evento detectado
  -> revisao humana
  -> feedback auditado
  -> curadoria
  -> metricas de qualidade
  -> dataset interno
  -> treinamento/ajuste fora de producao
  -> avaliacao contra baseline
  -> aprovacao
  -> deploy controlado
```

## O Que O Feedback Humano Deve Melhorar

O feedback humano deve melhorar mais do que o detector YOLO. Ele pode alimentar varias camadas do sistema.

- Deteccao de objetos: reduzir falsos positivos e falsos negativos, ajustar thresholds e identificar classes com desempenho ruim.
- Classificacao: corrigir classe errada, por exemplo `moto` detectada como `bicicleta`, `onibus` como `caminhao` ou pessoa como objeto irrelevante.
- Atributos visuais: corrigir cor superior/inferior, cor dominante de veiculo e marcacoes `desconhecido` quando houver evidencia suficiente.
- Tracking: identificar associacoes incorretas, fragmentacao de trajetos e troca de IDs entre objetos diferentes.
- Geolocalizacao: apontar posicoes estimadas erradas no mapa e calibrar azimute, FoV, altura e parametros por camera.
- Regras de alerta: validar se um alerta foi util, falso, irrelevante ou sensivel.
- Saude das cameras: registrar imagem ruim, camera fora do ar, lente obstruida, angulo alterado, noite excessiva, blur ou baixa qualidade.
- Revisao historica: melhorar filtros, ranking de eventos e recuperacao por atributos.
- Qualidade operacional: medir tempo de revisao, carga por operador, qualidade por camera e areas com maior incerteza.

## Tipos De Feedback

A taxonomia deve ser simples no MVP, mas extensivel. O campo `feedback_type` deve usar valores estaveis e em ingles para facilitar integracoes.

| Tipo | Quando Usar | Quem Aplica | Impacto Futuro | Exige Imagem/Crop/Bbox | Pode Treinar Modelo |
|---|---|---|---|---|---|
| `detection_correct` | O objeto detectado esta correto. | Operador, supervisor. | Mede precisao, reforca exemplos bons. | Recomendado. | Sim. |
| `false_positive` | O sistema detectou objeto onde nao havia objeto valido. | Operador. | Reduz falsos positivos, ajusta thresholds e datasets negativos. | Sim. | Sim. |
| `false_negative` | Havia objeto no frame, mas ele nao foi detectado. | Operador, curador. | Melhora recall e coleta exemplos dificeis. | Sim, com bbox manual futura. | Sim. |
| `wrong_class` | O objeto existe, mas a classe esta errada. | Operador, curador. | Melhora classificacao e matriz de confusao. | Sim. | Sim. |
| `bad_bbox` | A bbox nao cobre bem o objeto. | Operador, curador. | Melhora treinamento de deteccao e avaliacao IoU. | Sim, idealmente bbox corrigida. | Sim. |
| `wrong_attribute` | Cor ou atributo visual esta errado. | Operador. | Melhora extrator de atributos e filtros historicos. | Crop recomendado. | Sim, para atributo; nao necessariamente YOLO. |
| `wrong_track_association` | Dois objetos diferentes foram associados ao mesmo `track_id`. | Operador, supervisor. | Reduz ID switches e ajusta tracker. | Frame/crop recomendado. | Indiretamente. |
| `same_object` | Dois eventos/tracks representam o mesmo objeto ou pessoa provavel. | Operador, supervisor. | Melhora continuidade provavel e reagrupamento de tracks. | Crops recomendados. | Pode treinar reidentificacao nao biometrica. |
| `different_object` | Dois eventos/tracks foram ligados, mas sao objetos diferentes. | Operador, supervisor. | Reduz associacoes indevidas. | Crops recomendados. | Pode treinar separacao visual. |
| `bad_geolocation` | O ponto no mapa esta incorreto. | Operador, analista GIS. | Calibra camera, azimute, FoV e estimador geografico. | Frame e ponto corrigido no mapa. | Nao para YOLO; sim para calibracao. |
| `event_relevant` | Evento importante para operacao. | Operador. | Melhora ranking e priorizacao. | Nao obrigatorio. | Nao diretamente. |
| `event_irrelevant` | Evento correto, mas sem valor operacional. | Operador. | Reduz ruido e ajusta regras. | Nao obrigatorio. | Nao diretamente. |
| `alert_correct` | Alerta gerado foi util e verdadeiro. | Operador, supervisor. | Ajusta regras de alerta e prioridade. | Recomendado. | Nao diretamente. |
| `alert_false` | Alerta gerado foi falso ou indevido. | Operador, supervisor. | Reduz alertas falsos. | Recomendado. | Nao diretamente. |
| `camera_issue` | Camera offline, imagem travada, blur, baixa luz ou angulo alterado. | Operador, manutencao. | Mede saude por camera e reduz confianca de eventos. | Frame recomendado. | Nao. |
| `privacy_sensitive` | Evento contem dado sensivel ou exige anonimizacao. | Operador autorizado, DPO/supervisor. | Aciona retencao reduzida, blur, restricao de acesso. | Sim, com cuidado de acesso. | Nao usar para treino sem curadoria legal. |

Campos auxiliares recomendados:

- `original_value`: valor produzido pelo sistema, como classe original ou cor original.
- `corrected_value`: valor corrigido pelo operador.
- `confidence`: confianca do revisor, por exemplo 1 a 5 ou 0 a 100.
- `comment`: observacao livre.
- `payload_json`: detalhes especificos do feedback, como bbox corrigida, ponto no mapa, IDs relacionados ou motivo da rejeicao.

## Fluxo Operacional

O fluxo operacional deve separar revisao, curadoria, treinamento e producao.

```text
Evento detectado
  -> aparece na aba Revisao
  -> operador valida/corrige
  -> feedback e salvo com auditoria
  -> feedback entra em fila de curadoria
  -> curador resolve conflitos e aprova exemplos
  -> dados alimentam metricas
  -> casos bons viram dataset interno
  -> modelo/regra e ajustado fora de producao
  -> nova versao e avaliada contra baseline
  -> aprovacao tecnica/operacional
  -> deploy controlado em producao
```

Regras importantes:

- Feedback individual nao deve alterar automaticamente o modelo em tempo real.
- Correcoes de operador devem ficar auditaveis e reversiveis.
- Casos sensiveis devem ser sinalizados antes de entrar em dataset.
- Eventos usados para treino devem ter versao de modelo, camera, horario e contexto.
- Alteracoes de threshold e regras devem ser versionadas como configuracao operacional.

## Interface Sugerida

A aba `Revisao` pode evoluir para uma experiencia de anotacao rapida e auditavel.

Controles recomendados:

- Botao `Correto` para `detection_correct`.
- Botao `Falso positivo` para `false_positive`.
- Acao `Classe errada` com campo de classe corrigida.
- Campo de cor/atributo corrigido para `wrong_attribute`.
- Botao `Mesmo objeto que...` para associar dois eventos ou tracks.
- Botao `Nao e o mesmo objeto` para quebrar associacao incorreta.
- Ajuste manual de bbox em fase futura.
- Marcador no mapa para corrigir posicao estimada.
- Campo de observacao.
- Nivel de severidade ou prioridade.
- Sinalizacao `Dado sensivel`.
- Atalhos de teclado para revisao rapida.
- Indicador de que o feedback foi salvo e auditado.
- Filtro para mostrar apenas eventos sem feedback.
- Fila de revisao priorizada por incerteza.

Atalhos sugeridos:

- `C`: correto.
- `F`: falso positivo.
- `K`: classe errada.
- `A`: atributo errado.
- `T`: track errado.
- `G`: geolocalizacao errada.
- `S`: sensivel.

Os atalhos devem exigir foco explicito na tela de revisao para evitar acionamentos acidentais.

## Modelo De Dados MVP

Modelo SQLite inicial:

```sql
CREATE TABLE feedback_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    frame_id INTEGER,
    camera_id TEXT,
    track_id TEXT,
    feedback_type TEXT NOT NULL,
    original_value TEXT,
    corrected_value TEXT,
    confidence INTEGER,
    reviewer_id TEXT,
    reviewer_role TEXT,
    comment TEXT,
    created_at TEXT NOT NULL,
    payload_json TEXT
);
```

Campos:

- `id`: identificador unico do feedback.
- `event_id`: evento revisado, quando o feedback se refere a um evento existente.
- `frame_id`: frame associado, util para falso negativo, bbox ruim e imagem sensivel.
- `camera_id`: camera de origem, permite metricas por camera.
- `track_id`: track relacionado, quando aplicavel.
- `feedback_type`: tipo da taxonomia, como `false_positive` ou `wrong_attribute`.
- `original_value`: valor original do sistema.
- `corrected_value`: valor corrigido pelo operador.
- `confidence`: confianca do revisor ou severidade, em escala definida pelo produto.
- `reviewer_id`: usuario que fez a revisao.
- `reviewer_role`: perfil do usuario no momento da revisao.
- `comment`: observacao livre.
- `created_at`: timestamp UTC.
- `payload_json`: dados extras, como bbox corrigida, IDs comparados, ponto georreferenciado corrigido, motivo ou flags de privacidade.

Indices recomendados para o MVP:

```sql
CREATE INDEX idx_feedback_event_id ON feedback_annotations(event_id);
CREATE INDEX idx_feedback_camera_id ON feedback_annotations(camera_id);
CREATE INDEX idx_feedback_track_id ON feedback_annotations(track_id);
CREATE INDEX idx_feedback_type ON feedback_annotations(feedback_type);
CREATE INDEX idx_feedback_created_at ON feedback_annotations(created_at);
```

## Modelo De Dados Para Producao

Em producao, a recomendacao e migrar para PostgreSQL/PostGIS com JSONB e versionamento explicito.

Tabelas sugeridas:

- `feedback_annotations`: anotacoes individuais por evento, frame, track ou alerta.
- `feedback_sessions`: sessoes de revisao, com operador, duracao, filtros aplicados e lote revisado.
- `annotation_audit_log`: trilha imutavel de criacao, edicao, exclusao logica e exportacao.
- `dataset_items`: itens aprovados para dataset, com split, versao e referencia de imagem/crop.
- `model_versions`: modelos treinados ou configuracoes de regra, com artefato, parametros e status.
- `model_evaluations`: resultados de avaliacao por classe, camera, periodo e dataset.
- `camera_quality_metrics`: metricas agregadas por camera e janela temporal.
- `privacy_actions`: anonimizacao, blur, retencao reduzida e restricoes aplicadas.

Ideias de versionamento:

- Toda inferencia deve guardar `model_version`.
- Todo feedback deve guardar versao do evento original.
- Todo dataset deve ter `dataset_version`.
- Toda avaliacao deve comparar uma versao candidata contra baseline.
- Deploy deve registrar quem aprovou, quando aprovou e qual metrica justificou a troca.
- Rollback deve ser possivel para modelo, thresholds e regras de alerta.

## Endpoints Sugeridos

Endpoints futuros, sem implementacao nesta etapa:

### `POST /events/{event_id}/feedback`

Cria feedback para um evento. Deve validar permissao do usuario, salvar auditoria e retornar o feedback criado.

### `GET /events/{event_id}/feedback`

Lista feedbacks associados a um evento. Ajuda a mostrar historico de revisao e conflitos.

### `GET /feedback`

Busca feedbacks por periodo, camera, tipo, revisor, track, classe ou status de curadoria.

### `PATCH /feedback/{feedback_id}`

Atualiza comentario, valor corrigido, status ou confianca. Deve gerar auditoria e preservar historico.

### `GET /feedback/stats`

Retorna metricas agregadas: falsos positivos por camera, tipos mais corrigidos, tempo medio de revisao, cameras problematicas e atributos com maior erro.

### `POST /feedback/export-dataset`

Gera um pacote de dataset a partir de feedbacks curados. Deve registrar filtros, versao, responsavel e destino do artefato.

### `GET /model-evaluations`

Lista avaliacoes de modelos e regras. Deve permitir comparar baseline e candidato por classe, camera, horario e metrica.

## Active Learning

Active learning e a estrategia de escolher automaticamente quais eventos merecem revisao humana primeiro.

O Kaelis Argos deve priorizar eventos com maior valor de aprendizado ou maior risco operacional:

- Baixa confianca do YOLO.
- Conflito entre tracking e deteccao.
- Objetos em areas criticas.
- Eventos que geraram alerta.
- Cameras com alta taxa de erro.
- Objetos raros.
- Atributos visuais incertos.
- Geolocalizacao duvidosa.
- Mudanca brusca de distribuicao por camera.
- Tracks fragmentados ou com muitos ID switches.
- Eventos perto do threshold de confianca.
- Detecoes com bbox muito pequena, cortada ou ocluida.
- Discrepancia entre cameras proximas ou horarios semelhantes.

Exemplo de score de prioridade:

```text
review_priority =
  uncertainty_score
  + alert_weight
  + camera_error_rate
  + rare_class_weight
  + attribute_uncertainty
  + geolocation_uncertainty
```

Esse score deve apenas ordenar a fila de revisao. A decisao final continua humana.

## Metricas De Qualidade

Metricas recomendadas:

- Precisao por classe: proporcao de deteccoes validadas como corretas.
- Recall estimado: falsos negativos encontrados por revisao amostral.
- Falso positivo por camera: taxa de `false_positive` por camera.
- Falso positivo por tipo de objeto: classes mais problemáticas.
- Eventos por hora: volume e variacao temporal.
- Taxa de correcao humana: percentual de eventos revisados que tiveram correcao.
- Acuracia de atributos visuais: cor/atributo aceito versus corrigido.
- Erro medio de geolocalizacao: distancia entre ponto estimado e ponto corrigido.
- Fragmentacao de tracks: numero de tracks para o mesmo objeto provavel.
- ID switches: associacoes indevidas entre objetos diferentes.
- Tempo medio de revisao: eficiencia operacional.
- Taxa de alertas uteis: alertas corretos sobre total de alertas revisados.
- Qualidade por camera: score agregado de deteccao, imagem, alertas e feedback.
- Taxa de imagem ruim: eventos marcados como `camera_issue` ou baixa qualidade.
- Taxa de sensibilidade: eventos marcados como `privacy_sensitive`.

As metricas devem ser segmentadas por camera, periodo, classe, versao do modelo e condicao operacional quando possivel.

## Dataset Interno

O feedback humano pode virar dataset interno, desde que passe por curadoria.

Itens possiveis:

- Crops de objetos.
- Frames completos.
- Bbox original e bbox corrigida.
- Classe original e classe corrigida.
- Atributos visuais originais e corrigidos.
- Camera e local.
- Horario e periodo do dia.
- Condicoes de iluminacao.
- Versao do modelo.
- Feedback do operador.
- Status de privacidade.
- Split de treino, validacao ou teste.

Separacao recomendada:

- `train`: exemplos usados para treinamento.
- `validation`: exemplos usados para ajuste de parametros.
- `test`: exemplos bloqueados para avaliacao final.

Cuidados:

- Nao misturar frames muito parecidos entre treino e teste.
- Separar por camera ou por periodo quando for avaliar generalizacao.
- Remover ou anonimizar casos sensiveis.
- Registrar licenca, origem e politica de retencao.
- Manter datasets versionados e reproduziveis.

## Treinamento E Avaliacao

Processo seguro recomendado:

```text
Coletar feedback
  -> curar dados
  -> limpar anotacoes inconsistentes
  -> separar dataset
  -> treinar/fine-tunar fora de producao
  -> avaliar contra baseline
  -> aprovar apenas se melhorar metricas definidas
  -> registrar versao
  -> deploy controlado
  -> monitorar regressao
  -> permitir rollback
```

Regras:

- Treinamento nunca deve ocorrer diretamente em producao.
- Uma versao candidata deve ser avaliada em dataset fixo antes do deploy.
- A aprovacao deve considerar precision/recall por classe, falso positivo por camera, impacto em alertas e custo computacional.
- Melhorar uma classe nao deve degradar outra de forma inaceitavel.
- O deploy pode ser gradual por camera, grupo de cameras ou janela de horario.
- Rollback deve ser simples e documentado.

Nao e necessario implementar treinamento agora. Esta secao define o processo para quando o MVP evoluir.

## Uso Para Regras E Alertas

Feedback humano tambem melhora regras e operacao, nao apenas modelo YOLO.

Usos recomendados:

- Ajustar thresholds de confianca por camera.
- Ajustar thresholds por classe.
- Refinar regras de alerta.
- Configurar zonas criticas no mapa.
- Ajustar tempo de permanencia.
- Melhorar associacao de tracks.
- Ajustar sensibilidade por camera.
- Priorizar cameras com maior erro.
- Reduzir alertas falsos repetitivos.
- Criar perfis operacionais por horario ou condicao de luz.

Exemplo:

```text
Camera A tem muitos falsos positivos de pessoa a noite
  -> aumentar threshold noturno
  -> reduzir prioridade de atributos visuais nessa camera
  -> enviar camera para fila de calibracao
```

## Privacidade, LGPD E Limites

Esta evolucao deve ser conservadora do ponto de vista de privacidade.

Limites claros:

- Nao implementar reconhecimento facial.
- Nao identificar pessoa nominalmente.
- Nao usar biometria.
- Nao usar OCR de placa neste guia.
- Nao prometer reidentificacao garantida entre cameras.
- Usar termos como `atributos visuais observaveis` e `continuidade provavel`.
- Tratar `track_id` como continuidade tecnica provavel, nao identidade.
- Manter logs de auditoria.
- Respeitar retencao configuravel.
- Permitir anonimizacao/blur.
- Restringir acesso por perfil.
- Bloquear exportacao de dataset para usuarios sem permissao.
- Registrar motivo e responsavel por qualquer exportacao.

Casos marcados como `privacy_sensitive` devem seguir politica especial:

- acesso restrito;
- retencao reduzida quando aplicavel;
- anonimizacao antes de exportacao;
- exclusao de treino automatico;
- auditoria reforcada.

## Roadmap Recomendado

### Fase 1 - MVP

- Salvar feedback simples por evento.
- Botões de `correto`, `falso positivo` e `classe errada`.
- Campo de comentario.
- Auditoria de criacao de feedback.
- Estatisticas basicas por camera, classe e tipo de feedback.
- Filtro `eventos sem feedback` na revisao.

### Fase 2 - Revisao Avancada

- Correcao de atributos visuais.
- Correcao de track.
- Marcacao de geolocalizacao errada.
- Comparacao entre dois eventos como mesmo objeto ou objeto diferente.
- Exportacao de dataset curado.
- Fila de curadoria para feedbacks conflitantes.

### Fase 3 - Active Learning

- Fila automatica de eventos duvidosos.
- Painel de qualidade por camera.
- Ranking de eventos para revisao.
- Priorizacao por alerta, baixa confianca e camera com alta taxa de erro.
- Metricas por modelo, camera, classe e periodo.

### Fase 4 - Treinamento

- Dataset versionado.
- Avaliacao de modelo.
- Model registry.
- Comparacao baseline versus candidato.
- Deploy controlado.
- Monitoramento pos-deploy.
- Rollback de modelo, threshold e regra.

## Principios De Implementacao

- Feedback humano e dado critico, nao anotacao descartavel.
- Tudo que altera interpretacao de evento deve ser auditado.
- Curadoria vem antes de treinamento.
- Privacidade vem antes de conveniencia operacional.
- Modelos candidatos devem provar melhoria antes de producao.
- O sistema deve preferir incerteza explicita a confianca falsa.
