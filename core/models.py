"""
core/models.py — engine de estudo por combate em turnos.

Regras de projeto (ver DESIGN.md):

- Nada específico de concurso mora aqui. Datas, cotas e pesos vêm do manifest
  do pack (5.2).
- Item nunca guarda estado do jogador. Estado fica em Card/Review (1.4).
- O estado do FSRS é persistido como blob JSON opaco (`Card.to_dict()`) para não
  acoplar o schema à versão da lib. `due` e `stability` são espelhados em colunas
  indexadas porque toda query de fila e de HP de inimigo depende deles.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------- pack

class Pack(models.Model):
    """Um concurso. Tudo que é específico dele é dado, não código."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)

    exam_date = models.DateField()
    intro_window_days = models.PositiveIntegerField(default=30)  # 2.1

    desired_retention = models.FloatField(default=0.90)          # 2.2
    mature_threshold_days = models.PositiveIntegerField(default=21)  # 3.3

    block_size = models.PositiveIntegerField(default=15)         # 3.5
    blocks_per_session = models.PositiveIntegerField(default=3)  # 3.5
    player_hp = models.PositiveIntegerField(default=5)           # 3.4
    fast_answer_seconds = models.FloatField(default=20.0)        # 3.1
    boss_unlock_ratio = models.FloatField(default=0.70)          # 3.6

    imported_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name

    # -- 2.1: a cota de material novo sai do prazo, não da vontade -----------

    # os dias finais são só revisão + simulado
    review_only_days = models.PositiveIntegerField(default=14)

    @property
    def intro_deadline(self) -> dt.date:
        """Último dia em que conteúdo novo pode ser introduzido."""
        return self.exam_date - dt.timedelta(days=self.review_only_days)

    def days_until_exam(self, today: dt.date | None = None) -> int:
        today = today or timezone.localdate()
        return (self.exam_date - today).days

    def intro_days_left(self, today: dt.date | None = None) -> int:
        """Dias úteis restantes para introduzir conteúdo novo (0 = janela fechada).

        A política de cota mora em scheduling.daily_new_quota — aqui só o prazo.
        """
        today = today or timezone.localdate()
        if today > self.intro_deadline:
            return 0
        return max((self.intro_deadline - today).days, 1)


# ---------------------------------------------------------------- topic

class Topic(models.Model):
    """Um tópico do edital. Também é o inimigo (3.3).

    `external_id` é o próprio path ("itil/praticas/incidentes"). O parent é
    derivado por split no importador — não existe campo `parent` no YAML (1.3).
    """

    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name="topics")
    external_id = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    name = models.CharField(max_length=200)
    path = models.CharField(max_length=200, db_index=True)
    weight = models.FloatField(default=1.0)  # 2.4
    order = models.PositiveIntegerField(default=0)

    # 3.7 / mapa de desbloqueio
    unlocks = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="unlocked_by"
    )

    class Meta:
        unique_together = [("pack", "external_id")]
        ordering = ["path"]

    def __str__(self) -> str:
        return self.path

    def save(self, *args, **kwargs):
        self.path = self.external_id
        super().save(*args, **kwargs)

    # -- 3.3: HP do inimigo é derivado do FSRS, não da sessão ----------------

    def review_items(self):
        """Itens do pool de revisão nesta subárvore."""
        return Item.objects.filter(
            pack=self.pack,
            retired=False,
            pool=Item.POOL_REVIEW,
            topic__path__startswith=self.path,
        )

    def hp(self, player) -> tuple[int, int]:
        """(hp_atual, hp_max) — contínuo, proporcional à estabilidade.

        Cada item contribui com 1 ponto de HP quando nunca foi visto e 0 quando
        atinge a maturidade; entre os dois, cai linearmente. Maturidade binária
        deixaria a barra congelada por três semanas, que é o mesmo que não ter
        barra (3.3).
        """
        items = list(self.review_items().values_list("id", flat=True))
        hp_max = len(items)
        if not hp_max:
            return 0, 0
        threshold = self.pack.mature_threshold_days
        remaining = float(hp_max)
        for stability in Card.objects.filter(
            player=player, item_id__in=items
        ).values_list("stability", flat=True):
            remaining -= min(stability / threshold, 1.0)
        return max(remaining, 0.0), hp_max

    def maturity_ratio(self, player) -> float:
        """Fração dominada, contínua. É o que move a barra (3.3)."""
        current, total = self.hp(player)
        if not total:
            return 0.0
        return (total - current) / total

    def boss_unlocked(self, player) -> bool:  # 3.6
        return self.maturity_ratio(player) >= self.pack.boss_unlock_ratio


# ---------------------------------------------------------------- item

