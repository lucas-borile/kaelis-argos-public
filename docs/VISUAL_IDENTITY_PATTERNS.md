# Kaelis Argos Visual Identity Patterns

Guia de identidade visual para manter a interface do Kaelis Argos consistente, reconhecivel e facil de replicar em novas telas.

A direcao visual e inspirada em interfaces operacionais de defesa, observacao remota e analise critica: escura, precisa, densa, tecnica e legivel. Nao copiar layouts, assets, textos ou composicoes de outras marcas. A identidade do Argos deve parecer propria: urbana, auditavel, geoespacial e orientada a decisao.

## Principios

- Operacional antes de decorativo: cada elemento deve ajudar o operador a entender estado, prioridade ou acao.
- Preto como base: usar preto real e grafite neutro como estrutura; reservar cores para foco, estado e semantica operacional.
- Linhas finas e geometria contida: bordas de 1px, raio pequeno e paineis compactos.
- Estados claros: online, offline, alerta, revisao, historico e auditoria devem ser distinguiveis rapidamente.
- Densidade com respiro: informacao pode ser densa, mas organizada em grupos previsiveis.
- Tipografia funcional: sans para leitura, monospace para IDs, metricas, rotulos e dados tecnicos.
- Sem estetica promocional: evitar herois, cards decorativos grandes, gradientes chamativos ou ilustracoes ornamentais.

## Tokens De Cor

Base:

```css
--surface-0: #000000; /* fundo principal */
--surface-1: #050607; /* paineis profundos */
--surface-2: #0b0d10; /* paineis e blocos */
--surface-3: #12161b; /* hover e elevacao leve */
--surface-4: #1a2027; /* destaque contido */
```

Linhas:

```css
--line-1: #20252c; /* divisores e bordas comuns */
--line-2: #343b45; /* borda ativa/hover */
```

Texto:

```css
--text-1: #f2f5f8; /* texto primario */
--text-2: #b8c0ca; /* texto secundario */
--text-3: #747d88; /* metadados, labels */
```

Acentos:

```css
--brand: #9cc7ff;        /* foco, navegação ativa, camera saudavel */
--brand-strong: #d7e8ff; /* texto ativo e destaque */
--neutral-accent: #9aa4b2; /* estrutura, titulos tecnicos */
--amber: #d6a84f;        /* trajetos, atenção e revisão */
--green: #4ade80;        /* pessoa/ok */
--blue: #38bdf8;         /* veiculos */
--red: #fb7185;          /* moto/offline/erro */
```

Uso semantico:

- Pessoas: `--green`
- Carros, onibus, caminhoes: `--blue`
- Motos: `--red`
- Cameras saudaveis: `--brand`
- Cameras offline/erro: `--red`
- Trajetos de revisao: `--amber`
- Foco/interacao: `--brand` ou `--brand-strong`

## Tipografia

Familias:

```css
--sans: Inter, "Segoe UI", Arial, sans-serif;
--mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
```

Regras:

- Use `--sans` para nomes, descricoes, listas e formularios.
- Use `--mono` para IDs, horarios, rotulos compactos, metricas e navegacao tecnica.
- Labels devem ser pequenos, em uppercase, com letter-spacing positivo.
- Evitar fontes gigantes dentro de paineis operacionais.

Escala sugerida:

```css
metric-sm: 10px;
body-sm: 11px;
body: 12px;
panel-title: 16px;
status-value: 22px;
```

## Marca

Wordmark:

- Texto: `KAELIS ARGOS`
- Peso: 900
- Tracking: `0.12em`
- Cor: `--text-1`
- Ao lado do nome, usar uma badge de versao curta, como `BETA`, com borda `--brand` discreta.
- Nao usar tagline fixa no header. O estado/versao deve ser mais util que uma frase de marca.

Símbolo:

- Quadrado de 22px.
- Borda `--brand`.
- Fundo `--surface-2`.
- Cruz/reticulo interno em `--brand`.
- Sombra leve azul-fria.

O símbolo deve remeter a mira/cartografia/sensor, sem parecer arma, brasao ou copia de outra marca.

## Layout

Estrutura principal:

```text
Header fixo
  -> marca
  -> navegacao principal
  -> metricas rápidas
  -> usuario/hora
Mapa full-height
Painel lateral direito
  -> cabecalho do modulo
  -> controles do modulo
  -> listas/formularios/status
```

