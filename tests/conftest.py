import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from kotolancer.app import create_app
from kotolancer.config import Settings
from kotolancer.db import make_engine, session_factory
from kotolancer.manage import migrate
from kotolancer.profile import initialize_profile


@pytest.fixture
def settings(tmp_path):
    postgres = os.environ.get("KOTO_TEST_DATABASE_URL")
    schema = f"test_{uuid4().hex}"
    if postgres:
        import psycopg
        from psycopg import sql

        dsn = postgres.replace("postgresql+psycopg://", "postgresql://")
        admin = psycopg.connect(dsn, autocommit=True)
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        url = (
            make_url(postgres)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    else:
        url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    config = Settings(
        _env_file=None,
        environment="local",
        auth_mode="local",
        database_url=url,
        storage_dir=tmp_path / "files",
        allowed_hosts=["testserver"],
    )
    migrate(config.database_url)
    engine = make_engine(config.database_url)
    with session_factory(engine)() as db:
        initialize_profile(db)
    engine.dispose()
    yield config
    if postgres:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers={"X-Koto-Client": "workspace"}) as result:
        yield result
