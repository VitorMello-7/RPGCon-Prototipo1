"""
core/combat.py — o jogo (blocos 4 e 5).

Regras que governam este arquivo, do DESIGN:

3.2  Dano determinístico. Não existe multiplicador de velocidade como número
     separado: responder rápido produz Good/Easy, que produz mais estabilidade,
     que É o dano. Uma contabilidade só.
3.3  HP do inimigo é derivado do FSRS (itens não maduros na subárvore), não de
     pontos acumulados. Persiste entre sessões.
3.4  HP do jogador é por sessão. Errar tira 1. Item novo NUNCA tira HP.
     Zerou: a sessão acaba e a fila fica intacta para amanhã.
3.5  Expedição = 3 inimigos, um por vez, blocos de ~15 cards.
3.8  Tempo é medido sempre, exibido só no boss.
"""

from __future__ import annotations

import datetime as dt
import random

from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from fsrs import Rating

from .models import Card, Item, Pack, Session, Topic
from .scheduling import apply_review, derive_rating, next_card, pick_enemies
from .views import MAX_ELAPSED_MS, get_player


def _open_session(player, pack) -> Session | None:
    return Session.objects.filter(
        player=player, pack=pack, outcome=Session.OUTCOME_ONGOING, is_boss=False
    ).order_by("-started_at").first()


def _current_enemy(session) -> Topic | None:
    if session.enemy_index >= len(session.enemies):
        return None
    return Topic.objects.filter(
        pack=session.pack, path=session.enemies[session.enemy_index]
    ).first()


def _finish(session, outcome) -> Session:
    session.outcome = outcome
    session.ended_at = timezone.now()
    session.save()
    return session


def expedition(request, slug):
    """Ponto de entrada: retoma a expedição aberta ou monta uma nova (3.5)."""
    pack = get_object_or_404(Pack, slug=slug)
    player = get_player()

    session = _open_session(player, pack)
    if session is None:
        enemies = pick_enemies(player, pack)
        if not enemies:
            return render(request, "core/done.html", {"pack": pack})
        session = Session.objects.create(
            player=player, pack=pack,
            hp_remaining=pack.player_hp,
            enemies=[topic.path for topic in enemies],
        )
    return redirect("fight", slug=slug)


def fight(request, slug):
    """Serve o próximo card do inimigo atual."""
    pack = get_object_or_404(Pack, slug=slug)
    player = get_player()
    session = _open_session(player, pack)
    if session is None:
        return redirect("expedition", slug=slug)

    # avança de inimigo enquanto o atual não tiver mais o que servir
    while True:
        enemy = _current_enemy(session)
        if enemy is None:
            return redirect("summary", slug=slug, pk=_finish(
                session, Session.OUTCOME_CLEARED).pk)

        card, is_new = next_card(player, pack, topic=enemy)
        block_done = session.cards_in_block >= pack.block_size
        if card is None or block_done:
            session.enemy_index += 1
            session.cards_in_block = 0
            session.save()
            continue
        break

    request.session["card_id"] = card.pk
    request.session["started_at"] = timezone.now().isoformat()

    choices = None
    if card.item.kind == Item.KIND_MCQ:
        choices = list(enumerate(card.item.payload["choices"]))
        random.shuffle(choices)

    hp_current, hp_max = enemy.hp(player)
    return render(request, "core/fight.html", {
        "pack": pack, "session": session, "enemy": enemy,
        "card": card, "item": card.item, "is_new": is_new, "choices": choices,
        "enemy_hp": hp_current, "enemy_hp_max": hp_max,
        "enemy_pct": 100 * (hp_max - hp_current) / hp_max if hp_max else 0,
        "enemy_number": session.enemy_index + 1,
        "enemy_total": len(session.enemies),
    })