class Item(models.Model):
    """Uma unidade de conteúdo. Modelo único para os dois kinds (1.1)."""

    KIND_CLOZE = "cloze"
    KIND_MCQ = "mcq"
    KIND_CHOICES = [(KIND_CLOZE, "Cloze"), (KIND_MCQ, "Múltipla escolha")]

    POOL_REVIEW = "review"  # entra no FSRS e no combate diário
    POOL_EXAM = "exam"      # questão FGV real, só no boss (2.5)
    POOL_CHOICES = [(POOL_REVIEW, "Revisão"), (POOL_EXAM, "Simulado")]

    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name="items")
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name="items")
    external_id = models.CharField(max_length=200)

    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    pool = models.CharField(max_length=16, choices=POOL_CHOICES, default=POOL_REVIEW)
    payload = models.JSONField()

    source = models.CharField(max_length=300)  # 1.5, obrigatório
    comment = models.TextField(blank=True)

    # 5.3: reservado, sem uso na v1.
    concept_id = models.CharField(max_length=200, blank=True)

    # 3.6: cloze gerado a partir de erro em boss
    from_weakness = models.BooleanField(default=False)

    # 1.4: item sumiu do YAML → aposenta, não deleta (o Card sobrevive)
    retired = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("pack", "external_id")]

    def __str__(self) -> str:
        return f"[{self.kind}] {self.external_id}"

    # -- validação de payload por kind (1.1) --------------------------------

    def clean(self):
        if self.kind == self.KIND_CLOZE:
            for field in ("front", "back"):
                if not self.payload.get(field):
                    raise ValidationError(f"cloze exige '{field}'")
        elif self.kind == self.KIND_MCQ:
            choices = self.payload.get("choices") or []
            if not self.payload.get("stem"):
                raise ValidationError("mcq exige 'stem'")
            if len(choices) < 2:
                raise ValidationError("mcq exige ao menos 2 alternativas")
            answer = self.payload.get("answer")
            if not isinstance(answer, int) or not 0 <= answer < len(choices):
                raise ValidationError("mcq exige 'answer' como índice válido")

    @property
    def question_text(self) -> str:
        return self.payload.get("stem") or self.payload.get("front", "")


# ---------------------------------------------------------------- card

class Card(models.Model):
    """Estado de agendamento de um item para um jogador.

    `fsrs_state` é o dict opaco do py-fsrs. `due` e `stability` são espelhos
    indexados — toda query de fila e de HP usa eles, nunca o JSON.
    """

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cards"
    )
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="cards")

    fsrs_state = models.JSONField(default=dict)
    due = models.DateTimeField(db_index=True)
    stability = models.FloatField(default=0.0, db_index=True)

    reps = models.PositiveIntegerField(default=0)
    lapses = models.PositiveIntegerField(default=0)
    introduced_at = models.DateTimeField(auto_now_add=True)
    last_review = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("player", "item")]
        indexes = [models.Index(fields=["player", "due"])]

    def __str__(self) -> str:
        return f"{self.player} · {self.item.external_id}"

    @property
    def is_mature(self) -> bool:  # 3.3
        return self.stability >= self.item.pack.mature_threshold_days

    @property
    def is_new(self) -> bool:  # 3.4: item novo nunca causa dano
        return self.reps == 0


# ---------------------------------------------------------------- sessão

class Session(models.Model):
    """Uma expedição: 3 inimigos, HP por sessão (3.4, 3.5)."""

    OUTCOME_ONGOING = "ongoing"
    OUTCOME_CLEARED = "cleared"
    OUTCOME_DOWNED = "downed"   # HP zerou — fila fica intacta
    OUTCOME_ABANDONED = "abandoned"

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions"
    )
    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name="sessions")
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    hp_remaining = models.IntegerField(default=0)

    # 3.5: os 3 inimigos da expedicao, por path de topico
    enemies = models.JSONField(default=list)
    enemy_index = models.PositiveIntegerField(default=0)
    cards_in_block = models.PositiveIntegerField(default=0)
    cards_done = models.PositiveIntegerField(default=0)

    # 3.2/3.3: dano = ganho de estabilidade em dias. Uma contabilidade so.
    damage = models.FloatField(default=0.0)

    is_boss = models.BooleanField(default=False)
    boss_topic = models.ForeignKey(
        Topic, null=True, blank=True, on_delete=models.SET_NULL, related_name="boss_runs"
    )
    outcome = models.CharField(max_length=16, default=OUTCOME_ONGOING)


class Review(models.Model):
    """Log de toda resposta.

    Sem uso na v1. É o dataset do TCC e é irrecuperável se não gravar desde o
    dia 1: custa uma tabela agora.
    """

    RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY = 1, 2, 3, 4

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="reviews", null=True, blank=True
    )
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField()
    elapsed_ms = models.PositiveIntegerField()
    was_correct = models.BooleanField(null=True)   # None para cloze autoavaliado
    was_guess = models.BooleanField(default=False)  # 3.1, botão opcional
    was_new = models.BooleanField(default=False)
    stability_before = models.FloatField(default=0.0)
    stability_after = models.FloatField(default=0.0)
    reviewed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-reviewed_at"]
