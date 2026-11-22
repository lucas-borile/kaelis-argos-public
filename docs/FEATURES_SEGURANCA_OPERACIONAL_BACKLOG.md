# Backlog de Features para Operacao de Seguranca

Este documento lista features recomendadas para tornar o Kaelis Argos mais util para vigilantes patrimoniais, equipes de seguranca, portaria remota, supervisores e profissionais de monitoramento.

O foco e transformar o Argos em uma ferramenta operacional: menos painel tecnico, mais fila de atencao, resposta rapida, registro de ocorrencias, auditoria e melhoria continua da IA.

## Criterios de priorizacao

- **Ganho operacional**: quanto reduz tempo de resposta, falso alarme, retrabalho ou perda de evento importante.
- **Esforco**: impacto em backend, frontend, banco, IA, permissoes, testes e UX.
- **Risco**: chance de gerar ruido operacional, falso positivo, carga excessiva ou complexidade para o operador.
- **Dependencias**: componentes que precisam existir antes da feature ficar boa.

## Ordem sugerida de implementacao

| Ordem | Feature | Ganho | Esforco | Prioridade |
|---:|---|---|---|---|
| 1 | Fila de Atencao | Muito alto | Medio | Alta |
| 2 | Confirmacao rapida de alerta | Muito alto | Baixo/medio | Alta |
| 3 | Ocorrencias com linha do tempo | Alto | Medio | Alta |
| 4 | Classificacao de ocorrencia | Alto | Baixo/medio | Alta |
| 5 | Relatorio de turno | Muito alto | Medio | Alta |
| 6 | Painel de cameras problematicas | Alto | Medio | Alta |
| 7 | Regras por horario e contexto | Muito alto | Medio | Alta |
| 8 | Modo ronda virtual | Alto | Medio | Media/alta |
| 9 | Escalonamento automatico | Alto | Medio | Media/alta |
| 10 | Busca operacional simples | Alto | Medio/alto | Media |
| 11 | Modo portaria remota | Muito alto | Alto | Media |
| 12 | Evidencia exportavel | Alto | Medio | Media |
| 13 | Zonas por tipo de risco | Alto | Medio | Media |
| 14 | Status "Estou atendendo" | Alto | Baixo | Alta |
| 15 | Deteccao de chuva/noite/baixa visibilidade | Alto | Medio | Media |
| 16 | Permissoes por cliente/unidade | Muito alto | Alto | Media |
| 17 | Playbook de ocorrencia | Alto | Medio | Media |
| 18 | Anotacoes por ocorrencia | Alto | Baixo/medio | Media |
| 19 | Modelos de IA por camera/cenario | Alto | Alto | Media |
| 20 | Assistente de melhoria da IA | Alto | Alto | Media |

---

## 1. Fila de Atencao

**Status:** MVP implementado.

### Descricao

Tela operacional que mostra, em ordem de prioridade, o que precisa de atencao agora. Em vez de o operador procurar eventos no mapa, o Argos entrega uma fila priorizada.

Exemplos:

- Pessoa parada ha 4 minutos no portao lateral.
- Moto circulando fora do horario.
- Camera da garagem offline.
- Movimento em area restrita.
- Frame congelado em camera critica.

### Usuarios beneficiados

- Vigilante patrimonial.
- Operador de CFTV.
- Portaria remota.
- Supervisor de seguranca.

### Requisitos funcionais

- Agregar alertas, cameras offline, eventos temporais e regras de geofence.
- Calcular prioridade por severidade, horario, tipo de zona, duracao, confianca e status da camera.
- Permitir assumir atendimento.
- Permitir concluir, ignorar ou escalar item.
- Atualizar em tempo real via WebSocket.

### Requisitos tecnicos

- Criar entidade logica `attention_items` ou montar visao derivada de eventos existentes.
- Integrar com `geofence_alerts`, `events`, status de camera e tracking temporal.
- Criar endpoint de listagem priorizada.
- Criar endpoint para assumir e encerrar atendimento.
- Registrar auditoria.

### MVP implementado