def reveal(request, slug):
    """Cloze: mostra o verso e os 4 botões."""
    pack = get_object_or_404(Pack, slug=slug)
    player = get_player()
    session = _open_session(player, pack)
    card = Card.objects.filter(
        pk=request.session.get("card_id"), player=player
    ).select_related("item").first()
    if session is None or card is None:
        return redirect("expedition", slug=slug)
    enemy = _current_enemy(session)
    hp_current, hp_max = enemy.hp(player)
    return render(request, "core/fight.html", {
        "pack": pack, "session": session, "enemy": enemy,
        "card": card, "item": card.item, "revealed": True,
        "enemy_hp": hp_current, "enemy_hp_max": hp_max,
        "enemy_pct": 100 * (hp_max - hp_current) / hp_max if hp_max else 0,
        "enemy_number": session.enemy_index + 1,
        "enemy_total": len(session.enemies),
    })


@require_POST
def strike(request, slug):
    """Resolve o turno: rating, dano, HP."""
    pack = get_object_or_404(Pack, slug=slug)
    player = get_player()
    session = _open_session(player, pack)
    card = Card.objects.filter(
        pk=request.session.get("card_id"), player=player
    ).select_related("item", "item__pack").first()
    if session is None or card is None:
        return redirect("expedition", slug=slug)

    started = request.session.get("started_at")
    elapsed = MAX_ELAPSED_MS
    if started:
        delta = timezone.now() - dt.datetime.fromisoformat(started)
        elapsed = min(int(delta.total_seconds() * 1000), MAX_ELAPSED_MS)

    was_guess = bool(request.POST.get("guess"))
    was_new = card.is_new
    correct = correct_text = None

    if card.item.kind == Item.KIND_MCQ:
        if "choice" not in request.POST:
            return redirect("fight", slug=slug)
        chosen = int(request.POST["choice"])
        correct = chosen == card.item.payload["answer"]
        correct_text = card.item.payload["choices"][card.item.payload["answer"]]
        rating = derive_rating(
            correct=correct, elapsed_ms=elapsed, is_mature=card.is_mature,
            fast_seconds=pack.fast_answer_seconds, was_guess=was_guess,
        )
    else:
        if "rating" not in request.POST:
            return redirect("fight", slug=slug)
        rating = Rating(int(request.POST["rating"]))

    stability_before = card.stability
    apply_review(card, rating, elapsed_ms=elapsed, correct=correct,
                 was_guess=was_guess, session=session)

    # 3.2/3.3: o dano É o ganho de estabilidade, em dias de retenção
    damage = max(card.stability - stability_before, 0.0)
    session.damage += damage
    session.cards_in_block += 1
    session.cards_done += 1

    # 3.4: item novo nunca tira HP
    took_hit = rating == Rating.Again and not was_new
    if took_hit:
        session.hp_remaining -= 1
    session.save()

    if session.hp_remaining <= 0:
        _finish(session, Session.OUTCOME_DOWNED)
        return redirect("summary", slug=slug, pk=session.pk)

    enemy = _current_enemy(session)
    hp_current, hp_max = enemy.hp(player) if enemy else (0, 0)
    return render(request, "core/strike.html", {
        "pack": pack, "session": session, "enemy": enemy,
        "item": card.item, "correct": correct, "correct_text": correct_text,
        "damage": round(damage, 1), "took_hit": took_hit, "was_new": was_new,
        "enemy_hp": hp_current, "enemy_hp_max": hp_max,
        "enemy_pct": 100 * (hp_max - hp_current) / hp_max if hp_max else 0,
    })


def summary(request, slug, pk):
    """Fim de expedição: vitória ou queda."""
    pack = get_object_or_404(Pack, slug=slug)
    session = get_object_or_404(Session, pk=pk, player=get_player())
    defeated = []
    for path in session.enemies[:session.enemy_index]:
        topic = Topic.objects.filter(pack=pack, path=path).first()
        if topic:
            defeated.append(topic)
    return render(request, "core/summary.html", {
        "pack": pack, "session": session, "defeated": defeated,
        "downed": session.outcome == Session.OUTCOME_DOWNED,
        "damage": round(session.damage, 1),
    })


def abandon(request, slug):
    """Sai da expedição sem perder nada — a fila fica intacta."""
    pack = get_object_or_404(Pack, slug=slug)
    session = _open_session(get_player(), pack)
    if session:
        _finish(session, Session.OUTCOME_ABANDONED)
        return redirect("summary", slug=slug, pk=session.pk)
    return redirect("home")
