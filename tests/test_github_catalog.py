import copy
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from kotolancer.app import create_app
from kotolancer.github_catalog import CatalogBusy, RepositoryCatalog, fetch_catalog
from kotolancer.github_reader import GitHubError, GitHubReader


def repository(name, owner="example-owner", **extra):
    return {"name": name, "owner": {"login": owner}, "private": True, **extra}


def reader_for(settings, response):
    settings.github_owner = "example-owner"
    settings.github_token = SecretStr("not-a-real-token")
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/user/repos"
        assert request.url.params["affiliation"] == "owner"
        assert request.url.params["per_page"] == "100"
        assert "type" not in request.url.params
        return response(request)

    return GitHubReader(settings, httpx.Client(transport=httpx.MockTransport(handle))), calls


def test_paging_owner_filter_safe_projection_and_deduplication(settings):
    first = [repository(f"repo-{i:03}") for i in range(98)] + [
        repository("foreign", "other"),
        repository("same"),
    ]
    second = [
        repository(
            "SAME",
            owner="EXAMPLE-OWNER",
            description="x" * 500,
            language="Python",
            fork=True,
            archived=True,
            temp_clone_token="do-not-return",
        )
    ]
    reader, calls = reader_for(
        settings, lambda r: httpx.Response(200, json=first if r.url.params["page"] == "1" else second)
    )
    result = fetch_catalog(reader)
    assert len(calls) == 2 and len(result["repositories"]) == 99
    assert not result["possibly_incomplete"]
    row = next(x for x in result["repositories"] if x["name"] == "SAME")
    assert len(row["description"]) == 400 and row["fork"] and row["archived"]
    assert "temp_clone_token" not in str(result) and "do-not-return" not in str(result)


def test_catalog_bound_marks_possible_incompleteness(settings):
    reader, calls = reader_for(
        settings,
        lambda r: httpx.Response(
            200, json=[repository(f"repo-{r.url.params['page']}-{i}") for i in range(100)]
        ),
    )
    result = fetch_catalog(reader)
    assert len(calls) == 5 and len(result["repositories"]) == 500 and result["possibly_incomplete"]


@pytest.mark.parametrize(
    "payload", [{"message": "unexpected"}, [repository("../evil")], ["bad"], [repository(".")]]
)
def test_invalid_responses_do_not_become_catalog(settings, payload):
    reader, _ = reader_for(settings, lambda r: httpx.Response(200, json=payload))
    with pytest.raises(GitHubError):
        fetch_catalog(reader)


def test_cache_old_result_survives_failure_and_reads_are_local(settings):
    state = {"fail": False}
    reader, calls = reader_for(settings, lambda r: httpx.Response(403 if state["fail"] else 200, json=[]))
    catalog = RepositoryCatalog(settings, reader)
    assert catalog.view() == {"catalog": None, "refreshing": False} and calls == []
    saved = catalog.refresh()
    saved["catalog"]["owner"] = "tampered"
    assert catalog.view()["catalog"]["owner"] == "example-owner" and len(calls) == 1
    with pytest.raises(CatalogBusy):
        catalog.refresh()
    catalog.last_attempt = float("-inf")
    state["fail"] = True
    old = copy.deepcopy(catalog.view())
    with pytest.raises(GitHubError):
        catalog.refresh()
    assert catalog.view() == old


def test_parallel_refresh_does_not_block_local_view_or_duplicate_fetch(settings):
    entered, release = threading.Event(), threading.Event()

    def response(request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json=[])

    reader, calls = reader_for(settings, response)
    catalog = RepositoryCatalog(settings, reader)
    thread = threading.Thread(target=catalog.refresh)
    thread.start()
    try:
        assert entered.wait(5) and catalog.view()["refreshing"]
        with pytest.raises(CatalogBusy):
            catalog.refresh()
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not catalog.view()["refreshing"]


def test_endpoint_refresh_never_reads_sources_or_changes_profile(settings):
    reader, calls = reader_for(settings, lambda r: httpx.Response(200, json=[repository("KotoLancer")]))
    with TestClient(
        create_app(settings, github_reader=reader), headers={"X-Koto-Client": "workspace"}
    ) as client:
        profile = client.get("/api/profile").json()
        assert client.get("/api/github/repositories").json()["catalog"] is None and not calls
        result = client.post("/api/github/repositories/refresh")
        assert (
            result.status_code == 200 and result.json()["catalog"]["repositories"][0]["name"] == "KotoLancer"
        )
        assert client.get("/api/profile").json() == profile
        assert client.get("/api/github/reads").json() == []
        assert client.get("/api/github/proposals").json() == []
        assert len(calls) == 1
        assert client.post("/api/github/repositories/refresh").status_code == 409


def test_no_token_and_private_endpoints_stay_protected(settings):
    settings.github_token = SecretStr("")
    with TestClient(create_app(settings), headers={"X-Koto-Client": "workspace"}) as client:
        assert client.post("/api/github/repositories/refresh").status_code == 409
    settings.auth_mode = "token"
    settings.access_token = "a-real-length-test-only-access-token"
    with TestClient(create_app(settings), headers={"X-Koto-Client": "workspace"}) as client:
        assert client.get("/api/github/repositories").status_code == 401
        assert client.post("/api/github/repositories/refresh").status_code == 401
