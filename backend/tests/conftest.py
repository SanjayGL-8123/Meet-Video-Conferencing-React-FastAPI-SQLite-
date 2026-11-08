import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["SECRET_KEY"] = "test-secret-key-test-secret-key-0123456789"
os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, limiters  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:  # shared event loop so WS peers can talk to each other
        for l in limiters:
            l.reset()
        yield c
