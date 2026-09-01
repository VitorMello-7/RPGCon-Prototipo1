"""
core/management/commands/export_anki.py

    python manage.py export_anki dataprev-2026-p6

A apólice do 0.2: se o jogo não pegar, as horas gastas digitando conteúdo não
se perdem. Gera um .apkg com subdecks espelhando a hierarquia de tópicos.

O que o export NÃO carrega: o estado FSRS. O Anki reagenda do zero. Isso é
proposital — o export é seguro de conteúdo, não backup de progresso. Para o
progresso existe o `backup_db`.

Testado com genanki 0.13.1.
"""

from __future__ import annotations

import html
from pathlib import Path

import genanki
from django.core.management.base import BaseCommand, CommandError

from core.models import Item, Pack

# IDs fixos: mudar isso faz o Anki tratar tudo como nota nova na reimportação.
MODEL_BASIC_ID = 1607392319
MODEL_MCQ_ID = 1607392320

CSS = """
.card { font-family: -apple-system, Segoe UI, sans-serif; font-size: 18px;
        text-align: left; color: #eee; background: #1b1b1b; padding: 1rem; }
.src  { color: #888; font-size: 13px; margin-top: 1rem; }
ol    { margin: .5rem 0 0 1.2rem; }
"""

MODEL_BASIC = genanki.Model(
    MODEL_BASIC_ID, "Concurso — Cloze",
    fields=[{"name": "Frente"}, {"name": "Verso"}, {"name": "Fonte"}],
    templates=[{
        "name": "Card 1",
        "qfmt": "{{Frente}}",
        "afmt": '{{FrontSide}}<hr id="answer">{{Verso}}'
                '<div class="src">{{Fonte}}</div>',
    }],
    css=CSS,
)

MODEL_MCQ = genanki.Model(
    MODEL_MCQ_ID, "Concurso — Múltipla escolha",
    fields=[{"name": "Enunciado"}, {"name": "Alternativas"},
            {"name": "Gabarito"}, {"name": "Fonte"}],
    templates=[{
        "name": "Card 1",
        "qfmt": "{{Enunciado}}{{Alternativas}}",
        "afmt": '{{FrontSide}}<hr id="answer"><b>{{Gabarito}}</b>'
                '<div class="src">{{Fonte}}</div>',
    }],
    css=CSS,
)


class Command(BaseCommand):
    help = "Exporta um pack para .apkg do Anki."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--out", default=None, help="caminho do .apkg")
        parser.add_argument(
            "--include-exam", action="store_true",
            help="inclui o pool de simulado (por padrão só o de revisão)",
        )

    def handle(self, *args, **options):
        try:
            pack = Pack.objects.get(slug=options["slug"])
        except Pack.DoesNotExist:
            raise CommandError(f"pack '{options['slug']}' não encontrado")

        items = Item.objects.filter(pack=pack, retired=False).select_related("topic")
        if not options["include_exam"]:
            items = items.filter(pool=Item.POOL_REVIEW)
        if not items.exists():
            raise CommandError("nenhum item para exportar")

        decks: dict[str, genanki.Deck] = {}
        for item in items.order_by("topic__path", "id"):
            deck = self._deck_for(decks, pack, item)
            deck.add_note(self._note_for(pack, item))

        out = Path(options["out"] or f"{pack.slug}.apkg")
        genanki.Package(list(decks.values())).write_to_file(out)

        self.stdout.write(
            f"{items.count()} itens · {len(decks)} subdecks → {out.resolve()}"
        )
        self.stdout.write(self.style.WARNING(
            "o .apkg carrega conteúdo, não o progresso FSRS"
        ))

    # ------------------------------------------------------------------

    def _deck_for(self, decks, pack, item) -> genanki.Deck:
        """Subdeck por tópico: 'Pack::itil::praticas' vira hierarquia no Anki."""
        name = pack.name + "::" + item.topic.path.replace("/", "::")
        if name not in decks:
            decks[name] = genanki.Deck(
                abs(hash(name)) % (10 ** 10) + 10 ** 9, name
            )
        return decks[name]

    def _note_for(self, pack, item) -> genanki.Note:
        # guid derivado do external_id: reimportar atualiza em vez de duplicar
        guid = genanki.guid_for(pack.slug, item.external_id)
        tags = [item.topic.path.replace("/", "::"), item.pool]

        if item.kind == Item.KIND_MCQ:
            choices = item.payload["choices"]
            listed = "<ol>" + "".join(
                f"<li>{html.escape(str(c))}</li>" for c in choices
            ) + "</ol>"
            gabarito = html.escape(str(choices[item.payload["answer"]]))
            if item.comment:
                gabarito += f"<br><br>{html.escape(item.comment)}"
            return genanki.Note(
                model=MODEL_MCQ, guid=guid, tags=tags,
                fields=[html.escape(item.payload["stem"]), listed,
                        gabarito, html.escape(item.source)],
            )

        verso = html.escape(item.payload["back"])
        if item.comment:
            verso += f"<br><br>{html.escape(item.comment)}"
        return genanki.Note(
            model=MODEL_BASIC, guid=guid, tags=tags,
            fields=[html.escape(item.payload["front"]), verso,
                    html.escape(item.source)],
        )
