"""Focused tests for the actual `/health/` contract.

`core.views.health()` is a small, undecorated function view: it always
calls `database_probe()` (a real `select 1` against PostgreSQL) and
returns a JSON body. It has no method restriction of its own -- Django's
CSRF middleware, not the view, is what rejects an unsafe-method request
lacking a valid token. These tests confirm the endpoint's actual,
current guarantees rather than assuming a readiness/liveness contract it
does not implement.
"""

import pytest
from django.db import OperationalError
from django.test import Client


@pytest.mark.django_db
def test_health_get_succeeds_when_database_is_available(client):
    response = client.get("/health/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"ok": True, "database": 1}


@pytest.mark.django_db
def test_health_response_contains_no_private_data(client):
    response = client.get("/health/")

    assert set(response.json().keys()) == {"ok", "database"}


@pytest.mark.django_db
def test_health_is_reachable_without_authentication(client):
    # The default test client is already unauthenticated; this asserts
    # that fact is load-bearing, not incidental.
    response = client.get("/health/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_health_database_failure_is_not_caught_and_propagates_as_500(monkeypatch):
    import core.views as core_views

    def raising_probe():
        raise OperationalError("simulated connection failure")

    monkeypatch.setattr(core_views, "database_probe", raising_probe)

    # The view has no try/except of its own, so a database failure is not
    # translated into a graceful {"ok": false} body -- it propagates as an
    # ordinary unhandled exception. `raise_request_exception=False` lets
    # the test client observe Django's own resulting 500 response instead
    # of re-raising the exception into the test, exactly as a real
    # deployed server would behave. Documented here as the real contract
    # rather than a readiness/liveness guarantee.
    non_raising_client = Client(raise_request_exception=False)
    response = non_raising_client.get("/health/")

    assert response.status_code == 500


@pytest.mark.django_db
def test_health_accepts_get_even_with_csrf_enforced():
    csrf_client = Client(enforce_csrf_checks=True)
    response = csrf_client.get("/health/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_health_post_without_csrf_token_is_rejected_by_middleware():
    # The view itself imposes no method restriction; Django's
    # CsrfViewMiddleware is what blocks an unsafe-method request lacking a
    # valid token, exactly as it would for any other view in this project.
    csrf_client = Client(enforce_csrf_checks=True)
    response = csrf_client.post("/health/")
    assert response.status_code == 403
