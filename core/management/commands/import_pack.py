"""
core/management/commands/import_pack.py

    python manage.py import_pack packs/dataprev-2026-p6

Garantias (1.4):
- Upsert por (pack, external_id). Corrigir um item NUNCA reseta o Card.
- Item que sumiu do YAML é aposentado, não deletado.
- Nada é gravado se a validação falhar: a transação inteira volta atrás.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Item, Pack, Topic


MANIFEST_FIELDS = [
    "name", "exam_date", "intro_window_days", "review_only_days",
    "desired_retention", "mature_threshold_days", "block_size",
    "blocks_per_session", "player_hp", "fast_answer_seconds",
    "boss_unlock_ratio",
]


class Command(BaseCommand):
    help = "Importa (ou reimporta) um pack de conteúdo a partir de YAML."

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="pasta do pack")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="valida e mostra o relatório sem gravar nada",
        )

    def handle(self, *args, **options):
        root = Path(options["path"])
        manifest_file = root / "manifest.yaml"
        if not manifest_file.exists():
            raise CommandError(f"manifest não encontrado: {manifest_file}")

        manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
        slug = manifest.get("slug")
        if not slug:
            raise CommandError("manifest sem 'slug'")

        report = {"topics": 0, "created": 0, "updated": 0, "unchanged": 0, "retired": 0}

        try:
            with transaction.atomic():
                pack = self._upsert_pack(slug, manifest)
                topics = self._load_topics(root, manifest, pack, report)
                self._load_items(root, manifest, pack, topics, report)
                if options["dry_run"]:
                    raise _DryRun()
        except _DryRun:
            self.stdout.write(self.style.WARNING("dry-run: nada foi gravado"))

        self.stdout.write(
            f"{report['topics']} tópicos · "
            f"{report['created']} novos · {report['updated']} atualizados · "
            f"{report['unchanged']} sem mudança · {report['retired']} aposentados"
        )
        if not options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"pack '{slug}' importado"))

    # ------------------------------------------------------------------ pack

    def _upsert_pack(self, slug: str, manifest: dict) -> Pack:
        defaults = {k: manifest[k] for k in MANIFEST_FIELDS if k in manifest}
        pack, _ = Pack.objects.update_or_create(slug=slug, defaults=defaults)
        return pack

    # ---------------------------------------------------------------- topics

    def _load_topics(self, root: Path, manifest: dict, pack: Pack, report: dict) -> dict:
        """Cria/atualiza tópicos. O 'id' é o path; o parent sai do split (1.3)."""
        raw: dict[str, dict] = {}
        for filename in manifest.get("files", []):
            doc = yaml.safe_load((root / filename).read_text(encoding="utf-8")) or {}
            for entry in doc.get("topics", []):
                path = entry["id"].strip("/")
                if path in raw:
                    raise CommandError(f"tópico duplicado: {path}")
                raw[path] = entry

        # pais antes dos filhos
        topics: dict[str, Topic] = {}
        for path in sorted(raw, key=lambda p: (p.count("/"), p)):
            entry = raw[path]
            parent_path = path.rsplit("/", 1)[0] if "/" in path else None
            if parent_path and parent_path not in topics:
                raise CommandError(f"tópico '{path}' sem pai '{parent_path}' definido")
            topic, _ = Topic.objects.update_or_create(
                pack=pack,
                external_id=path,
                defaults={
                    "name": entry["name"],
                    "parent": topics.get(parent_path),
                    "weight": entry.get("weight", 1.0),
                    "order": entry.get("order", 0),
                },
            )
            topics[path] = topic
            report["topics"] += 1

        # desbloqueios só depois que todos existem
        for path, entry in raw.items():
            targets = []
            for target in entry.get("unlocks", []):
                target = target.strip("/")
                if target not in topics:
                    raise CommandError(
                        f"tópico '{path}' desbloqueia '{target}', que não existe "
                        f"no pack — confira se o arquivo dele está em 'files'"
                    )
                targets.append(topics[target])
            topics[path].unlocks.set(targets)

        return topics

    # ----------------------------------------------------------------- items

    def _load_items(self, root: Path, manifest: dict, pack: Pack,
                    topics: dict, report: dict) -> None:
        seen: set[str] = set()

        for filename in manifest.get("files", []):
            doc = yaml.safe_load((root / filename).read_text(encoding="utf-8")) or {}
            for entry in doc.get("items", []):
                external_id = entry["id"]
                if external_id in seen:
                    raise CommandError(f"item duplicado: {external_id}")
                seen.add(external_id)

                topic_path = entry["topic"].strip("/")
                if topic_path not in topics:
                    raise CommandError(
                        f"item '{external_id}' aponta para tópico inexistente "
                        f"'{topic_path}'"
                    )

                kind = entry["kind"]
                payload = self._payload_for(kind, entry, external_id)

                fields = {
                    "topic": topics[topic_path],
                    "kind": kind,
                    "pool": entry.get("pool", Item.POOL_REVIEW),
                    "payload": payload,
                    "source": entry["source"],       # 1.5, obrigatório
                    "comment": entry.get("comment", ""),
                    "concept_id": entry.get("concept_id", ""),
                    "retired": False,
                }

                existing = Item.objects.filter(
                    pack=pack, external_id=external_id
                ).first()

                if existing is None:
                    item = Item(pack=pack, external_id=external_id, **fields)
                    item.full_clean(exclude=["pack"])
                    item.save()
                    report["created"] += 1
                    continue

                changed = any(
                    getattr(existing, key) != value for key, value in fields.items()
                )
                if not changed:
                    report["unchanged"] += 1
                    continue

                for key, value in fields.items():
                    setattr(existing, key, value)
                existing.full_clean(exclude=["pack"])
                existing.save()  # o Card e o estado FSRS não são tocados (1.4)
                report["updated"] += 1

        # 1.4: sumiu do YAML → aposenta, não deleta
        retired = Item.objects.filter(pack=pack, retired=False).exclude(
            external_id__in=seen
        )
        report["retired"] = retired.count()
        retired.update(retired=True)

    def _payload_for(self, kind: str, entry: dict, external_id: str) -> dict:
        try:
            if kind == Item.KIND_CLOZE:
                return {"front": entry["front"], "back": entry["back"]}
            if kind == Item.KIND_MCQ:
                return {
                    "stem": entry["stem"],
                    "choices": entry["choices"],
                    "answer": entry["answer"],
                }
        except KeyError as exc:
            raise CommandError(f"item '{external_id}': campo faltando {exc}")
        raise CommandError(f"item '{external_id}': kind desconhecido '{kind}'")


class _DryRun(Exception):
    """Aborta a transação no modo dry-run."""
