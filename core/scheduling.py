"""
core/scheduling.py — única parte do projeto que conhece o py-fsrs.

Testado com fsrs 6.3.2. Se a lib mudar de API, só este arquivo muda: o resto
lê `card.due` e `card.stability`, que são colunas espelhadas.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone
from fsrs import Card as FsrsCard, Rating, Scheduler

from .models import Card, Item, Review


def _scheduler(pack) -> Scheduler:
    return Scheduler(desired_retention=pack.desired_retention)  # 2.2


# ------------------------------------------------------------------ 3.1

def derive_rating(*, correct: bool, elapsed_ms: int, is_mature: bool,
                  fast_seconds: float, was_guess: bool = False) -> Rating:
    """Rating automático para múltipla escolha, sem tap extra.

    Errou ou admitiu chute  → Again
    Acertou devagar          → Hard
    Acertou rápido           → Good
    Acertou rápido e maduro  → Easy
    """
    if was_guess or not correct:
        return Rating.Again
    if elapsed_ms > fast_seconds * 1000:
        return Rating.Hard
    return Rating.Easy if is_mature else Rating.Good


# ------------------------------------------------------------------ card

def get_or_create_card(player, item: Item) -> tuple[Card, bool]:
    """Cria o Card na primeira vez que o item aparece."""
    card = Card.objects.filter(player=player, item=item).first()
    if card:
        return card, False
    fresh = FsrsCard()
    return Card.objects.create(
        player=player,
        item=item,
        fsrs_state=fresh.to_dict(),
        due=fresh.due,
        stability=fresh.stability or 0.0,
    ), True


@transaction.atomic
def apply_review(card: Card, rating: Rating, *, elapsed_ms: int,
                 correct: bool | None = None, was_guess: bool = False,
                 session=None) -> Card:
    """Aplica o rating, avança o FSRS e grava o log do 3.1/TCC."""
    pack = card.item.pack
    was_new = card.is_new
    stability_before = card.stability

    fsrs_card = FsrsCard.from_dict(card.fsrs_state)
    fsrs_card, _log = _scheduler(pack).review_card(
        fsrs_card, rating, review_duration=elapsed_ms
    )

    card.fsrs_state = fsrs_card.to_dict()
    card.due = fsrs_card.due
    card.stability = fsrs_card.stability or 0.0
    card.reps += 1
    if rating == Rating.Again:
        card.lapses += 1
    card.last_review = timezone.now()
    card.save()

    Review.objects.create(
        session=session,
        card=card,
        rating=int(rating),
        elapsed_ms=elapsed_ms,
        was_correct=correct,
        was_guess=was_guess,
        was_new=was_new,
        stability_before=stability_before,
        stability_after=card.stability,
    )
    return card


# ------------------------------------------------------------------ fila

def introduced_today(player, pack) -> int:
    return Card.objects.filter(
        player=player,
        item__pack=pack,
        introduced_at__date=timezone.localdate(),
    ).count()


def pending_new(player, pack) -> int:
    """Itens do pool de revisão que o jogador nunca viu."""
    return (
        Item.objects.filter(pack=pack, retired=False, pool=Item.POOL_REVIEW)
        .exclude(cards__player=player)
        .count()
    )


def daily_new_quota(player, pack) -> int:
    """Cota de conteúdo novo do dia, derivada do prazo (2.1).

    A base é o que estava pendente no INÍCIO do dia — pendentes + já
    introduzidos hoje. Usar só os pendentes faria a cota encolher a cada item
    estudado, entregando menos da metade do previsto.
    """
    days_left = pack.intro_days_left()
    if days_left == 0:
        return 0
    base = pending_new(player, pack) + introduced_today(player, pack)
    return -(-base // days_left)  # ceil


def new_items_allowed(player, pack) -> int:
    """Quantos itens novos ainda cabem hoje (2.1)."""
    return max(daily_new_quota(player, pack) - introduced_today(player, pack), 0)


def next_due_card(player, pack) -> Card | None:
    """Card vencido de maior prioridade: atraso × peso do tópico (2.3)."""
    now = timezone.now()
    due = (
        Card.objects.filter(
            player=player,
            item__pack=pack,
            item__retired=False,
            item__pool=Item.POOL_REVIEW,
            due__lte=now,
        )
        .select_related("item", "item__topic", "item__pack")
    )
    best, best_score = None, -1.0
    for card in due:
        overdue_days = max((now - card.due).total_seconds() / 86400, 0.0) + 1.0
        score = overdue_days * card.item.topic.weight
        if score > best_score:
            best, best_score = card, score
    return best


def next_new_item(player, pack) -> Item | None:
    """Próximo item nunca visto, respeitando a cota diária."""
    if new_items_allowed(player, pack) <= 0:
        return None
    return (
        Item.objects.filter(pack=pack, retired=False, pool=Item.POOL_REVIEW)
        .exclude(cards__player=player)
        .select_related("topic", "pack")
        .order_by("-topic__weight", "topic__order", "id")
        .first()
    )


def next_card(player, pack) -> tuple[Card | None, bool]:
    """(card, is_new). Vencidos primeiro; conteúdo novo só depois."""
    card = next_due_card(player, pack)
    if card:
        return card, False
    item = next_new_item(player, pack)
    if item:
        card, _ = get_or_create_card(player, item)
        return card, True
    return None, False


def remaining_today(player, pack) -> int:
    """Contagem para a barra de progresso.

    O total atrasado NUNCA é exibido (2.3): mostramos só o que cabe hoje.
    """
    now = timezone.now()
    due = Card.objects.filter(
        player=player, item__pack=pack, item__retired=False,
        item__pool=Item.POOL_REVIEW, due__lte=now,
    ).count()
    cap = pack.block_size * pack.blocks_per_session
    return min(due + new_items_allowed(player, pack), cap)
