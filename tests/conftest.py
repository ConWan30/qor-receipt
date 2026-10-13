import pytest
from fastapi.testclient import TestClient

from qor_receipt.demo import FakeClock
from qor_receipt.keys import SoftwareKey
from qor_receipt.referee.app import create_app


@pytest.fixture
def ref():
    clock, key = FakeClock(), SoftwareKey()
    return TestClient(create_app(key, clock)), clock, key
