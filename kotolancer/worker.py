import time

from .ai_provider import OpenAIProvider
from .config import Settings
from .consultant import process_next
from .db import make_engine, session_factory
from .models import AIWorkerState, now


def main():
    settings = Settings()
    if not settings.ai_configured or settings.openai_total_budget_nusd <= 0:
        print(
            "OpenAI key, model or production allowance is missing. No worker started and no API calls made."
        )
        return
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    provider = OpenAIProvider(settings)
    try:
        while True:
            with factory() as db:
                marker = db.get(AIWorkerState, 1)
                if marker:
                    marker.heartbeat_at = now()
                else:
                    db.add(AIWorkerState(id=1))
                db.commit()
            if not process_next(factory, settings, provider):
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
