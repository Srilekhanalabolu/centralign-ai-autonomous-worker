import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def settings(tmp_path):
    s = Settings()
    s.llm_api_key = ""  # tests always use the deterministic policy
    s.finance_db_path = tmp_path / "finance.db"
    s.step_delay = 0
    s.approval_timeout = 5
    return s


@pytest.fixture()
def app(settings):
    application = create_app(settings)
    client = TestClient(application)
    application.state.finance_http = client  # agent calls the finance API in-process
    return application


@pytest.fixture()
def client(app):
    return app.state.finance_http