- Fila derivada de eventos temporais abertos e cameras com falha, pausadas ou stale.
- Estado operacional persistido em `attention_item_states`.
- Endpoint `GET /api/attention`.
- Endpoint `PATCH /api/attention/{item_key}` para abrir, assumir, resolver ou ignorar.
- Auditoria em alteracoes da fila.
- Broadcast WebSocket `attention_updated`.
- Painel "Fila de atencao" na Visao Geral.
- Acoes rapidas: abrir camera, assumir e resolver.
- Testes automatizados cobrindo listagem, assumir e resolver.

### Dificuldades

- Definir uma pontuacao de prioridade que nao gere ruido.
- Evitar duplicidade entre alerta, evento e camera problematica.
- Manter a fila atualizada sem sobrecarregar o frontend.

### Riscos

- Se a priorizacao for ruim, o operador perde confianca na fila.
- Muitos alertas podem criar fadiga operacional.

---

## 2. Confirmacao Rapida de Alerta

**Status:** MVP implementado.

### Descricao

Ao abrir um alerta, o operador ve botoes grandes e objetivos para registrar a decisao.

Exemplos:

- Confirmar risco.
- Falso alarme.
- Monitorar.
- Acionar responsavel.
- Ignorar com motivo.

### Requisitos funcionais

- Mostrar frame, camera, horario, regra acionada e objeto detectado.
- Permitir decisao em um clique.
- Permitir comentario opcional.
- Atualizar status do alerta/ocorrencia.
- Alimentar feedback humano para IA.

### Requisitos tecnicos

- Reaproveitar `feedback_annotations`.
- Criar status operacional de alerta.
- Registrar auditoria.
- Atualizar UI em tempo real para outros operadores.

### MVP implementado

- Acoes rapidas na Fila de Atencao:
  - Confirmar risco.
  - Falso alarme.
  - Monitorar.
  - Acionar responsavel.
  - Ignorar.
- Endpoint `POST /api/attention/{item_key}/confirm`.
- Persistencia de `quick_action` e `confirmed_at` em `attention_item_states`.
- Mapeamento operacional:
  - `confirm_risk`, `monitor` e `notify_responsible` colocam o item em atendimento.
  - `false_alarm` resolve o item.
  - `ignore` ignora/remove da fila padrao.
- Auditoria `attention.quick_confirm`.
- Broadcast WebSocket `attention_updated`.
- Testes automatizados para risco confirmado e falso alarme.

### Dificuldades

- Criar taxonomia simples sem perder informacao util.
- Evitar que feedback operacional seja confundido com anotacao tecnica de dataset.

### Riscos

- Operadores podem marcar rapidamente sem revisar se a UI incentivar cliques mecanicos.

---

## 3. Ocorrencias com Linha do Tempo

**Status:** MVP implementado.

### Descricao

Transformar alertas importantes em ocorrencias com historico completo.

Exemplo de linha do tempo:

- 22:41 alerta gerado.
- 22:42 operador visualizou.
- 22:43 contato realizado.
- 22:45 pessoa deixou o local.
- 22:47 ocorrencia encerrada.

### Requisitos funcionais

- Abrir ocorrencia a partir de alerta, frame, camera ou manualmente.
- Vincular frames, eventos, cameras e comentarios.
- Registrar responsavel pelo atendimento.
- Encerrar com classificacao.
- Permitir exportacao futura.

### Requisitos tecnicos

- Criar tabelas `incidents` e `incident_timeline`.
- Criar endpoints CRUD.
- Integrar com auditoria.
- Integrar com chat/mencoes e frames.

### MVP implementado

