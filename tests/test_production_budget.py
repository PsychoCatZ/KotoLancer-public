from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from pydantic import SecretStr

from kotolancer.ai_budget import ProductionBudget, summary
from kotolancer.ai_provider import OpenAIProvider, ProviderError
from kotolancer.consultant import claim_next, process_next
from kotolancer.db import session_factory
from kotolancer.models import AIJob, AIRun, now
from tests.test_consultant import consultant as consultant
from tests.test_consultant import submit
from tests.test_foundation import create_order


def job(consultant):
    c, _, factory, s = consultant
    s.openai_model = "gpt-6-luna"
    s.openai_reasoning_effort = "low"
    s.openai_total_budget_nusd = 1_000_000_000
    s.openai_api_key = SecretStr("not-real")
    order = create_order(c)
    queued = submit(c, order)
    assert queued.status_code == 202
    with factory() as db:
        ident, lease = claim_next(db, s)
    return c, factory, s, ident, lease


def sdk_count(count=100, callback=None):
    calls = []

    def counter(**kwargs):
        calls.append(kwargs)
        if callback:
            callback()
        return SimpleNamespace(input_tokens=count)

    return SimpleNamespace(responses=SimpleNamespace(input_tokens=SimpleNamespace(count=counter))), calls


def arguments():
    return {
        "model": "gpt-6-luna",
        "service_tier": "default",
        "reasoning": {"effort": "low"},
        "max_output_tokens": 4000,
        "store": False,
        "input": "synthetic only",
    }


def test_reservation_durable_before_generation_and_survives_restart(consultant):
    _, factory, s, ident, lease = job(consultant)
    sdk, calls = sdk_count()
    amount = ProductionBudget(factory, s, ident, lease).reserve(sdk, arguments())
    assert amount == 3283500 and len(calls) == 1
    assert not {"store", "max_output_tokens", "service_tier"} & set(calls[0])
    restarted = session_factory(factory.kw["bind"])
    with restarted() as db:
        assert summary(db, s)["reserved_nusd"] == amount
        assert db.get(AIRun, ident).counted_input_tokens == 100
    with pytest.raises(ProviderError, match="уже резервировался"):
        ProductionBudget(restarted, s, ident, lease).reserve(sdk, arguments())
    with restarted() as db:
        assert summary(db, s)["reserved_nusd"] == amount


@pytest.mark.parametrize("case", ["unapproved", "model", "effort", "tools", "tier", "output"])
def test_unapproved_policy_does_not_even_count_or_generate(consultant, case):
    _, factory, s, ident, lease = job(consultant)
    a = arguments()
    if case == "unapproved":
        s.openai_total_budget_nusd = 0
    elif case == "model":
        a["model"] = "other"
    elif case == "effort":
        a["reasoning"] = {"effort": "high"}
    elif case == "tools":
        a["tools"] = [{"type": "web_search"}]
    elif case == "tier":
        a["service_tier"] = "fast"
    else:
        a["max_output_tokens"] = 4001
    sdk, calls = sdk_count()
    with pytest.raises(ProviderError):
        ProductionBudget(factory, s, ident, lease).reserve(sdk, a)
    assert calls == []


@pytest.mark.parametrize("count", [0, -1, 200001, True, "100"])
def test_invalid_or_excessive_count_fails_closed(consultant, count):
    _, factory, s, ident, lease = job(consultant)
    sdk, _ = sdk_count(count)
    with pytest.raises(ProviderError):
        ProductionBudget(factory, s, ident, lease).reserve(sdk, arguments())
    with factory() as db:
        assert summary(db, s)["reserved_nusd"] == 0


def test_insufficient_allowance_does_not_reserve(consultant):
    _, factory, s, ident, lease = job(consultant)
    s.openai_total_budget_nusd = 3283499
    sdk, _ = sdk_count()
    with pytest.raises(ProviderError) as error:
        ProductionBudget(factory, s, ident, lease).reserve(sdk, arguments())
    assert error.value.code == "budget_exhausted"
    with factory() as db:
        assert summary(db, s)["reserved_nusd"] == 0


@pytest.mark.parametrize("case", ["accepted", "lease_lost"])
def test_state_changes_during_count_prevent_reservation(consultant, case):
    _, factory, s, ident, lease = job(consultant)

    def change():
        from kotolancer.models import Order

        with factory() as db:
            row = db.get(AIJob, ident)
            if case == "accepted":
                db.get(Order, row.order_id).accepted_at = now()
            else:
                row.lease_until = now() - timedelta(seconds=1)
            db.commit()

    sdk, _ = sdk_count(callback=change)
    with pytest.raises(ProviderError):
        ProductionBudget(factory, s, ident, lease).reserve(sdk, arguments())
    with factory() as db:
        assert summary(db, s)["reserved_nusd"] == 0


def test_worker_reserves_before_unknown_network_outcome_and_never_retries(consultant, monkeypatch):
    c, _, factory, s = consultant
    s.openai_model = "gpt-6-luna"
    s.openai_reasoning_effort = "low"
    s.openai_api_key = SecretStr("not-real")
    s.openai_total_budget_nusd = 1_000_000_000
    order = create_order(c)
    queued = submit(c, order).json()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        with factory() as db:
            assert db.get(AIRun, queued["id"]).reserved_nusd > 0
        raise httpx.ConnectError("synthetic lost connection", request=request)

    def make_sdk(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["base_url"] == "https://api.openai.com/v1"
        return OpenAI(
            api_key="not-real",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr("kotolancer.ai_provider.OpenAI", make_sdk)
    assert process_next(factory, s, OpenAIProvider(s))
    assert not process_next(factory, s, OpenAIProvider(s))
    assert calls == ["/v1/responses/input_tokens", "/v1/responses"]
    with factory() as db:
        assert db.get(AIJob, queued["id"]).error_code == "unknown_outcome"
        assert summary(db, s)["reserved_nusd"] == 3283500


def test_concurrent_reservations_cannot_exceed_one_request_allowance(consultant):
    _, factory, s, first, lease1 = job(consultant)
    c = consultant[0]
    order = create_order(c)
    assert submit(c, order).status_code == 202
    with factory() as db:
        second, lease2 = claim_next(db, s)
    s.openai_total_budget_nusd = 3283500

    def reserve(pair):
        sdk, _ = sdk_count()
        try:
            return ProductionBudget(factory, s, *pair).reserve(sdk, arguments())
        except ProviderError:
            return 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [(first, lease1), (second, lease2)]))
    assert sum(results) == 3283500
    with factory() as db:
        assert summary(db, s)["reserved_nusd"] == 3283500


def test_count_network_failure_does_not_create_reservation(consultant):
    from openai import APIConnectionError

    _, factory, s, ident, lease = job(consultant)

    def fail():
        raise APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses/input_tokens")
        )

    sdk, _ = sdk_count(callback=fail)
    with pytest.raises(ProviderError) as error:
        ProductionBudget(factory, s, ident, lease).reserve(sdk, arguments())
    assert error.value.code == "token_count_failed"
    with factory() as db:
        assert summary(db, s)["reserved_nusd"] == 0
