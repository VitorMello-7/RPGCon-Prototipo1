"""core/admin.py — o admin é a sua ferramenta de curadoria de conteúdo.

Editar item aqui é legítimo e não mexe no Card. Mas o YAML é a fonte da
verdade: o próximo import sobrescreve. Corrija no YAML, reimporte.
"""

from django.contrib import admin

from .models import Card, Item, Pack, Review, Session, Topic


@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "exam_date", "intro_deadline", "imported_at")


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("path", "name", "weight", "item_count")
    list_filter = ("pack",)
    search_fields = ("path", "name")
    filter_horizontal = ("unlocks",)

    @admin.display(description="itens (subárvore)")
    def item_count(self, obj):
        return obj.review_items().count()


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("external_id", "kind", "pool", "topic", "retired", "source")
    list_filter = ("pack", "kind", "pool", "retired", "from_weakness")
    search_fields = ("external_id", "source", "payload")
    autocomplete_fields = ("topic",)


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("item", "player", "due", "stability", "reps", "lapses")
    list_filter = ("player",)
    readonly_fields = ("fsrs_state", "introduced_at")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("started_at", "player", "outcome", "hp_remaining", "is_boss")
    list_filter = ("outcome", "is_boss")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("reviewed_at", "card", "rating", "elapsed_ms", "was_correct")
    list_filter = ("rating", "was_correct", "was_new", "was_guess")
    readonly_fields = [f.name for f in Review._meta.fields]