- Tabelas `incidents` e `incident_timeline`.
- Endpoint `POST /api/attention/{item_key}/incident` para abrir ocorrencia a partir da Fila de Atencao.
- Endpoint `GET /api/incidents` para listar ocorrencias.
- Endpoint `GET /api/incidents/{incident_id}` com timeline.
- Endpoint `POST /api/incidents/{incident_id}/timeline` para adicionar notas/eventos.
- Endpoint `PATCH /api/incidents/{incident_id}` para atualizar ou encerrar.
- Ao abrir ocorrencia, o item da Fila de Atencao passa para atendimento.
- Ao encerrar ocorrencia, o item da Fila de Atencao e resolvido.
- Auditoria `incident.create`, `incident.timeline` e `incident.update`.
- Broadcast WebSocket `incident_created` e `incident_updated`.
- Painel "Ocorrencias abertas" na Visao Geral.
- Acesso via menu de tres pontos da Fila de Atencao.
- Testes automatizados cobrindo abertura, timeline e encerramento.

### Dificuldades

- Modelar ocorrencias sem duplicar os eventos existentes.
- Sincronizar status entre alerta, ocorrencia e fila de atencao.

### Riscos

- Complexidade de fluxo se a ocorrencia exigir muitos campos obrigatorios.

---

## 4. Classificacao de Ocorrencia

### Descricao

Ao concluir uma ocorrencia, o operador escolhe uma categoria padronizada.

Exemplos:

- Ronda normal.
- Morador/prestador.
- Suspeita real.
- Falso positivo.
- Problema de camera.
- Acionado responsavel.
- Sem acao necessaria.

### Requisitos funcionais

- Lista configuravel de categorias.
- Comentario opcional.
- Vinculo com relatorios e metricas.
- Diferenciar classificacao operacional de feedback tecnico de IA.

### Requisitos tecnicos

- Campo `resolution_type` em ocorrencias.
- Tabela opcional de categorias configuraveis.
- Dashboard de estatisticas por categoria.

### Dificuldades

- Criar categorias curtas e universais para varios tipos de cliente.

### Riscos

- Categorias demais reduzem consistencia.

---

## 5. Relatorio de Turno

### Descricao

Gerar resumo do turno para vigilantes e supervisores.

Conteudo sugerido:

- Ocorrencias atendidas.
- Alertas criticos.
- Falsos alarmes.
- Cameras offline.
- Areas mais acionadas.
- Pendencias para proximo turno.
- Operadores ativos.

### Requisitos funcionais

- Selecionar periodo e operador.
- Gerar resumo automatico.
- Permitir anotacao final manual.
- Exportar PDF/HTML.

### Requisitos tecnicos

- Consultas agregadas sobre ocorrencias, alertas, cameras e auditoria.
- Renderizador HTML/PDF.
- Permissao para supervisor/admin.

### Dificuldades

- Construir resumo util sem ficar longo demais.
- Garantir que eventos nao revisados fiquem marcados como pendentes.

### Riscos

- Relatorios incompletos podem gerar falsa sensacao de cobertura.

---

## 6. Painel de Cameras Problematicas

### Descricao

Tela que mostra cameras com sinais de problema operacional.

Indicadores:

- Offline frequente.
- Baixa confianca media.
- Muitos falsos positivos.
- Muitos falsos negativos.
- Imagem escura.
- Frame congelado.
- Sem eventos ha muito tempo.
- Latencia alta.

### Requisitos funcionais

- Ranking de cameras por risco operacional.
- Filtros por local, cliente e periodo.
- Sugestoes de acao.

### Requisitos tecnicos

- Metricas por camera.
- Integracao com frame change gate, feedback e status de captura.
- Endpoint de resumo de saude.

### Dificuldades

- Detectar problema real sem confundir com camera de area pouco movimentada.

### Riscos

- Pode gerar alarmes falsos de camera ruim se nao houver contexto de uso.

---

## 7. Regras por Horario e Contexto

### Descricao

Regras prontas para cenarios comuns de seguranca patrimonial.

Exemplos:

- Pessoa no estacionamento entre 22h e 6h.
- Moto parada na frente por mais de 2 minutos.
- Pessoa em area tecnica sem autorizacao.
- Camera critica offline por mais de 60 segundos.
- Movimento em portao apos fechamento.

### Requisitos funcionais

- Templates de regra.
- Horarios por dia da semana.
- Severidade.
- Cooldown.
- Canais de notificacao.

### Requisitos tecnicos

- Expandir `geofence_rules`.
- Criar presets no frontend.
- Integrar com tracking temporal.

