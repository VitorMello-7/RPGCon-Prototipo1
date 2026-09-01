# Registro de decisões — engine de estudo por combate em turnos

Documento fechado após entrevista de design. Cada item é uma decisão tomada,
não uma opção em aberto. Revisar só com dado de uso real.

---

## 0. Escopo

| # | Decisão |
|---|---|
| 0.1 | Construir agora, orçamento **duro de 12h**. Estourou o bloco 3 → congela e usa o modo simples. |
| 0.2 | Sem método prévio. Duas apólices obrigatórias: **modo simples** dentro do app e **export Anki**. |
| 0.3 | Objetivo primário: passar no Dataprev (11/10/2026). TCC do MBA é fase 2, janela 12/10 → fevereiro. |
| 0.4 | Usuário único, sem auth. `player` como FK desde o início. |

**Regra que governa tudo:** zero cerimônia arquitetural na v1. `models.py` gordo,
sem camada de serviço, sem contextos separados. A reescrita com DDD é o TCC.

---

## 1. Conteúdo

| # | Decisão |
|---|---|
| 1.1 | Modelo único `Item` com `kind` (`mcq` \| `cloze`) e `payload` JSON. Validação por serializer no import. |
| 1.2 | Fontes, por retorno/hora: (1) cloze gerado por LLM a partir de resumos, (2) provas FGV coladas e normalizadas, (3) texto legal à mão. **LLM não gera card de artigo de lei.** Sem parser genérico de PDF. |
| 1.3 | `Topic` com `parent` FK + `path` materializado. O `id` no YAML **é** o path; parent derivado por split. |
| 1.4 | Upsert por `(pack, external_id)`. Correção de item **nunca** reseta o estado FSRS. Item removido do YAML é aposentado (`retired=True`), não deletado. |
| 1.5 | `source` obrigatório em todo item. Comentário opcional. |

---

## 2. Agendamento

| # | Decisão |
|---|---|
| 2.1 | Cota diária de conteúdo novo derivada do prazo, não da vontade. Itens novos só nos primeiros **30 dias**; os 14 finais são revisão + simulado. |
| 2.2 | Retenção-alvo do FSRS: **0,90**. |
| 2.3 | Teto rígido por sessão. Prioridade = `atraso × weight`. **Nunca exibir o total atrasado.** |
| 2.4 | `weight` por tópico, calibrado pela frequência histórica FGV. Alimenta priorização e ordem dos bosses. |
| 2.5 | **Dois pools separados.** `review` (cloze/conceitual, entra no FSRS e no combate diário) e `exam` (questões FGV reais, alternativas embaralhadas, só nos bosses). Questão de prova nunca entra na rotação de revisão. |

---

## 3. Combate

| # | Decisão |
|---|---|
| 3.1 | Rating derivado automaticamente em MCQ: errou → Again; certo lento (>20s) → Hard; certo rápido → Good; certo rápido em card maduro → Easy. Cloze usa os 4 botões. Botão opcional "foi chute" força Again. |
| 3.2 | Dano **determinístico**. Sem crítico, sem esquiva, sem RNG. |
| 3.3 | HP do inimigo é **persistente e derivado do FSRS**: `HP_max` = itens do tópico (subárvore), `HP_atual` = itens não maduros (`stability < 21d`). Exibir também o dano da sessão. |
| 3.4 | HP do jogador **por sessão** (5). Erro tira 1. Zerou, a sessão acaba e a fila fica intacta. **Item novo nunca causa dano.** |
| 3.5 | Sessão = expedição com **3 inimigos**, blocos de ~15 cards. Se começar a confundir frameworks, cair para 5 blocos de 8. |
| 3.6 | Boss desbloqueia em 70% de maturidade do bloco. 20 questões FGV cronometradas. Perder não pune — **cada erro gera um cloze novo** marcado como fraqueza. |
| 3.7 | **Sem XP, níveis, loot ou inventário.** Só a árvore de desbloqueio (mapa do edital). |
| 3.8 | Tempo sempre medido, exibido **só no boss**. |

---

## 4. Stack

| # | Decisão |
|---|---|
| 4.1 | `startproject` novo. **Não** partir do TemplateONE. |
| 4.2 | Django no PC + **Tailscale** para acesso pelo celular. Sem VPS, sem TLS, sem domínio. |
| 4.3 | Page reload puro. HTMX é polimento da v2. |
| 4.4 | Pico.css via CDN. Sem build step. Alvos de toque grandes, alternativas em botões full-width. |
| 4.5 | `sqlite3 .backup` diário via cron para pasta sincronizada. YAMLs no git. |

**Risco declarado:** infra é onde 12h viram 30. Tudo nesta camada é descartável.

---

## 5. Portabilidade

| # | Decisão |
|---|---|
| 5.1 | YAML, um arquivo por tópico, pasta por pack: `packs/<slug>/`. |
| 5.2 | `manifest.yaml` carrega data da prova, janela de introdução, cotas, HP e pesos. **Nenhuma data ou peso hardcoded em Python.** |
| 5.3 | `concept_id` existe no schema e **não é usado**. Reaproveitamento entre concursos fica para 2027. |

---

## Orçamento das 12h

| Bloco | h | Entregável |
|---|---|---|
| 1 | 2 | Models + import de YAML + admin |
| 2 | 2 | Modo simples: pergunta → resposta → FSRS grava |
| 3 | 1 | Export Anki + Tailscale + backup |
| 4 | 3 | Combate: HP, expedição de 3 inimigos, dano por tempo |
| 5 | 1 | Mapa de desbloqueio |
| 6 | 3 | Conteúdo: primeiros ~120 itens de ITIL |

**Checkpoint no fim do bloco 3:** existe um sistema de estudo usável. Se o tempo
estourou ali, para, usa o modo simples e volta a estudar. Blocos 4–5 são bônus.
Boss e pool de simulado ficam para o segundo fim de semana, se houver um.

---

## Decisões técnicas fora da entrevista

- **Estado do FSRS como blob JSON** (`Card.to_dict()`), com `due` e `stability`
  espelhados em colunas indexadas. Isola o schema da versão da lib; as queries de
  fila e de HP usam as colunas.
- **Tabela `Review`** grava toda resposta com tempo, acerto e rating. Sem uso na
  v1 — é o dataset do TCC, e é irrecuperável se não gravar desde o dia 1.

## A revisar no dia 7, com dados reais

- **3.5** — 15 cards por bloco pode ser longo demais para sessão no celular.
- **2.1** — a cota diária pode sair desanimadora dependendo do tamanho do pack.

Ambas são um número no manifest.
