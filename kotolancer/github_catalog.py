"""Explicit, bounded metadata refresh. No source reads or profile mutations."""

import copy
import re
import threading
import time
from datetime import UTC, datetime

from .github_reader import GitHubError, GitHubReader
from .github_sources import validate_repository


def fetch_catalog(reader):
    owner = reader.settings.github_owner
    validate_repository(owner, "catalog")
    reader.deadline, reader.requests = time.monotonic() + 60, 0
    repositories = {}
    limited = False
    for page in range(1, 6):
        rows = reader.get(
            "/user/repos",
            affiliation="owner",
            visibility="all",
            sort="full_name",
            direction="asc",
            per_page=100,
            page=page,
        )
        if not isinstance(rows, list) or len(rows) > 100:
            raise GitHubError("GitHub вернул неподдерживаемый список проектов.")
        for row in rows:
            if not isinstance(row, dict):
                raise GitHubError("GitHub вернул неподдерживаемый список проектов.")
            if str((row.get("owner") or {}).get("login", "")).casefold() != owner.casefold():
                continue
            name = row.get("name")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name)
                or name in {".", ".."}
            ):
                raise GitHubError("GitHub вернул неподдерживаемое имя проекта.")
            repositories[name.casefold()] = {
                "name": name,
                "description": str(row.get("description") or "")[:400],
                "language": str(row.get("language") or "")[:50],
                "private": bool(row.get("private", True)),
                "archived": bool(row.get("archived")),
                "fork": bool(row.get("fork")),
            }
        if len(rows) < 100:
            break
        limited = page == 5
    return {
        "owner": owner,
        "repositories": sorted(repositories.values(), key=lambda r: r["name"].casefold()),
        "fetched_at": datetime.now(UTC).isoformat(),
        "possibly_incomplete": limited,
    }


class CatalogBusy(Exception):
    pass


class RepositoryCatalog:
    def __init__(self, settings, reader=None):
        self.settings, self.reader = settings, reader
        self.snapshot = None
        self.lock = threading.Lock()
        self.last_attempt = float("-inf")

    def view(self):
        return {"catalog": copy.deepcopy(self.snapshot), "refreshing": self.lock.locked()}

    def refresh(self):
        if not self.lock.acquire(blocking=False):
            raise CatalogBusy("Список уже обновляется. Дождись результата.")
        try:
            if time.monotonic() - self.last_attempt < 10:
                raise CatalogBusy("Повторное обновление доступно через несколько секунд.")
            self.last_attempt = time.monotonic()
            reader = self.reader or GitHubReader(self.settings)
            try:
                snapshot = fetch_catalog(reader)
            finally:
                if self.reader is None:
                    reader.client.close()
            self.snapshot = snapshot
            return {"catalog": copy.deepcopy(snapshot), "refreshing": False}
        finally:
            self.lock.release()
