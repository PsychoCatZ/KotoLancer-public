from urllib.parse import quote

from fastapi import HTTPException
from pydantic import Field

from .schemas import StrictModel


class CitationInput(StrictModel):
    path: str = Field(min_length=1, max_length=300)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


def location_url(location):
    return f"https://github.com/{location['repository']}/blob/{location['commit_sha']}/{quote(location['path'], safe='/')}#L{location['line_start']}-L{location['line_end']}"


def citations_from_snapshot(snapshot, inputs):
    files = {f["path"]: f for f in snapshot.facts["files"]}
    result = []
    for value in inputs:
        file = files.get(value.path)
        if not file:
            raise HTTPException(422, "Основание должно ссылаться на прочитанный файл этого снимка.")
        lines = file["text"].splitlines()
        if (
            value.line_end < value.line_start
            or value.line_end > len(lines)
            or value.line_end - value.line_start >= 80
        ):
            raise HTTPException(422, "Выбери существующий диапазон до 80 строк.")
        excerpt = "\n".join(lines[value.line_start - 1 : value.line_end])
        if not excerpt.strip() or len(excerpt) > 4000:
            raise HTTPException(422, "Пустой или слишком большой фрагмент. Сузь диапазон строк.")
        result.append(
            {
                "repository": snapshot.repository,
                "commit_sha": snapshot.commit_sha,
                "path": value.path,
                "blob_sha": file["sha"],
                "line_start": value.line_start,
                "line_end": value.line_end,
                "excerpt": excerpt,
                "kind": file.get("kind", "declaration"),
            }
        )
    if len({(x["path"], x["line_start"], x["line_end"]) for x in result}) != len(result):
        raise HTTPException(422, "Не повторяй один и тот же фрагмент.")
    return result
