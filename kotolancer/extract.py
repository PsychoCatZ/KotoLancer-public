"""Bounded text extraction. Run in a subprocess; never execute document contents."""

import json
import sys
import warnings
from pathlib import Path
from zipfile import ZipFile

MAX_TEXT = 300_000
MAX_PAGES = 50
MAX_UNPACKED = 64 * 1024 * 1024


def extract(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 25_000_000
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.verify()
        return {
            "status": "needs_visual",
            "blocks": [],
            "warnings": ["Изображение сохранено. Визуальный разбор появится после подключения AI."],
        }
    blocks = []
    notes = []
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("PDF защищён паролем. Загрузи незашифрованную копию.")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError(f"В PDF больше {MAX_PAGES} страниц. Раздели документ на части.")
        total = 0
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            total += len(text)
            if total > MAX_TEXT:
                raise ValueError("Превышен лимит извлечённого текста. Раздели PDF на части.")
            blocks.append({"location": f"Страница {number}", "text": text})
            if not text:
                notes.append(f"Страница {number}: текста нет; нужен визуальный разбор.")
        notes.append(
            "Извлечён только текст PDF. Сканы, схемы и полнота визуального содержания пока не проверены."
        )
        return {"status": "partial", "blocks": blocks, "warnings": notes}
    if suffix == ".docx":
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(entry.file_size for entry in entries) > MAX_UNPACKED:
                raise ValueError("Слишком большой распакованный DOCX.")
            images = sum(entry.filename.startswith("word/media/") for entry in entries)
        document = Document(path)
        total = 0
        for number, block in enumerate(document.iter_inner_content(), 1):
            if isinstance(block, Paragraph):
                text = block.text
            elif isinstance(block, Table):
                text = "\n".join(" | ".join(cell.text for cell in row.cells) for row in block.rows)
            else:
                continue
            total += len(text)
            if total > MAX_TEXT:
                raise ValueError("Превышен лимит извлечённого текста DOCX.")
            if text.strip():
                blocks.append({"location": f"Блок {number}", "text": text})
        if images:
            notes.append(f"В DOCX есть изображения ({images}). Их содержание пока не разобрано.")
        notes.append(
            "Извлечены абзацы и таблицы. Колонтитулы, текстовые поля и сложная вёрстка могут быть не учтены."
        )
        return {"status": "partial", "blocks": blocks, "warnings": notes}
    raise ValueError("Неподдерживаемый формат")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = extract(Path(sys.argv[1]))
    except ValueError as exc:
        result = {"status": "failed", "blocks": [], "warnings": [str(exc)]}
    except Exception:
        result = {
            "status": "failed",
            "blocks": [],
            "warnings": [
                "Не удалось разобрать файл. Оригинал сохранён; проверь его и загрузи исправленную копию."
            ],
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
