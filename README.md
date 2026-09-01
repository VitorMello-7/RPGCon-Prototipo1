# estudo-rpg

Ferramenta de estudo por repetição espaçada, com pele de RPG de turnos, para
concursos públicos. Feita para uso pessoal — sou eu estudando pra minha
própria prova, não um produto para terceiros (ainda).

Esse repositório também é o experimento prático do meu TCC do MBA em
Engenharia de Software: uso a IA como assistente de desenvolvimento (agente
Claude Opus/Sonnet 5) para construir a ferramenta inteira, e o histórico de
respostas do próprio sistema (`Review`) vira o dataset de análise do
trabalho. Duas coisas acontecendo ao mesmo tempo: eu estudo pra um concurso
de verdade, e documento como foi construir isso com apoio de IA.

Uso alguns princípios de desenvolvimento que sigo — o "grill me" do Matt
Pocock (questionar a decisão antes de aceitar) e a filosofia pragmática do
Fabio Akita — mas o objetivo aqui não é arquitetura bonita. É funcionar.

## A ideia, em uma frase

**O agendador decide o que aparece; o RPG só narra.** Por baixo do combate
tem um algoritmo de repetição espaçada (FSRS) escolhendo o que estudar e
quando. A "luta" contra um inimigo é só a apresentação disso — dano é ganho
de retenção, HP do inimigo é o quanto daquele assunto eu já domino. Nenhuma
mecânica de jogo muda o que é estudado ou a ordem — se algo fizer isso, é
bug, não feature.

Duas apólices sempre disponíveis, sem depender uma da outra: **modo simples**
(pergunta → resposta → grava, sem jogo nenhum) e **export para Anki**
(`.apkg`, pra estudar fora do app se um dia eu quiser).

## Arquitetura, resumida

```
core/
  models.py        Pack, Topic, Item, Card, Session, Review
  scheduling.py     único arquivo que sabe o que é FSRS — o resto só lê due/stability
  views.py          modo simples
  combat.py         expedição, HP, dano, mapa de desbloqueio
  admin.py          curadoria de conteúdo pelo Django admin
  management/commands/
    import_pack.py    YAML -> banco, upsert preservando progresso
    export_anki.py    gera .apkg (só conteúdo, sem progresso)
    backup_db.py       cópia consistente do sqlite
packs/<slug>/
  manifest.yaml     data da prova, cotas, HP, pesos — nada disso em Python
  <área>.yaml       tópicos e itens (cloze ou múltipla escolha)
```

A separação importa por um motivo prático: o motor (tudo em `core/`) não
sabe nada sobre Dataprev, ITIL ou Lei 13.303. Quem sabe é o `packs/`. Trocar
de concurso — ou de assunto inteiro — é trocar de pasta em `packs/`, não
reescrever código.

Stack: Django 6.1, SQLite, `py-fsrs` 6.3.2 pro agendamento, `genanki` 0.13.1
pro export, Pico.css via CDN (sem build de frontend, page reload puro).

## Instalação

```powershell
py -m venv venv
venv\Scripts\activate
pip install django==6.1 fsrs==6.3.2 genanki==0.13.1 pyyaml==6.0.3
py manage.py migrate
```

Não tem cadastro de usuário — a v1 é monousuário de propósito (`get_player()`
sempre devolve o primeiro usuário do banco). Se o banco estiver vazio, crie
um antes do primeiro uso:

```powershell
py manage.py createsuperuser
```

## Como usar

### 1. Importar o conteúdo de um pack

Todo conteúdo (perguntas, tópicos, prazos da prova) vem de YAML em
`packs/<slug>/`, nunca do código. Antes de importar de verdade, sempre rode
com `--dry-run` — ele mostra quantos itens seriam criados/atualizados/
aposentados sem gravar nada:

```powershell
py manage.py import_pack packs\dataprev-2026-p6 --dry-run
py manage.py import_pack packs\dataprev-2026-p6
```

Reimportar depois de editar um item corrige o texto **sem resetar** seu
progresso de memorização — o vínculo é pelo `external_id` do item, não pelo
conteúdo. Só remover um item do YAML o aposenta (ele some da fila, mas o
histórico continua no banco).

### 2. Rodar o servidor

```powershell
py manage.py runserver 0.0.0.0:8000
```

`0.0.0.0` (em vez de `127.0.0.1`) é o que permite acessar de outro
dispositivo na mesma rede — por exemplo o celular, via Tailscale, sem expor
nada pra internet.

### 3. As telas

| Rota | O que é |
|---|---|
| `/` | redireciona pro pack ativo |
| `/<slug>/` | **modo simples**: pergunta → resposta → próxima |
| `/<slug>/mapa/` | mapa de desbloqueio dos tópicos (o "edital visual") |
| `/<slug>/expedicao/` | combate: escolhe até 3 inimigos (tópicos) pra a sessão |
| `/admin/` | edição de conteúdo pelo Django admin |

**Modo simples** é a apólice de baixo esforço: aparece uma pergunta, você
responde, o sistema grava e mostra a próxima. Cloze mostra a frente, você
revela o verso e se autoavalia em 4 botões (Errei / Difícil / Bom / Fácil).
Múltipla escolha deriva essa nota sozinha, a partir de acerto e tempo de
resposta — você só escolhe a alternativa.

**Expedição** é a versão em RPG do mesmo agendador: você entra numa sessão
com até 3 inimigos (tópicos escolhidos por peso e disponibilidade), cada
"golpe" é uma pergunta respondida certo, e a vida do inimigo cai conforme
sua retenção naquele assunto sobe de verdade — não existe combo, crítico ou
sorte, é determinístico. HP do jogador (5 por sessão) só cai quando você
erra um item que já vinha revisando; item novo nunca causa dano, senão todo
dia com conteúdo novo vira derrota.

Nenhum dos dois modos deixa passar mais itens novos por dia do que a cota
calculada a partir do prazo da prova — isso é decidido pelo agendador, o
jogo não interfere.

### 4. Backup e export

```powershell
py manage.py backup_db --dest "$HOME\OneDrive\backups-estudo"
py manage.py export_anki dataprev-2026-p6
```

O backup é uma cópia consistente do sqlite (progresso incluso). O export
Anki gera um `.apkg` só com o conteúdo — sem o histórico de repetição, que
não faz sentido fora deste sistema.

## Onde estão as decisões de projeto

Esse README é a porta de entrada; as decisões de fato — por que FSRS, por
que sem XP/loot, como a cota diária é calculada, os limites e trade-offs
aceitos — estão registradas e numeradas em `DESIGN.md`, com o histórico do
que já foi corrigido em `CLAUDE.md`. Se algo aqui parecer arbitrário, a
razão provavelmente está lá.