### Dificuldades

- Criar templates flexiveis sem virar formulario complexo.

### Riscos

- Regras mal configuradas podem gerar muitos alertas.

---

## 8. Modo Ronda Virtual

### Descricao

Sequencia automatica de cameras para conferencia periodica.

Exemplo:

- Portao principal.
- Garagem.
- Fundos.
- Area tecnica.

O operador confirma:

- Sem alteracao.
- Pessoa vista.
- Veiculo suspeito.
- Problema de imagem.

### Requisitos funcionais

- Criar rotas de ronda.
- Rodar sequencia em tela cheia.
- Checklist por camera.
- Gerar registro de ronda.

### Requisitos tecnicos

- Tabelas `patrol_routes`, `patrol_steps`, `patrol_runs`.
- Integração com frames/processamento sob demanda.
- Relatorio por ronda.

### Dificuldades

- Sincronizar camera lenta/offline sem travar a ronda.

### Riscos

- Se o fluxo for demorado, operador pode abandonar a ronda.

---

## 9. Escalonamento Automatico

### Descricao

Se um alerta nao for visto ou concluido no prazo, o Argos aciona outro nivel.

Exemplos:

- 30 segundos sem visualizacao: notificar supervisor.
- 2 minutos sem encerramento: escalar para gerente.
- Camera critica offline: alerta imediato.

### Requisitos funcionais

- Regras de escalonamento por severidade.
- Destinatarios por cliente/unidade.
- Registro de quem foi notificado.

### Requisitos tecnicos

- Worker periodico.
- Integracao com Telegram, WebSocket e futuramente email/WhatsApp.
- Estados de atendimento.

### Dificuldades

- Evitar notificacoes repetidas.
- Garantir confiabilidade do worker.

### Riscos

- Escalonamento excessivo pode causar fadiga.

---

## 10. Busca Operacional Simples

### Descricao

Busca orientada a linguagem operacional, sem exigir filtros tecnicos.

Exemplos:

- Pessoa com roupa escura.
- Carro branco.
- Moto perto do portao.
- Eventos da garagem ontem a noite.

### Requisitos funcionais

- Campo de busca simples.
- Chips/filtros sugeridos.
- Resultados com frame, camera, horario e confianca.

### Requisitos tecnicos

- Inicialmente parser estruturado simples.
- Futuramente busca semantica ou assistente.
- Indexar atributos, classe, camera, geofence e horario.

### Dificuldades

- Interpretar texto livre com baixa ambiguidade.

### Riscos

- Prometer busca natural demais antes do backend suportar.

---

## 11. Modo Portaria Remota

### Descricao

Interface enxuta para operadores que monitoram varios clientes/unidades.

Informacoes principais:

- Cliente/local.
- Cameras criticas.
- Alertas ativos.
- Botao de contato.
- Historico rapido.
- Status de camera/interfone.

### Requisitos funcionais

- Separar unidades/clientes.
- Permissoes por unidade.
- Fila de atencao agrupada por cliente.
- Troca rapida entre locais.

### Requisitos tecnicos

- Entidades `clients` ou `sites`.
- Vinculo camera/geofence/usuario/site.
- Permissoes por escopo.

### Dificuldades

- Mudar o modelo atual centrado em cameras para modelo multi-unidade.

### Riscos

- Alto impacto em permissoes e consultas existentes.

---

## 12. Evidencia Exportavel

### Descricao

Exportar pacote de evidencia a partir de uma ocorrencia.

Conteudo:

- Frames.
- Horarios.
- Cameras.
- Mapa/localizacao.
- Acoes tomadas.
- Operador responsavel.
- Linha do tempo.

### Requisitos funcionais

- Botao "Exportar evidencia".
- Escolher incluir imagens, timeline e comentarios.
- Gerar PDF/HTML e pacote zip opcional.

### Requisitos tecnicos

- Renderizador de relatorio.
- Controle de acesso.
- Mascaramento futuro de privacidade.

### Dificuldades

- Garantir consistencia e rastreabilidade juridica.

### Riscos

