"""
core/management/commands/backup_db.py

    python manage.py backup_db

Backup do 4.5. Usa a API de backup do sqlite3 do próprio Python: copia o banco
de forma consistente mesmo com o runserver ativo, e não depende do sqlite3.exe,
que não vem instalado no Windows.

O db.sqlite3 é a única coisa do projeto que não se reconstrói: o conteúdo está
no git, o código está no git, mas seis semanas de estado FSRS moram só aqui.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copia o banco para a pasta de backups, mantendo os N mais recentes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dest", default=None,
            help="pasta de destino (padrão: BACKUP_DIR do settings ou ./backups)",
        )
        parser.add_argument(
            "--keep", type=int, default=30,
            help="quantos backups manter (padrão: 30)",
        )

    def handle(self, *args, **options):
        source = Path(settings.DATABASES["default"]["NAME"])
        if not source.exists():
            raise CommandError(f"banco não encontrado: {source}")

        dest_dir = Path(
            options["dest"]
            or getattr(settings, "BACKUP_DIR", None)
            or settings.BASE_DIR / "backups"
        )
        dest_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = dest_dir / f"db-{stamp}.sqlite3"

        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        size_kb = target.stat().st_size / 1024
        self.stdout.write(f"{target} ({size_kb:.0f} KB)")

        # rotação
        backups = sorted(dest_dir.glob("db-*.sqlite3"))
        excess = backups[:-options["keep"]] if options["keep"] > 0 else []
        for old in excess:
            old.unlink()
        if excess:
            self.stdout.write(f"{len(excess)} backup(s) antigo(s) removido(s)")

        self.stdout.write(self.style.SUCCESS(
            f"{len(backups) - len(excess)} backup(s) na pasta"
        ))
