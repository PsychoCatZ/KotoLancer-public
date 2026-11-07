"""Conservative, durable generation allowance. Not an invoice or account-wide billing limit."""

from openai import APIError
from sqlalchemy import func, select

from .ai_context import freshness
from .ai_provider import ProviderError
from .models import AIJob, AIRun, Order, ProfileHead, now

# Official Standard Luna upper rates, including >272K and cache-write pricing, 2026-10-17.
INPUT_NUSD = 250
OUTPUT_NUSD = 750
MAX_INPUT = 200_000


def summary(db, settings):
    reserved = int(db.scalar(select(func.coalesce(func.sum(AIRun.reserved_nusd), 0))) or 0)
    return {
        "limit_nusd": settings.openai_total_budget_nusd,
        "reserved_nusd": reserved,
        "remaining_nusd": max(0, settings.openai_total_budget_nusd - reserved),
        "is_billing_statement": False,
        "policy": "luna-standard-upper-20261017",
    }


class ProductionBudget:
    def __init__(self, factory, settings, job_id, lease):
        self.factory, self.settings, self.job_id, self.lease = factory, settings, job_id, lease

    def reserve(self, client, arguments):
        if self.settings.openai_total_budget_nusd <= 0:
            raise ProviderError(
                "budget_not_approved",
                "Для рабочих запросов ещё не согласован денежный предел. Генерация не отправлена.",
            )
        if (
            arguments.get("model") != "gpt-6-luna"
            or arguments.get("service_tier") != "default"
            or arguments.get("tools")
            or arguments.get("reasoning") != {"effort": "low"}
            or not 1 <= arguments.get("max_output_tokens", 0) <= 4000
        ):
            raise ProviderError(
                "budget_policy_mismatch",
                "Настройки модели не соответствуют согласованному режиму. Генерация не отправлена.",
            )
        with self.factory() as db:
            if summary(db, self.settings)["remaining_nusd"] <= 0:
                raise ProviderError(
                    "budget_exhausted", "Расчётный денежный предел исчерпан. Генерация не отправлена."
                )
        count_args = {
            k: v for k, v in arguments.items() if k not in {"store", "max_output_tokens", "service_tier"}
        }
        try:
            count = client.responses.input_tokens.count(**count_args).input_tokens
        except APIError as exc:
            raise ProviderError(
                "token_count_failed",
                "Не удалось подсчитать вход. Генерация не отправлялась; автоматического повтора нет.",
            ) from exc
        if type(count) is not int or not 1 <= count <= MAX_INPUT:
            raise ProviderError(
                "input_token_limit",
                "Вход превышает предел безопасного расчёта или не подсчитан. Раздели материалы. Генерация не отправлена.",
            )
        amount = ((count * 11 + 9) // 10 + 1024) * INPUT_NUSD + arguments["max_output_tokens"] * OUTPUT_NUSD
        with self.factory() as db:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            db.scalar(select(ProfileHead).where(ProfileHead.id == 1).with_for_update())
            job = db.scalar(select(AIJob).where(AIJob.id == self.job_id).with_for_update())
            run = db.get(AIRun, self.job_id)
            if (
                not job
                or not run
                or job.status != "running"
                or job.lease_token != self.lease
                or job.lease_until is None
                or job.lease_until.replace(tzinfo=now().tzinfo) <= now()
            ):
                raise ProviderError(
                    "budget_lease_lost", "Запрос потерял право на запуск. Генерация не отправлена."
                )
            order = (
                db.scalar(select(Order).where(Order.id == job.order_id).with_for_update())
                if job.order_id
                else None
            )
            if freshness(db, job, order)[0]:
                raise ProviderError(
                    "budget_context_changed",
                    "Условия изменились до отправки. Создай новый запрос; генерация не отправлена.",
                )
            if job.context.get("model") != arguments["model"] or run.model != arguments["model"]:
                raise ProviderError(
                    "budget_policy_mismatch", "Модель задания изменилась. Генерация не отправлена."
                )
            if run.reserved_nusd:
                raise ProviderError(
                    "budget_already_reserved",
                    "Этот запрос уже резервировался. Повторная генерация запрещена.",
                )
            if summary(db, self.settings)["reserved_nusd"] + amount > self.settings.openai_total_budget_nusd:
                raise ProviderError(
                    "budget_exhausted",
                    "Для запроса не хватает расчётного денежного резерва. Генерация не отправлена.",
                )
            run.reserved_nusd, run.counted_input_tokens = amount, count
            db.commit()  # Durable before responses.create; no release after unknown outcome or restart.
        return amount