- Expor imagens sensiveis sem controle adequado.

---

## 13. Zonas por Tipo de Risco

### Descricao

Geofences com significado operacional.

Tipos:

- Perimetro.
- Portao.
- Garagem.
- Area tecnica.
- Recepcao.
- Area proibida.
- Rota de ronda.

### Requisitos funcionais

- Escolher tipo da zona.
- Sugerir regras por tipo.
- Filtrar eventos por tipo de zona.

### Requisitos tecnicos

- Campo `zone_type` em geofences.
- UI com presets.
- Regras padrao associadas.

### Dificuldades

- Criar taxonomia simples para varios ambientes.

### Riscos

- Tipos demais podem confundir operadores.

---

## 14. Status "Estou Atendendo"

### Descricao

Permitir que um operador assuma um alerta/ocorrencia.

Exemplo:

`Operador responsável assumiu o alerta às 22:41.`

### Requisitos funcionais

- Botao "Assumir".
- Mostrar operador responsavel.
- Liberar/repassar atendimento.
- Registrar tempo de resposta.

### Requisitos tecnicos

- Campos `assigned_to`, `assigned_at`.
- Evento de auditoria.
- Atualizacao via WebSocket.

### Dificuldades

- Tratar abandono de atendimento.

### Riscos

- Alertas podem ficar presos com operador ausente se nao houver timeout.

---

## 15. Deteccao de Chuva, Noite e Baixa Visibilidade

### Descricao

Detectar condicoes que reduzem assertividade da IA.

Sinais:

- Imagem escura.
- Reflexo forte.
- Chuva provavel.
- Baixa nitidez.
- Contraste ruim.

### Requisitos funcionais

- Indicar condicao no frame/camera.
- Adaptar thresholds ou sugerir revisao.
- Marcar exemplos para dataset.

### Requisitos tecnicos

- Heuristicas de brilho, contraste, blur e reflexo.
- Opcionalmente modelo leve para clima/visibilidade.
- Integracao com Feature Manager.

### Dificuldades

- Chuva pode ser dificil de detectar sem falso positivo.

### Riscos

- Ajuste automatico agressivo pode aumentar falso positivo.

---

## 16. Permissoes por Cliente/Unidade

### Descricao

Controle de acesso para portaria remota e operacoes multi-local.

Exemplos:

- Operador A ve condominio X.
- Supervisor ve todos.
- Cliente ve apenas relatorios.

### Requisitos funcionais

- Criar unidades/clientes.
- Associar usuarios a unidades.
- Filtrar cameras, eventos, frames e relatorios por unidade.

### Requisitos tecnicos

- Modelo RBAC com escopo.
- Migrar endpoints para aplicar filtro de escopo.
- Testes de seguranca por endpoint.

### Dificuldades

- Alto risco de vazamento se algum endpoint esquecer o filtro.

### Riscos

- Requer auditoria forte antes de uso real multi-cliente.

---

## 17. Playbook de Ocorrencia

### Descricao

Passo a passo operacional quando um alerta importante e aberto.

Exemplo:

1. Ver camera ao vivo.
2. Confirmar pessoa/veiculo.
3. Acionar audio/interfone.
4. Registrar ocorrencia.
5. Notificar responsavel.
6. Encerrar com motivo.

### Requisitos funcionais

- Playbooks por tipo de ocorrencia.
- Checklist.
- Registro de etapas concluidas.
- Tempo por etapa.

### Requisitos tecnicos

- Tabelas `playbooks`, `playbook_steps`, `incident_step_logs`.
- UI compacta dentro da ocorrencia.

### Dificuldades

- Criar playbooks flexiveis sem burocratizar atendimento.

### Riscos

- Checklist longo pode atrasar resposta.

---

## 18. Anotacoes por Ocorrencia

### Descricao

Permitir comentarios, mencoes e anexos diretamente em uma ocorrencia.

Exemplos:

- `@operador verificar portao`.
- `#frame:456`.
- `#camera:garagem`.

### Requisitos funcionais

- Comentarios por ocorrencia.
- Mencoes a usuarios, cameras, frames e geofences.
- Historico auditavel.

