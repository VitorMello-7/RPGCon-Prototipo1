"""
core/views.py — modo simples (0.2).

Sem HP, sem inimigos, sem mapa: pergunta -> resposta -> FSRS grava. E a apolice
para os dias em que o combate for atrito, e a base sobre a qual o bloco 4
constroi.

Tempo e medido no servidor (4.3, page reload puro): o `started_at` vai para a
sessao do Django quando a pergunta e renderizada.
"""

from __future__ import annotations

import datetime as dt
import random

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from fsrs import Rating

from .models import Card, Item, Pack
from .scheduling import apply_review, derive_rating, next_card, remaining_today

MAX_ELAPSED_MS = 120_000  # trava o card esquecido aberto a noite toda


def get_player():
    """v1 e monousuario (0.4). Vira request.user quando houver login."""
    return get_user_model().objects.order_by("id").first()


def _start_timer(request, card):
    request.session["card_id"] = card.pk
    request.session["started_at"] = timezone.now().isoformat()


def _elapsed_ms(request) -> int:
    started = request.session.get("started_at")
    if not started:
        return MAX_ELAPSED_MS
    delta = timezone.now() - dt.datetime.fromisoformat(started)
    return min(int(delta.total_seconds() * 1000), MAX_ELAPSED_MS)


def study(request, slug):
    """Mostra o proximo item da fila."""
    pack = get_object_or_404(Pack, slug=slug)
    player = get_player()

    card, is_new = next_card(player, pack)
    if card is None:
        return render(request, "core/done.html", {"pack": pack})

    _start_timer(request, card)

    choices = None
    if card.item.kind == Item.KIND_MCQ:
        # 2.5: alternativas sempre embaralhadas
        choices = list(enumerate(card.item.payload["choices"]))
        random.shuffle(choices)

    return render(request, "core/study.html", {
        "pack": pack,
        "card": card,
        "item": card.item,
        "is_new": is_new,
        "choices": choices,
        "remaining": remaining_today(player, pack),
    })


@require_POST
def answer(request, slug):
    """Recebe a resposta, deriva o rating (3.1) e grava."""
    pack = get_object_or_404(Pack, slug=slug)
    player = get_player()
    card = Card.objects.filter(
        pk=request.session.get("card_id"), player=player
    ).select_related("item", "item__pack").first()
    if card is None:
        # sessao expirada ou botao voltar: volta para a fila em vez de 500
        return redirect("study", slug=slug)

    elapsed = _elapsed_ms(request)
    was_guess = bool(request.POST.get("guess"))

    correct_text = None
    if card.item.kind == Item.KIND_MCQ:
        if "choice" not in request.POST:
            return redirect("study", slug=slug)
        chosen = int(request.POST["choice"])
        correct = chosen == card.item.payload["answer"]
        correct_text = card.item.payload["choices"][card.item.payload["answer"]]
        rating = derive_rating(
            correct=correct,
            elapsed_ms=elapsed,
            is_mature=card.is_mature,
            fast_seconds=pack.fast_answer_seconds,
            was_guess=was_guess,
        )
    else:
        # cloze: autoavaliacao nos 4 botoes (3.1)
        if "rating" not in request.POST:
            return redirect("study", slug=slug)
        rating = Rating(int(request.POST["rating"]))
        correct = None
        chosen = None

    stability_before = card.stability
    apply_review(card, rating, elapsed_ms=elapsed,
                 correct=correct, was_guess=was_guess)

    return render(request, "core/result.html", {
        "pack": pack,
        "card": card,
        "item": card.item,
        "correct": correct,
        "chosen": chosen,
        "correct_text": correct_text,
        "rating": rating.name,
        "elapsed_s": round(elapsed / 1000, 1),
        "grew": card.stability > stability_before,
    })


def reveal(request, slug):
    """Cloze: mostra o verso e os 4 botoes, sem gravar nada ainda."""
    pack = get_object_or_404(Pack, slug=slug)
    player = get_player()
    card = Card.objects.filter(
        pk=request.session.get("card_id"), player=player
    ).select_related("item").first()
    if card is None:
        return redirect("study", slug=slug)
    return render(request, "core/study.html", {
        "pack": pack,
        "card": card,
        "item": card.item,
        "revealed": True,
        "remaining": remaining_today(player, pack),
    })


def home(request):
    pack = Pack.objects.first()
    if pack is None:
        return render(request, "core/done.html", {"pack": None})
    return redirect("study", slug=pack.slug)
