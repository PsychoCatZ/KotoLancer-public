"""Client-facing copy only; original requirements and internal analysis stay intact."""

import re


def platform_copy(text: str, platform: str) -> str:
    if platform.strip().casefold() == "kwork":
        name = r"(?:instagram|инстаграм(?:а|у|ом|е)?)"
        for prepositions, replacement in (
            ("из|для|от|у|с|без", "указанной в ТЗ социальной сети"),
            ("к", "указанной в ТЗ социальной сети"),
            ("в", "указанной в ТЗ социальной сети"),
            ("про", "указанную в ТЗ социальную сеть"),
        ):
            text = re.sub(
                rf"\b({prepositions})\s+{name}(?!\w)",
                lambda match, replacement=replacement: f"{match[1]} {replacement}",
                text,
                flags=re.IGNORECASE,
            )
        text = re.sub(
            rf"(?<!\w){name}(?!\w)",
            "указанная в ТЗ социальная сеть",
            text,
            flags=re.IGNORECASE,
        )
    return text


def clarification_message(result: dict, platform: str) -> str | None:
    if result.get("recommendation") == "decline" or not result.get("questions"):
        return None
    # Old analyses have no client_message. Compose their saved questions without an AI call.
    text = result.get("client_message") or (
        "Здравствуйте! Чтобы уточнить объём работы, подскажите, пожалуйста:\n\n"
        + "\n\n".join(f"{i}. {q['text']}" for i, q in enumerate(result["questions"], 1))
    )
    return platform_copy(text, platform)