### Requisitos tecnicos

- Reaproveitar parte do chat existente.
- Criar vinculo `incident_id`.
- Permitir anexar referencias.

### Dificuldades

- Evitar duplicidade entre chat geral e comentarios da ocorrencia.

### Riscos

- Conversas soltas podem esconder informacao critica se nao forem bem organizadas.

---

## 19. Modelos de IA por Camera/Cenario

### Descricao

Permitir salvar modelos candidatos e aplicar modelos especificos em cameras ou grupos.

Exemplos:

- Modelo para pedestres na chuva.
- Modelo para veiculos em estacionamento.
- Modelo leve para Raspberry Pi.

### Requisitos funcionais

- Model registry.
- Salvar candidato, metricas e caminho do `best.pt`.
- Aplicar modelo em camera.
- Aplicar modelo em grupo.
- Rollback.

### Requisitos tecnicos

- Tabela `model_candidates`.
- Endpoints de listar/aplicar/arquivar.
- Integrar com `camera.yolo_model`.
- Validar existencia do arquivo.
- Registrar auditoria.

### Dificuldades

- Gerenciar memoria quando muitas cameras usam modelos diferentes.
- Avaliar qualidade antes de aplicar.

### Riscos

- Modelo ruim pode reduzir deteccao em camera critica.

---

## 20. Assistente de Melhoria da IA

### Descricao

Fluxo guiado para melhorar IA sem exigir conhecimento tecnico.

Etapas sugeridas:

1. Escolher objetivo.
2. Escolher periodo.
3. Revisar frames sugeridos.
4. Desenhar boxes.
5. Exportar dataset.
6. Treinar candidato.
7. Comparar antes/depois.
8. Aplicar em camera ou grupo.

### Requisitos funcionais

- Wizard visual.
- Sugestoes automaticas baseadas em falsos negativos, baixa confianca e clima.
- Validacao antes do treino.
- Comparacao de metricas.
- Aplicacao controlada.

### Requisitos tecnicos

- Integrar anotacao, dataset YOLO, training jobs e model registry.
- Criar avaliacao automatica.
- Criar tela dedicada "Melhoria da IA".

### Dificuldades

- Unificar varias partes complexas em fluxo simples.
- Exigir dados suficientes antes de treinar.

### Riscos

- Treinar com dataset pequeno ou enviesado pode piorar o modelo.

---

## Requisitos transversais

### UX

- Separar modos: Operar, Investigar, Melhorar IA, Administrar.
- Mostrar uma acao principal por tela.
- Usar nomes humanos em vez de nomes tecnicos internos.
- Exibir consequencia antes de acoes sensiveis.
- Ocultar opcoes avancadas por padrao.

### Seguranca

- Auditoria em todas as acoes operacionais.
- Permissoes por papel e, futuramente, por unidade.
- Controle rigoroso de acesso a frames e evidencias.
- Evitar exportacao de imagens sensiveis sem permissao.

### Performance

- Evitar consultas pesadas no dashboard ao vivo.
- Usar paginacao em eventos, frames e ocorrencias.
- Manter exportacao e treino em jobs background.
- Medir impacto de modelos diferentes por camera.

### IA

- Feedback operacional nao deve alterar modelo automaticamente.
- Dataset precisa de curadoria.
- Candidatos devem ser avaliados antes do deploy.
- Rollback deve estar disponivel.

### Auditoria

- Registrar quem viu, assumiu, alterou, exportou, aplicou modelo ou encerrou ocorrencia.
- Preservar linha do tempo imutavel para evidencias.

## MVP recomendado

Para maior ganho com menor risco, o primeiro pacote deveria conter:

1. Fila de Atencao.
2. Status "Estou atendendo".
3. Confirmacao rapida de alerta.
4. Classificacao de ocorrencia.
5. Ocorrencias simples com linha do tempo.
6. Relatorio de turno basico.
7. Painel de cameras problematicas.

Esse pacote ja muda o Argos de um sistema de deteccao para uma plataforma operacional de seguranca.
