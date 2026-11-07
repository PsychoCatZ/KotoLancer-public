from dataclasses import dataclass

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError

from .ai_context import input_content
from .ai_schemas import RESULT_TYPES
from .config import Settings


@dataclass
class ProviderResult:
    text: str
    model: str
    response_id: str | None = None
    usage: dict | None = None


class ProviderError(Exception):
    def __init__(self, code, message, result: ProviderResult | None = None):
        super().__init__(message)
        self.code, self.message, self.result = code, message, result


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client

    def generate(self, kind: str, context: dict, *, guard=None):
        client = self.client or OpenAI(
            api_key=self.settings.openai_api_key.get_secret_value(),
            base_url="https://api.openai.com/v1",
            timeout=self.settings.openai_timeout_seconds,
            max_retries=0,
        )
        try:
            return self._generate(client, kind, context, guard=guard)
        finally:
            if self.client is None:
                client.close()

    def _generate(self, client, kind: str, context: dict, *, guard=None):
        schema = RESULT_TYPES[kind].model_json_schema()
        if kind == "github_review":
            from .github_review import constrain_schema

            constrain_schema(schema, context)

        def constrain_sources(node):
            if isinstance(node, dict):
                for name, value in node.items():
                    if name == "source_ids" and isinstance(value, dict) and value.get("type") == "array":
                        value["items"] = {"type": "string", "enum": sorted(context["sources"])}
                    else:
                        constrain_sources(value)
            elif isinstance(node, list):
                for child in node:
                    constrain_sources(child)

        constrain_sources(schema)
        arguments = {
            "model": context["model"],
            "store": False,
            "service_tier": "default",
            "instructions": context["rules"]["text"],
            "input": [{"role": "user", "content": input_content(context, self.settings)}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"kotolancer_{kind}_v1",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": self.settings.openai_max_output_tokens,
        }
        if context["reasoning_effort"]:
            arguments["reasoning"] = {"effort": context["reasoning_effort"]}
        try:
            if guard is not None:
                guard.reserve(client, arguments)
            response = client.responses.create(**arguments)
        except AuthenticationError as exc:
            raise ProviderError(
                "authentication", "OpenAI не принял ключ. Проверь настройки доступа."
            ) from exc
        except RateLimitError as exc:
            raise ProviderError(
                "rate_limit",
                "OpenAI ограничил запрос. Проверь доступный баланс и лимиты; автоматического повтора нет.",
            ) from exc
        except (APITimeoutError, APIConnectionError) as exc:
            raise ProviderError(
                "unknown_outcome",
                "Связь с OpenAI прервалась. Списание возможно; результат неизвестен. Автоматического повтора нет.",
            ) from exc
        except APIError as exc:
            raise ProviderError(
                "api_error", "OpenAI вернул ошибку. Проверь модель и настройки; автоматического повтора нет."
            ) from exc
        result = ProviderResult(
            text=response.output_text,
            model=response.model,
            response_id=response.id,
            usage=response.usage.model_dump() if response.usage else None,
        )
        if response.status != "completed":
            raise ProviderError(
                "incomplete", "OpenAI не завершил ответ. Он не сохранён как готовая оценка.", result
            )
        if any(
            getattr(part, "type", "") == "refusal"
            for output in response.output
            for part in getattr(output, "content", [])
        ):
            raise ProviderError(
                "refusal", "Модель отказалась обрабатывать запрос. Готовый анализ не создан.", result
            )
        return result