Regras:

- O mapa permanece como superficie principal.
- A lateral direita e o cockpit operacional.
- Modulos principais: `Ao vivo`, `Revisao`, `Status`, `Log completo`.
- Evitar empilhar todos os recursos em uma unica aba.
- Nao colocar cards dentro de cards. Use paineis, secoes e listas.

## Navegacao

Navegacao principal:

- Fundo `--surface-1`.
- Borda `--line-1`.
- Agrupar itens por segmento operacional, sem adicionar clique extra:
  - `Operar`: Ao vivo, Ambientes
  - `Equipe`: Chat
  - `Investigar`: Revisao, Log
  - `Configurar`: Cameras, Geofences
  - `Sistema`: Status
- Grupo ativo deve receber destaque discreto, apenas para orientar contexto.
- Item ativo com fundo `#101722`, texto `--brand-strong` e linha inferior interna.
- Labels curtos. Evitar nomes longos na barra superior.
- Em telas estreitas, esconder labels de grupo e manter apenas os botoes.

Subnavegacao:

- Usar apenas dentro de um modulo, como `Cameras` e `Nova camera`.
- Deve ter menor peso visual que a navegacao principal.

## Paineis

Cabecalho de painel:

```css
background:
  linear-gradient(90deg, rgba(156, 199, 255, .08), transparent 46%),
  var(--surface-2);
border-bottom: 1px solid var(--line-1);
```

Titulo:

- `16px`
- `--text-1`
- Peso 850

Subtitulo:

- `12px`
- `--text-3`
- Linha 1.35

Secoes:

- Padding 16px.
- Divisor `--line-1`.
- Titulo `h2` em `--mono`, 11px, uppercase, `--neutral-accent`.

## Botões

Botao padrao:

```css
height: 32px;
border: 1px solid var(--line-2);
border-radius: 5px;
background: var(--surface-3);
color: var(--text-2);
font-weight: 700;
```

Hover:

```css
border-color: var(--brand);
color: var(--brand-strong);
```

Botao destrutivo:

- Texto `--red`
- Fundo escuro avermelhado `#211018`
- Borda `#4b2530`

Botao de edicao/acao tecnica:

- Texto `--brand`
- Fundo `#101722`
- Borda `#415066`

## Formularios

Inputs e selects:

- Fundo `#07090c`
- Borda `--line-2`
- Texto `--text-1`
- Raio 5px
- Foco com borda `--brand` e sombra sutil `rgba(156, 199, 255, .12)`
- Placeholders em cinza baixo contraste, proximo de `#5f6874`.

Labels:

- Uppercase.
- `--mono`.
- 10px.
- `--text-3`.

Inputs devem manter altura minima de 32px para consistencia com botoes.

Estrutura:

- Formularios operacionais devem ser agrupados em secoes com `.form-section`.
- A primeira secao deve conter apenas os campos essenciais para concluir a acao.
- Campos tecnicos, calibracao, modelos, acesso e scheduler devem ficar em `details.form-section`.
- Use titulos curtos: `Fonte`, `Posicionamento`, `Deteccao e prioridade`, `Acesso aos frames`.
- O resumo de uma secao recolhida deve ter uma unica linha e um indicador simples `+`/`-`.
- Evitar exibir mais de 6-8 campos simultaneamente em um modal comum.

Selects:

- Usar `appearance: none` para remover a seta nativa quando possivel.
- Seta customizada discreta com dois gradientes lineares no canto direito.
- Reservar `padding-right: 32px` para a seta.
- Adicionar uma faixa sutil de acao no lado direito com `rgba(156,199,255,.06)`.
- Hover pode elevar a borda para `#46505d` e a seta para `--brand`.
- Opcoes devem usar fundo escuro `#090c10` e texto `--text-1`.
- Estado desabilitado deve reduzir opacidade e usar `--text-3`.

Scrollbars:

- Globais, finas e discretas.
- Firefox: `scrollbar-width: thin` e `scrollbar-color: #2c333c #020304`.
- WebKit: largura e altura de 9px para cobrir rolagem vertical e horizontal.
- Track preta `#020304`, com raio alto.
- Thumb em cinza tecnico, preferencialmente `linear-gradient(180deg, #343c47, #20262e)`.
- Thumb com borda de 2px na cor do track para parecer embutido.
- Hover pode clarear para `linear-gradient(180deg, #4b5665, #2b333d)`.
- O canto entre scrollbars horizontal e vertical deve manter `#020304`.

