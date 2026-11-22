# Revisao Historica Com Feedback Humano

## Escopo Implementado

Esta entrega adiciona uma camada de feedback humano sobre a revisao historica do Kaelis Argos. O objetivo e permitir que operadores validem eventos salvos, corrijam classificacoes, sinalizem problemas operacionais e preparem um conjunto curado para evolucoes futuras do pipeline de deteccao.

## Backend

Novas tabelas:

- `feedback_annotations`: anotacoes de revisores vinculadas a evento, frame, camera e track.
- `feedback_dataset_exports`: historico de exportacoes curadas.

Novos endpoints:

- `GET /review/events`: busca historica priorizada por incerteza, filtros visuais e estado de feedback.
- `GET /review/events/{event_id}`: detalhe do evento com resumo de feedback.
- `GET /review/next`: proximo item sem feedback para revisao ativa.
- `POST /review/events/{event_id}/mark`: marcacao rapida de relevancia.
- `POST /events/{event_id}/feedback`: cria anotacao humana auditada.
- `GET /events/{event_id}/feedback`: lista feedbacks de um evento.
- `GET /feedback`: lista anotacoes para curadoria.
- `PATCH /feedback/{feedback_id}`: atualiza anotacao, status de curadoria ou exclusao logica.
- `GET /feedback/stats`: metricas agregadas por tipo, camera e revisor.
- `POST /feedback/export-dataset`: exporta metadados curados para `datasets/<versao>/metadata.json`.

Tipos de feedback suportados:

- `detection_correct`
- `false_positive`
- `wrong_class`
- `event_relevant`
- `event_irrelevant`
- `alert_correct`
- `alert_false`
- `camera_issue`
- `privacy_sensitive`
- `false_negative`
- `bad_bbox`
- `wrong_attribute`
- `wrong_track_association`
- `same_object`
- `different_object`
- `bad_geolocation`

## Frontend

A aba `Revisao` agora inclui:

- filtros por confianca maxima, estado de feedback, tipo de feedback e status de curadoria;
- fila priorizada de eventos revisaveis;
- painel de evento selecionado;
- botoes rapidos para feedback humano;
- campos para classe correta, motivo de camera, correcao livre e comentario;
- atalhos ativos apenas na revisao: `C`, `F`, `K`, `A`, `T`, `G`, `S`.

## Privacidade E Limites

Itens marcados como `privacy_sensitive` recebem `curation_status=privacy_blocked` e sao excluidos da exportacao automatica por padrao.

Esta feature nao implementa:

- reconhecimento facial;
- biometria;
- OCR de placas;
- identificacao nominal;
- treinamento automatico em producao.

O endpoint de exportacao cria um dataset de metadados e referencias aos frames. O uso para treinamento ou avaliacao deve ocorrer em pipeline separado, com revisao humana e politica de privacidade apropriadas.

## Proximos Passos

- Adicionar tela administrativa dedicada para curadoria em lote.
- Gerar pacotes YOLO/COCO opcionais a partir dos metadados.
- Criar metricas de drift por camera, horario e classe.
- Separar anotacoes de atributo, bbox e geolocalizacao em validadores visuais dedicados.
- Adicionar testes automatizados de API para o fluxo completo de feedback.
