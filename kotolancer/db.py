from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    options = {"check_same_thread": False, "timeout": 20} if url.startswith("sqlite") else {}
    # Three resident processes must fit PostgreSQL's production max_connections=20.
    pool = {"pool_size": 2, "max_overflow": 1, "pool_timeout": 10} if url.startswith("postgresql") else {}
    engine = create_engine(url, connect_args=options, pool_pre_ping=True, **pool)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
