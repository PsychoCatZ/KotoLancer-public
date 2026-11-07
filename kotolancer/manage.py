import argparse

from alembic import command
from alembic.config import Config

from .config import ROOT, Settings
from .db import make_engine, session_factory
from .profile import initialize_profile


def migrate(url: str):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


def main():
    parser = argparse.ArgumentParser(description="KotoLancer database management")
    parser.add_argument("action", choices=["init", "migrate"])
    args = parser.parse_args()
    settings = Settings()
    migrate(settings.database_url)
    if args.action == "init":
        engine = make_engine(settings.database_url)
        try:
            with session_factory(engine)() as session:
                created = initialize_profile(session)
            print("Initial profile imported." if created else "Profile already exists; no data overwritten.")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
