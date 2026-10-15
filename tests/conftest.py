from __future__ import annotations

import dataclasses
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from leadpilot.api import create_app
from leadpilot.app import App, build
from leadpilot.db import Database
from leadpilot.settings import Settings, load_business

BUSINESS = load_business("config/business.yaml")

HOT_LEAD = {
    "name": "Dr. Priya Lee",
    "email": "priya@brightsmile-dental.example",
    "company": "BrightSmile Dental",
    "message": "I'm the owner of a dental practice with 40 staff. Our front desk spends 15 hours a week "
               "copying appointment requests into our system by hand. Budget is $8,000 and we want it "
               "automated within the next 3 weeks.",
}
WARM_LEAD = {
    "name": "Chris Nolan",
    "email": "chris@nolanplumbing.example",
    "company": "Nolan Plumbing",
    "message": "We're a plumbing company with about 20 employees. We'd like to automate how we follow up "
               "on quotes. Can you tell me more about how you work?",
}


def make_settings(**overrides: Any) -> Settings:
    base = Settings(qualifier="rules", db_path=":memory:", business=BUSINESS, retry_base_seconds=0.0,
                    run_worker=False)
    return dataclasses.replace(base, **overrides)


@pytest.fixture
def make_ctx(tmp_path) -> Callable[..., App]:
    def _make(crm: Any = None, qualifier: Any = None, **overrides: Any) -> App:
        s = make_settings(db_path=str(tmp_path / "test.db"), **overrides)
        return build(s, db=Database(s.db_path), crm=crm, qualifier=qualifier)
    return _make


@pytest.fixture
def ctx(make_ctx) -> App:
    return make_ctx()


@pytest.fixture
def client(ctx) -> TestClient:
    with TestClient(create_app(ctx, start_worker=False)) as c:
        yield c
