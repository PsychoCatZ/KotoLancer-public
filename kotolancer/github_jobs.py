"""Durable owner-triggered reads. Crash/expiry never triggers a network retry."""

import copy
import threading
from datetime import UTC, timedelta

from sqlalchemy import select

from .github_reader import GitHubError, GitHubReader
from .models import GitHubReadJob, GitHubSnapshot, now, uid


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def process_read(factory, settings, reader=None, stop=None):
    with factory() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        for expired in db.scalars(
            select(GitHubReadJob)
            .where(GitHubReadJob.status == "running", GitHubReadJob.lease_until < now())
            .with_for_update()
        ):
            expired.status, expired.error, expired.finished_at = (
                "failed",
                "Чтение прервано или истёк срок. Новый запуск возможен только вручную.",
                now(),
            )
        job = db.scalar(
            select(GitHubReadJob)
            .where(GitHubReadJob.status == "queued")
            .order_by(GitHubReadJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            db.commit()
            return False
        job.status, job.lease, job.lease_until = "running", uid(), now() + timedelta(seconds=120)
        job_id, lease, repository, paths = job.id, job.lease, job.repository, list(job.paths)
        base = db.get(GitHubSnapshot, job.source_snapshot_id) if job.source_snapshot_id else None
        source = (
            {"repository": base.repository, "commit_sha": base.commit_sha, "facts": copy.deepcopy(base.facts)}
            if base
            else None
        )
        db.commit()

    def progress(value):
        if stop and stop.is_set():
            raise GitHubError("Чтение остановлено при выключении API. Для повтора нужен ручной запуск.")
        with factory() as db:
            current = db.get(GitHubReadJob, job_id)
            if (
                not current
                or current.status != "running"
                or current.lease != lease
                or utc(current.lease_until) < now()
            ):
                raise GitHubError("Чтение отменено или его срок истёк.")
            if value:
                current.progress = value
                db.commit()

    active = reader or GitHubReader(settings, progress=progress)
    original_progress = active.progress
    active.progress = progress
    result, error = None, ""
    try:
        result = active.inspect(repository, source, paths)
        progress({})
    except GitHubError as exc:
        error = str(exc)
    except Exception:
        error = "Не удалось завершить чтение. Результат не сохранён, автоматического повтора нет."
    finally:
        active.progress = original_progress
        if reader is None:
            active.client.close()
    with factory() as db:
        current = db.scalar(select(GitHubReadJob).where(GitHubReadJob.id == job_id).with_for_update())
        if current.status != "running" or current.lease != lease:
            return True
        if utc(current.lease_until) < now():
            error = "Результат пришёл после истечения срока и не сохранён."
        if error:
            current.status, current.error = "failed", error
        else:
            snapshot = GitHubSnapshot(**result)
            db.add(snapshot)
            db.flush()
            current.status, current.snapshot_id = "succeeded", snapshot.id
        current.finished_at = now()
        db.commit()
    return True


def start_reader(factory, settings):
    stop = threading.Event()

    def run():
        while not stop.is_set():
            try:
                worked = process_read(factory, settings, stop=stop)
            except Exception:
                # A lease abandoned by a storage failure expires; never expose raw DB/token errors.
                worked = False
            if not worked:
                stop.wait(2)

    thread = threading.Thread(target=run, name="kotolancer-github-read", daemon=True)
    thread.start()
    return stop, thread
