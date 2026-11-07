"""Pinned UTF-8 sources only; no clone, execution, arbitrary URLs or implicit full audit."""

import base64
import hashlib
import re
from pathlib import PurePosixPath
from urllib.parse import quote

from .github_reader import GitHubError

EXCLUDED = {"node_modules", "vendor", ".venv", "venv", "dist", "build", "target", "__pycache__"}
SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".rs",
    ".kt",
    ".kts",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".java",
    ".cs",
    ".go",
    ".swift",
    ".rb",
    ".php",
    ".sql",
    ".sh",
    ".ps1",
    ".html",
    ".css",
    ".vue",
    ".svelte",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".gradle",
}
MANIFESTS = {
    "readme",
    "readme.md",
    "readme.rst",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "cargo.toml",
    "build.gradle",
    "build.gradle.kts",
    "dockerfile",
    "compose.yaml",
    "docker-compose.yml",
}


def validate_repository(owner, repository):
    if (
        not re.fullmatch(r"[A-Za-z0-9-]{1,39}", owner)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repository)
        or repository in {".", ".."}
    ):
        raise GitHubError("Укажи имя своего репозитория без URL и слешей.")


def eligible(path):
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 300
        or "\\" in path
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
    ):
        return False
    parts = path.split("/")
    if any(not p or p in {".", ".."} or p.startswith(".") or p.lower() in EXCLUDED for p in parts):
        return False
    name = parts[-1].lower()
    if any(
        word in name for word in ["credential", "secret", "private_key", "token", "password"]
    ) or name.endswith((".pem", ".key")):
        return False
    return name in MANIFESTS or PurePosixPath(name).suffix in SUFFIXES


def inspect_sources(reader, repository, source=None, paths=None):
    owner = reader.settings.github_owner
    validate_repository(owner, repository)
    prefix = f"/repos/{owner}/{repository}"
    warnings = [
        "Прочитан ограниченный набор файлов. Код не запускался. Наличие кода не подтверждает его работу, эксплуатацию или всю компетенцию."
    ]
    if source:
        if source["repository"] != f"{owner}/{repository}" or not re.fullmatch(
            r"[a-f0-9]{40}", source["commit_sha"]
        ):
            raise GitHubError("Снимок относится к другому репозиторию или не содержит точного коммита.")
        sha, facts = source["commit_sha"], source["facts"]
        available = facts.get("available_files", [])
        chosen = {e["path"]: e for e in available}
        if (
            not paths
            or len(paths) > 12
            or len(paths) != len(set(paths))
            or any(not eligible(p) or p not in chosen for p in paths)
        ):
            raise GitHubError("Выбери до 12 разных файлов из сохранённого списка.")
        entries = [chosen[p] for p in paths]
        mode = "implementation"
        truncated = facts.get("tree_truncated", False)
        tree_entries = facts.get("tree_entries", 0)
        catalog_truncated = facts.get("catalog_truncated", False)
        meta = {"language": facts.get("declared_language"), "private": facts.get("private", True)}
        warnings.append(
            "Прочитаны выбранные файлы на коммите исходного снимка; более новая ветка не использовалась."
        )
    else:
        meta = reader.get(prefix)
        branch = meta.get("default_branch")
        if not branch:
            raise GitHubError("В репозитории ещё нет основной ветки.")
        commit = reader.get(f"{prefix}/commits/{quote(branch, safe='')}")
        sha, tree_sha = commit.get("sha", ""), commit.get("commit", {}).get("tree", {}).get("sha", "")
        if not all(re.fullmatch(r"[a-f0-9]{40}", v) for v in (sha, tree_sha)):
            raise GitHubError("GitHub вернул неподдерживаемый идентификатор коммита.")
        tree = reader.get(f"{prefix}/git/trees/{tree_sha}", recursive=1)
        truncated, tree_entries = bool(tree.get("truncated")), len(tree.get("tree", []))
        candidates = [
            e
            for e in tree.get("tree", [])
            if e.get("type") == "blob" and e.get("mode") in {"100644", "100755"} and eligible(e.get("path"))
        ]
        candidates.sort(key=lambda e: (e["path"].count("/"), e["path"]))
        catalog_truncated = len(candidates) > 500
        available = [{k: e.get(k) for k in ["path", "sha", "size"]} for e in candidates[:500]]
        manifests = [e for e in available if e["path"].rsplit("/", 1)[-1].lower() in MANIFESTS]
        entries, mode = manifests[:12], "catalog"
        warnings.append(
            "Первый снимок читает README/манифесты и предлагает список файлов реализации; зависимости не доказывают готовую реализацию."
        )
        if len(manifests) > 12:
            warnings.append("README/манифестов больше 12: прочитаны первые 12.")
    if truncated:
        warnings.append("GitHub обрезал дерево; список файлов неполон.")
    if catalog_truncated:
        warnings.append(
            "Список ограничен первыми 500 допустимыми файлами. Отсутствие файла в списке не означает отсутствие реализации."
        )
    files, total = [], 0
    for index, item in enumerate(entries):
        reader.report({"done": index, "total": len(entries)})
        size, blob_sha = item.get("size"), item.get("sha", "")
        if not isinstance(size, int) or size < 0 or not re.fullmatch(r"[a-f0-9]{40}", blob_sha):
            raise GitHubError("Недопустимые метаданные файла GitHub.")
        if size > 65536 or total + size > 131072:
            warnings.append(f"{item['path']}: пропущен по пределу размера.")
            continue
        blob = reader.get(f"{prefix}/git/blobs/{blob_sha}")
        try:
            if blob.get("encoding") != "base64" or blob.get("sha") != blob_sha:
                raise ValueError("Invalid blob")
            data = base64.b64decode("".join(blob["content"].split()), validate=True)
            if (
                len(data) != size
                or len(data) > 65536
                or hashlib.sha1(b"blob " + str(size).encode() + b"\0" + data).hexdigest() != blob_sha
            ):
                raise ValueError("Blob integrity mismatch")
            text = data.decode("utf-8")
            if "\0" in text:
                raise ValueError("Binary content")
        except (ValueError, KeyError, UnicodeError) as exc:
            raise GitHubError(
                "Файл не удалось прочитать как целый UTF-8 текст с проверенной контрольной суммой."
            ) from exc
        total += size
        files.append(
            {
                "path": item["path"],
                "sha": blob_sha,
                "bytes": size,
                "text": text,
                "lines": len(text.splitlines()),
                "kind": "declaration"
                if item["path"].rsplit("/", 1)[-1].lower() in MANIFESTS
                or PurePosixPath(item["path"]).suffix.lower()
                in {".md", ".rst", ".txt", ".toml", ".yaml", ".yml"}
                else "implementation",
            }
        )
    reader.report({"done": len(entries), "total": len(entries)})
    return {
        "repository": f"{owner}/{repository}",
        "commit_sha": sha,
        "facts": {
            "mode": mode,
            "declared_language": meta.get("language"),
            "private": bool(meta.get("private")),
            "files": files,
            "available_files": available,
            "catalog_truncated": catalog_truncated,
            "tree_truncated": truncated,
            "tree_entries": tree_entries,
            "warnings": warnings,
        },
    }
