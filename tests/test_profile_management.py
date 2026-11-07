import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.test_github_reads import github_context as github_context
from tests.test_github_reads import implementation


def accepted(ctx, source):
    c = ctx[4]
    before = c.get("/api/profile").json()
    p = c.post(
        "/api/github/proposals",
        json={
            "snapshot_id": source["id"],
            "expected_revision": before["revision"],
            "capability_ids": ["cap-014"],
            "statement": "Обработчик передаёт фотографию в сохранение. Выполнение не проверялось. "
            + str(uuid4()),
            "citations": [{"path": "src/bot.py", "line_start": 1, "line_end": 2}],
        },
    )
    assert p.status_code == 201
    ident = p.json()["id"]
    assert c.post(f"/api/github/proposals/{ident}/accept").status_code == 200
    return ident, "github-" + ident


def revise(c, profile, reason="Явная ручная корректировка владельца"):
    current = c.get("/api/profile").json()
    r = c.put(
        "/api/profile", json={"expected_revision": current["revision"], "profile": profile, "reason": reason}
    )
    assert r.status_code == 200, r.text
    return r.json()


def revoke(c, eid, revision, reason="Уточнено основание: прежнее утверждение больше не используем"):
    return c.post(
        f"/api/profile/evidence/{eid}/revoke", json={"expected_revision": revision, "reason": reason}
    )


def test_revoke_updates_all_links_and_preserves_history_without_resurrection(github_context):
    ctx = github_context
    source = implementation(ctx)
    c = ctx[4]
    pid, eid = accepted(ctx, source)
    p = c.get("/api/profile").json()["profile"]
    p["projects"][0]["evidence_ids"].append(eid)
    before = revise(c, p)
    assert revoke(c, eid, before["revision"]).status_code == 200
    after = c.get("/api/profile").json()
    assert after["revision"] == before["revision"] + 1
    expected = copy.deepcopy(before["profile"])
    expected["evidence"] = [e for e in expected["evidence"] if e["id"] != eid]
    for item in [*expected["projects"], *expected["capabilities"]]:
        item["evidence_ids"] = [i for i in item["evidence_ids"] if i != eid]
    assert after["profile"] == expected
    old = c.get(f"/api/profile/revisions/{before['revision']}").json()
    assert old["profile"] == before["profile"]
    assert next(e for e in old["profile"]["evidence"] if e["id"] == eid)["citations"]
    assert "прежнее утверждение" in after["reason"]
    assert c.get("/api/github/proposals").json()[0]["status"] == "revoked"
    assert c.post(f"/api/github/proposals/{pid}/accept").status_code == 409
    assert revoke(c, eid, before["revision"]).json() == {"status": "revoked"}
    assert c.get("/api/profile").json()["revision"] == after["revision"]


def test_last_a_basis_requires_explicit_owner_correction(github_context):
    ctx = github_context
    c = ctx[4]
    pid, eid = accepted(ctx, implementation(ctx))
    p = c.get("/api/profile").json()["profile"]
    cap = next(x for x in p["capabilities"] if x["id"] == "cap-014")
    cap["evidence_ids"] = [eid]
    before = revise(c, p)
    assert revoke(c, eid, before["revision"]).status_code == 409
    assert c.get("/api/profile").json()["profile"] == before["profile"]
    assert c.get("/api/github/proposals").json()[0]["status"] == "accepted"
    cap["category"] = "B"
    manual = revise(c, p, "Владелец явно уточнил границу опыта до смежной")
    assert revoke(c, eid, manual["revision"]).status_code == 200
    assert (
        next(x for x in c.get("/api/profile").json()["profile"]["capabilities"] if x["id"] == "cap-014")[
            "category"
        ]
        == "B"
    )


@pytest.mark.parametrize("case", ["stale", "blank", "owner_basis", "missing", "unauthorized"])
def test_revoke_rejections_leave_profile_unchanged(github_context, case):
    ctx = github_context
    c = ctx[4]
    _, eid = accepted(ctx, implementation(ctx))
    before = c.get("/api/profile").json()
    revision, reason, expected = before["revision"], "Проверка отказа без изменений", 409
    if case == "stale":
        revision -= 1
    elif case == "blank":
        reason, expected = "   ", 422
    elif case == "owner_basis":
        eid = next(e["id"] for e in before["profile"]["evidence"] if e["source"] == "owner")
    elif case == "missing":
        eid, expected = "missing", 404
    else:
        expected = 403
        c.headers.pop("X-Koto-Client")
    assert revoke(c, eid, revision, reason).status_code == expected
    c.headers["X-Koto-Client"] = "workspace"
    assert c.get("/api/profile").json()["profile"] == before["profile"]
    assert c.get("/api/profile").json()["revision"] == before["revision"]


def test_parallel_revokes_cannot_overwrite_each_other(github_context):
    ctx = github_context
    c = ctx[4]
    source = implementation(ctx)
    _, one = accepted(ctx, source)
    _, two = accepted(ctx, source)
    before = c.get("/api/profile").json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda eid: revoke(c, eid, before["revision"]), [one, two]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    after = c.get("/api/profile").json()
    assert after["revision"] == before["revision"] + 1
    assert sum(e["id"] in [one, two] for e in after["profile"]["evidence"]) == 1


def test_new_entries_keep_ids_owner_basis_and_conflict_protection(client):
    before = client.get("/api/profile").json()
    p = copy.deepcopy(before["profile"])
    p["evidence"].append(
        {
            "id": "owner-manual",
            "source": "owner",
            "statement": "Владелец описал реализованный компонент и его границы",
            "reference": "Ручная запись для проверки",
        }
    )
    p["projects"].append(
        {
            "id": "new-project",
            "name": "Новый проект",
            "scope": "Реализованный компонент; проверка владельцем",
            "evidence_ids": ["owner-manual"],
        }
    )
    p["capabilities"].append(
        {
            "id": "new-capability",
            "label": "Новая область",
            "scope": "Смежный опыт в указанном компоненте",
            "category": "B",
            "evidence_ids": ["owner-manual"],
        }
    )
    result = revise(client, p)
    assert result["profile"]["capabilities"][:-1] == before["profile"]["capabilities"]
    p["projects"][-1]["name"] = "Уточнённое имя"
    edited = revise(client, p)
    assert edited["profile"]["projects"][-1]["id"] == "new-project"
    assert client.get(f"/api/profile/revisions/{result['revision']}").json()["profile"] == result["profile"]
    assert (
        client.put(
            "/api/profile",
            json={
                "expected_revision": result["revision"],
                "reason": "Устаревшая открытая форма",
                "profile": result["profile"],
            },
        ).status_code
        == 409
    )
    assert client.get("/api/profile").json()["profile"] == edited["profile"]