## Listas E Linhas

Linhas de camera, evento e auditoria:

- Fundo `--surface-2`.
- Borda `--line-1`.
- Hover `--surface-3`.
- Raio 5px.
- Padding 8-10px.

Titulo da linha:

- `--text-1`
- Peso 750
- Nome principal a esquerda, horario/status a direita.

Metadados:

- `--text-3`
- 11px
- Quebra livre para URLs/IDs longos.

## Mapa E Marcadores

Mapa:

- Tema escuro neutro.
- O mapa deve ser o principal plano de trabalho.
- Evitar overlays decorativos que escondam ruas/objetos.

Marcadores:

- Camera: quadrado pequeno com borda `--brand`.
- Camera offline: mesmo formato com `--red`.
- Pessoa: ponto `--green`.
- Veiculo: ponto `--blue`.
- Moto: ponto `--red`.

Campo de visao:

- Camera saudavel: linha/preenchimento `--brand`.
- Camera offline: linha/preenchimento `--red`.
- Baixa opacidade para nao competir com deteccoes.

Trajeto:

- Linha `--amber`.
- Pontos numerados.
- Primeiro ponto `--green`.
- Ultimo ponto `--red`.

## Modal De Frame

O viewer de camera deve parecer uma janela operacional:

- Fundo `--surface-1`.
- Borda `--line-2`.
- Sombra forte `--shadow-1`.
- Header `--surface-2`.
- Stats em grid.
- Area do frame com grid tecnico sutil.
- Chips de deteccao compactos.

Canvas:

- Boxes seguem a semantica do mapa.
- Labels com fundo escuro translúcido.
- Header do frame com ID da camera e modo de processamento.

## Login

Login deve parecer uma tela de acesso operacional:

- Fundo escuro com grid sutil.
- Painel compacto, centralizado.
- Marca em `--brand-strong`.
- Subtitulo discreto.
- Campos e botao iguais ao restante do app.

Nao usar imagem promocional, hero ou copy de marketing.

## Estados

Estados principais:

- Online: `--green` ou `--brand`, dependendo do contexto.
- Offline/erro: `--red`.
- Revisao/historico: `--amber`.
- Neutro/desconhecido: `--text-3`.
- Ativo/foco: `--brand-strong`.

Textos recomendados:

- `ON`
- `ERRO`
- `online`
- `offline`
- `processando`
- `desconhecido`

## Movimento E Interacao

O MVP pode funcionar sem animacoes. Se adicionar:

- Duracao curta: 120-180ms.
- Apenas hover/foco/entrada de painel.
- Evitar transicoes longas que atrasem operacao.
- Nao animar mapas, marcadores ou listas de forma que atrapalhe leitura.

## Checklist Para Novas Telas

Antes de finalizar uma nova tela:

- Usa os tokens de cor definidos?
- Mantem mapa ou dado operacional como foco?
- Tem navegacao clara e curta?
- Estados online/offline/erro estao obvios?
- Labels tecnicos usam monospace?
- Inputs e botoes têm altura consistente?
- Nao ha cards decorativos sem funcao?
- Texto cabe em mobile e desktop?
- O layout evita uma paleta de uma cor só?
- Auditoria e privacidade ficam visiveis quando relevantes?

## Exemplo De Estrutura CSS

```css
.module-panel {
  background: var(--surface-1);
  border-left: 1px solid var(--line-1);
}

.module-head {
  padding: 16px;
  border-bottom: 1px solid var(--line-1);
  background:
    linear-gradient(90deg, rgba(156, 199, 255, .08), transparent 46%),
    var(--surface-2);
}

.module-title {
  color: var(--text-1);
  font-size: 16px;
  font-weight: 850;
}

.module-subtitle {
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.35;
}
```

## Aplicacao Atual

A interface atual em `frontend/index.html` ja aplica estes padroes:

- navegacao principal por modulo;
- fundo preto com grid tecnico discreto;
- marca com simbolo de reticulo;
- tokens CSS no `:root`;
- mapa como plano principal;
- painel lateral modular;
- status do sistema em cards operacionais;
- log completo separado da operacao ao vivo;
- viewer de camera com frame e deteccoes.
