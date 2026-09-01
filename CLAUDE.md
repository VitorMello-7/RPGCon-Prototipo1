# CLAUDE.md

Contexto do projeto para sessões futuras. Leia `DESIGN.md` antes de propor
qualquer mudança de comportamento — ele é um registro de decisões fechadas,
não um documento de discussão.

## O que é

Ferramenta de estudo por repetição espaçada com pele de RPG de turnos, para
concursos públicos. Uso pessoal, monousuário, rodando local no Windows 11 com
acesso pelo celular via Tailscale.

**Alvo imediato:** Dataprev 2026, Perfil 6 (Gestão de Serviços de TIC), prova em
**11/10/2026**. A janela de conteúdo novo fecha em **27/09/2026**.

**Depois:** o mesmo engine deve servir outros concursos trocando o content pack.
E vira o TCC do MBA (entrega até fevereiro), com o log de `Review` como dataset.

## Princípio que governa tudo

**O agendador decide, o jogo narra.** O FSRS escolhe o que aparece; o combate é
apresentação. Nenhuma mecânica de jogo pode alterar o que é estudado ou quando.
Se uma feature proposta muda a fila, ela está errada.

## Arquitetura

```
core/
  models.py        Pack, Topic, Item, Card, Session, Review
  scheduling.py    ÚNICO arquivo que importa fsrs. Fila, cota, rating.
  views.py         Modo simples (a apólice): pergunta -> resposta -> grava
  combat.py        Expedição, HP, dano, mapa
  admin.py         Curadoria de conteúdo
  management/commands/
    import_pack.py   YAML -> banco, upsert preservando progresso
    export_anki.py   .apkg (conteúdo, não progresso)
    backup_db.py     cópia consistente do sqlite
packs/<slug>/
  manifest.yaml    datas, cotas, HP, limiares — nada disso em Python
  <topico>.yaml    tópicos e itens
```

Django 6.1, SQLite, Pico.css via CDN, page reload puro (sem JS, sem HTMX).
`py-fsrs` 6.3.2, `genanki` 0.13.1, `pyyaml`.

## Invariantes — quebrar qualquer uma destas é bug

1. **YAML é a fonte da verdade.** O admin edita, o próximo import sobrescreve.
2. **`external_id` é imutável.** Renomear cria item novo e aposenta o antigo,
   perdendo o histórico FSRS daquele card.
3. **Corrigir um item nunca reseta o `Card`.** Upsert por `(pack, external_id)`.
4. **Item removido do YAML é aposentado (`retired=True`), nunca deletado.**
5. **`Item` não guarda estado do jogador.** Estado mora em `Card` e `Review`.
6. **O estado do FSRS é um blob JSON opaco.** `due` e `stability` são colunas
   espelhadas, e é por elas que toda query filtra. Nunca consultar o JSON.
7. **Uma contabilidade só.** O dano é o ganho de estabilidade, em dias de
   retenção. Não existe contador de dano separado, nem multiplicador de
   velocidade como número — velocidade vira dano através do rating do FSRS.
8. **Nada específico de concurso em Python.** Datas, cotas, pesos e limiares
   vêm do `manifest.yaml`.
9. **Pool `exam` nunca entra na revisão diária.** Questão de prova só no boss;
   ver a mesma questão cinco vezes ensina o enunciado, não o conteúdo.
10. **Item novo nunca tira HP do jogador.** Senão todo dia de conteúdo novo
    termina em derrota e o usuário aprende a evitar material novo.
11. **O total atrasado nunca é exibido.** É o número que faz as pessoas
    largarem o Anki.

## Decisões já tomadas e fechadas

Estão em `DESIGN.md`, numeradas (0.1 a 5.3). Referencie o número ao mexer.
As mais fáceis de violar por engano:

- **3.7** — sem XP, níveis, loot ou inventário. Só o mapa de desbloqueio.
- **3.2** — dano determinístico, sem crítico e sem RNG.
- **3.8** — tempo medido sempre, exibido só no boss.
- **2.1** — cota de conteúdo novo derivada do prazo, não da vontade.
- **0.4** — monousuário, sem auth. `get_player()` devolve o primeiro usuário.

## Correções já aplicadas (não reintroduzir)

- **Cota que encolhia.** `daily_new_quota` dividia os itens sem card pelos dias
  restantes e depois subtraía os introduzidos hoje — dupla contagem. A base
  correta é o pendente no início do dia (pendentes + introduzidos hoje).
- **HP binário congelado.** Maturidade binária a 21 dias deixava a barra do
  inimigo parada por três semanas. Agora o HP é contínuo: cada item cai
  linearmente de 1 até 0 conforme a estabilidade se aproxima do limiar.
- **Cota contando conteúdo trancado.** O mapa filtra `pending_new`; contar
  itens inalcançáveis inflava a cota com material que o jogo não serve.
- **500 em POST fora de contexto.** Botão voltar, duas abas ou sessão expirada
  redirecionam para a fila.
- **`unlocks` para tópico inexistente** dá erro legível, não `KeyError`.

## Comandos

```powershell
py manage.py import_pack packs\dataprev-2026-p6 --dry-run   # sempre antes
py manage.py import_pack packs\dataprev-2026-p6
py manage.py export_anki dataprev-2026-p6
py manage.py backup_db --dest "$HOME\OneDrive\backups-estudo"
py manage.py runserver 0.0.0.0:8000
```

Rotas: `/` (redireciona), `/<slug>/` modo simples, `/<slug>/expedicao/`,
`/<slug>/mapa/`, `/admin/`.

## Como validar mudanças

Não há suíte de testes. A verificação é feita com `manage.py shell` e o
`django.test.Client`, percorrendo o fluxo como um navegador faria. Ao mexer em
agendamento ou combate, confirme pelo menos:

- import idempotente (reimport = `0 novos · 0 atualizados`)
- editar item preserva `stability` e `reps` do card
- cota entrega o número que anuncia
- errar 5 vezes derruba e a fila continua vencida depois
- tópico trancado não é servido

## Ao trabalhar em conteúdo

- **Cloze conceitual por LLM: sim.** A partir de resumos que o usuário leu.
- **Artigo de lei por LLM: não.** Lei 13.303 e INs da SGD são digitados do texto
  oficial. Errar prazo ou número de artigo faz o usuário decorar informação
  errada, que é pior que não estudar.
- Ao trabalhar uma prova antiga, faça **duas** coisas com cada questão: guarde a
  original no pool `exam` e extraia o conceito como cloze no pool `review`.
- `source` é obrigatório em todo item.

## Estado atual

Blocos 1 a 5 do plano concluídos (~9h de 12 orçadas). Falta o **boss** (3.6):
20 questões do pool `exam`, cronometradas, desbloqueio em 70% de maturidade do
bloco, e cada erro gera um cloze novo marcado como fraqueza.

O gargalo do projeto **não é mais código** — é conteúdo. O banco tem itens de
exemplo e itens sintéticos de teste. Antes de escrever qualquer feature nova,
pergunte se a hora não renderia mais escrevendo cards.

## Como o usuário quer ser tratado

Vitor pede avaliação honesta antes de compromisso, recomendações explícitas com
trade-offs declarados, e que discrepâncias sejam levantadas antes de gerar
qualquer coisa. Listar opções neutras sem recomendar é o oposto do que ele quer.

Verificar antes de afirmar: rodar o código e reportar o que aconteceu vale mais
que descrever o que deveria acontecer.
