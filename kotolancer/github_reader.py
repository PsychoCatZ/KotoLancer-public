"""Bounded GET-only GitHub inspection. Never execute repository code."""

import time

import httpx


class GitHubError(Exception):
    pass


class GitHubReader:
    def __init__(self, settings, client=None, progress=None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.progress = progress
        self.deadline = time.monotonic() + 60
        self.requests = 0

    def report(self, value):
        if time.monotonic() >= self.deadline:
            raise GitHubError("Истёк общий срок чтения GitHub. Сузь выбранный набор файлов.")
        if self.progress:
            self.progress(value)

    def get(self, path, **params):
        self.report({})
        self.requests += 1
        if self.requests > 20:
            raise GitHubError("Достигнут предел запросов одного просмотра GitHub.")
        token = self.settings.github_token.get_secret_value()
        if not token:
            raise GitHubError("Сохрани отдельный read-only токен GitHub.")
        try:
            with self.client.stream(
                "GET",
                f"https://api.github.com{path}",
                params=params,
                timeout=min(30, max(0.1, self.deadline - time.monotonic())),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
            ) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    self.report({})
                    content.extend(chunk)
                    if len(content) > 2 * 1024 * 1024:
                        raise GitHubError("Ответ GitHub превышает предел. Нужен более узкий просмотр.")
                import json

                return json.loads(content)
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubError(
                "GitHub не разрешил чтение или недоступен. Проверь репозитории, срок и права токена."
            ) from exc

    def inspect(self, repository, source=None, paths=None):
        from .github_sources import inspect_sources

        self.deadline = time.monotonic() + 60
        self.requests = 0
        return inspect_sources(self, repository, source, paths)
