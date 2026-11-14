"""Shelf configuration in the signed rule model (docs/worker-briefs/w2-shelf-config.md).

What a slot should hold comes from the active, signed rule set. A product change signs a new rule set
version and never edits one. What the device saw stays apart from what the slot should hold.
"""

import hashlib
import importlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import rules as R

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "rules" / "school_kitchen.yaml"
PIN = "4821"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", f"{PIN}:Head chef")
    monkeypatch.setenv("CATALOG", "local")  # never the real catalog or the live API in tests
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    monkeypatch.setenv("RULES_FILE", str(BASE))
    monkeypatch.setenv("RESTOCK_MIN_S", "0")
    monkeypatch.delenv("RULE_VERSIONS_DIR", raising=False)
    return tmp_path


def start():
    """A backend process on the files of the test: same audit log, same rule set versions, fresh memory."""
    import backend.app as m
    m = importlib.reload(m)
    return m, TestClient(m.app)


def slot(view: list[dict], n: int) -> dict:
    return next(s for s in view if s["slot"] == n)


ASSIGNMENT_KEYS = {"slot", "product_id", "product", "group", "target_units", "reorder_below", "rule_set_version",
                   "assigned_in_version", "assignment_id"}


# Reading the shelf --------------------------------------------------------------

def test_the_shelf_lists_each_slot_of_the_signed_rule_set_with_its_assignment_id(env):
    _, c = start()
    view = c.get("/shelf").json()
    assert [s["slot"] for s in view] == [1, 2, 3]
    s3 = slot(view, 3)
    assert s3["product_id"] == "MOCK-MILK-LF-1L" and s3["target_units"] == 24 and s3["reorder_below"] == 6
    assert s3["rule_set_version"] == 3 and s3["assignment_id"] == "v3:MOCK-MILK-LF-1L"
    assert s3["observation"] is None and s3["pending_order"] is None
    assert set(s3) == ASSIGNMENT_KEYS | {"observation", "pending_order"}


def test_the_observation_is_kept_apart_from_the_assignment(env):
    m, c = start()
    c.post("/events", json={"type": "slot_state", "slot": 1, "state": "low", "estimated_units": 20})
    s1 = slot(c.get("/shelf").json(), 1)
    assert s1["target_units"] == 30  # what the device saw does not change what the slot should hold
    obs = s1["observation"]
    assert obs["state"] == "low" and obs["units"] == 20 and obs["fill"] is None
    assert not set(obs) & {"product_id", "target_units", "assignment_id"}
    # Two structures inside the backend, not one dict
    assert m.shelf.observations[1].units == 20 and not hasattr(m.shelf.observations[1], "product_id")
    assert not hasattr(m.shelf.assignments()[0], "state")


# Events, version 1 and version 2 -----------------------------------------------------

def _trigger(c) -> dict:
    return [e for e in c.get("/log?format=json").json() if e["step"] == "trigger"][-1]


def test_a_version_1_event_keeps_its_own_units(env):
    _, c = start()
    r = c.post("/events", json={"type": "slot_state", "slot": 2, "state": "low", "estimated_units": 2})
    assert r.status_code == 200
    assert _trigger(c)["detail"]["event"]["estimated_units"] == 2
    assert slot(c.get("/shelf").json(), 2)["observation"]["units"] == 2


def test_a_version_2_event_counts_units_from_fill_and_the_backends_target(env):
    _, c = start()
    # The device counts with its own old target (1); the backend's target for slot 2 is 12.
    ev = {"type": "slot_state", "slot": 2, "state": "low", "estimated_units": 1, "fill": 0.25, "frame_id": 7,
          "frame_time": "2026-11-14T04:39:46+02:00"}
    assert c.post("/events", json=ev).status_code == 200
    t = _trigger(c)
    assert t["detail"]["event"]["estimated_units"] == 3  # round(0.25 x 12), below the reorder level 4
    assert "about 3 units left" in t["detail"]["summary"]
    obs = slot(c.get("/shelf").json(), 2)["observation"]
    assert obs == obs | {"units": 3, "device_units": 1, "fill": 0.25, "frame_id": 7,
                         "frame_time": "2026-11-14T04:39:46+02:00", "state": "low"}


def test_a_version_2_event_above_the_reorder_level_orders_nothing(env):
    _, c = start()
    r = c.post("/events", json={"type": "slot_state", "slot": 2, "state": "low", "estimated_units": 0, "fill": 0.5})
    assert r.json()["status"] == "ok"  # round(0.5 x 12) = 6, at or above the reorder level 4
    assert not [e for e in c.get("/log?format=json").json() if e["step"] == "trigger"]


def test_version_2_fields_out_of_range_are_rejected(env):
    _, c = start()
    assert c.post("/events", json={"type": "slot_state", "slot": 2, "state": "low", "fill": 1.5}).status_code == 422
    assert c.post("/events", json={"type": "slot_state", "slot": 2, "state": "low", "fill": 0.5,
                                   "frame_id": -1}).status_code == 422


# A product change is a new signed rule set version ----------------------------------------

def _versions(env) -> Path:
    return env / "a-rule-versions"


def test_a_product_change_signs_a_new_version_and_a_new_assignment_id(env):
    base_sha = hashlib.sha256(BASE.read_bytes()).hexdigest()
    m, c = start()
    r = c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "changed" and body["rule_set_version"] == 4 and body["calibration_stale"] is True
    assert body["previous"]["assignment_id"] == "v3:MOCK-MILK-LF-1L"
    assert body["assignment"]["assignment_id"] == "v4:MOCK-MILK-LF-1L-B"

    # The new version is a file with its signature, made from the base file and its hash
    f = _versions(env) / "school_kitchen.v4.yaml"
    signed = json.loads((_versions(env) / "school_kitchen.v4.signed.json").read_text())
    assert signed["version"] == 4 and signed["signed_by"] == "Head chef"
    assert signed["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    v4 = yaml.safe_load(f.read_text())
    assert v4["based_on"] == {"version": 3, "sha256": base_sha}
    assert v4["banned_allergens"] == ["nuts", "peanuts", "sesame"] and v4["limits"]["auto_order_eur"] == 150
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == base_sha  # the signed base file is never rewritten

    # Active everywhere at once
    view = c.get("/shelf").json()
    s3 = slot(view, 3)
    assert s3["product_id"] == "MOCK-MILK-LF-1L-B" and s3["group"] == "lactose_free_milk"  # approved substitute
    assert s3["rule_set_version"] == 4 and s3["product"] == "Lactose-free milk drink 1 L, brand B"
    # Only the changed slot's calibration goes stale
    assert slot(view, 1)["assignment_id"] == "v3:MOCK-BREAD-RYE-500"
    assert slot(view, 2)["assignment_id"] == "v3:MOCK-YOG-NAT-1KG"
    assert c.get("/rules").json()["version"] == 4 and c.get("/device").json()["rule_set_version"] == 4
    assert m.agent.rules["version"] == 4 and m.driving_test.rules["version"] == 4

    entry = [e for e in c.get("/log?format=json").json() if e["step"] == "rule_set"][-1]
    assert entry["actor"] == "Head chef" and entry["rule_set_version"] == 4
    assert entry["detail"]["sha256"] == signed["sha256"]
    assert "calibrate it again" in entry["detail"]["summary"]


def test_the_agent_buys_the_new_product_after_a_change(env):
    m, c = start()
    c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"})
    m.agent.auto_limit_eur = 1  # every order waits for the chef, so the options can be read
    c.post("/events", json={"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    t = _trigger(c)
    assert t["rule_set_version"] == 4 and "brand B" in t["detail"]["summary"]
    usual = {l["product_id"] for o in c.get("/approvals/open").json()[0]["options"] for l in o["lines"] if l["usual"]}
    assert usual == {"MOCK-MILK-LF-1L-B"}


def test_a_target_change_keeps_the_assignment_id_and_counts_with_the_new_target(env):
    _, c = start()
    r = c.put("/shelf/1", json={"pin": PIN, "product_id": "MOCK-BREAD-RYE-500", "target_units": 20,
                                "reorder_below": 5}).json()
    assert r["status"] == "changed" and r["rule_set_version"] == 4 and r["calibration_stale"] is False
    assert r["assignment"]["assignment_id"] == "v3:MOCK-BREAD-RYE-500"  # same product, same photos
    c.post("/events", json={"type": "slot_state", "slot": 1, "state": "low", "estimated_units": 15, "fill": 0.5})
    assert slot(c.get("/shelf").json(), 1)["observation"]["units"] == 10  # round(0.5 x 20)


def test_the_same_values_sign_nothing(env):
    _, c = start()
    r = c.put("/shelf/2", json={"pin": PIN, "product_id": "MOCK-YOG-NAT-1KG", "target_units": 12, "reorder_below": 4})
    assert r.json()["status"] == "unchanged" and r.json()["rule_set_version"] == 3
    assert not _versions(env).exists()
    assert not [e for e in c.get("/log?format=json").json() if e["step"] == "rule_set"]


# Persistence and refusal ----------------------------------------------------------------

def test_a_change_survives_a_restart_and_the_next_change_builds_on_it(env):
    _, c = start()
    c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"})
    m, after = start()
    s3 = slot(after.get("/shelf").json(), 3)
    assert s3["assignment_id"] == "v4:MOCK-MILK-LF-1L-B" and s3["rule_set_version"] == 4
    assert m.agent.rules["version"] == 4 and m.ruleset is m.shelf.rules
    r = after.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-C"}).json()
    assert r["rule_set_version"] == 5 and r["assignment"]["assignment_id"] == "v5:MOCK-MILK-LF-1L-C"
    v5 = yaml.safe_load((_versions(env) / "school_kitchen.v5.yaml").read_text())
    assert v5["based_on"]["version"] == 4
    m, again = start()
    assert [r["version"] for r in m.shelf.chain] == [3, 4, 5]
    assert slot(again.get("/shelf").json(), 3)["assignment_id"] == "v5:MOCK-MILK-LF-1L-C"


def test_a_changed_version_file_stops_the_backend(env):
    _, c = start()
    c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"})
    f = _versions(env) / "school_kitchen.v4.yaml"
    f.write_text(f.read_text().replace("target_units: 24", "target_units: 99"))
    with pytest.raises(R.RuleSetChanged, match="changed but is still version 4"):
        start()


def test_an_unsigned_version_file_stops_the_backend(env):
    _, c = start()
    c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"})
    (_versions(env) / "school_kitchen.v4.signed.json").unlink()
    with pytest.raises(R.RuleSetChanged, match="not signed"):
        start()


def _chain_on_copy(tmp_path) -> tuple[Path, Path, dict]:
    base = tmp_path / "r.yaml"
    shutil.copy(BASE, base)
    R.sign(base)
    versions = tmp_path / "versions"
    parent = R.load_rules(base)
    targets = [dict(t) | ({"target_units": 25} if t["slot"] == 3 else {}) for t in parent["targets"]]
    return base, versions, R.write_version(parent, base, versions, targets, "Head chef", "slot 3 target 24 -> 25")


def test_a_version_signed_by_hand_but_not_made_from_the_base_is_refused(tmp_path):
    base, versions, v4 = _chain_on_copy(tmp_path)
    assert [r["version"] for r in R.load_rule_chain(base, versions)] == [3, 4]
    f = R.version_path(base, versions, 4)
    f.write_text(f.read_text().replace(v4["based_on"]["sha256"], "0" * 64))
    R.sign(f)  # the hash matches again, but the file no longer follows the base
    with pytest.raises(R.RuleSetChanged, match="was not made from v3"):
        R.load_rule_chain(base, versions)


def test_versions_made_from_an_older_base_are_refused(tmp_path):
    base, versions, _ = _chain_on_copy(tmp_path)
    base.write_text(base.read_text().replace("version: 3", "version: 4"))
    R.sign(base)  # the head chef signed a new base file in the meantime
    with pytest.raises(R.RuleSetChanged, match="older rule set"):
        R.load_rule_chain(base, versions)


def test_a_gap_in_the_versions_is_refused(tmp_path):
    base, versions, _ = _chain_on_copy(tmp_path)
    R.version_path(base, versions, 4).rename(R.version_path(base, versions, 5))
    with pytest.raises(R.RuleSetChanged, match="v4 is missing"):
        R.load_rule_chain(base, versions)


def test_a_second_writer_of_the_same_version_is_refused(tmp_path):
    base, versions, _ = _chain_on_copy(tmp_path)
    parent = R.load_rules(base)
    with pytest.raises(R.RuleSetChanged, match="exists already"):
        R.write_version(parent, base, versions, parent["targets"], "Sous chef", "a late change")


# Invalid changes --------------------------------------------------------------------------

@pytest.mark.parametrize("n, body, status, words", [
    (3, {"product_id": "NO-SUCH-PRODUCT"}, 422, "not in the catalog"),
    (3, {"product_id": "MOCK-ALMOND-1L"}, 422, "nuts"),  # banned allergen
    (3, {"product_id": "MOCK-MILK-REG-1L"}, 422, "not lactose-free"),  # must_have rule
    (3, {"product_id": "MOCK-MILK-LF-1L-X"}, 422, "allergen data is missing"),  # stand-in rules: unknown is forbidden
    (1, {"product_id": "MOCK-BREAD-SEED"}, 422, "sesame"),
    (3, {"product_id": "MOCK-YOG-NAT-1KG"}, 409, "already the product of slot 2"),  # one product, one slot
    (3, {"product_id": "MOCK-OAT-1L"}, 422, "needs a new group"),  # not a substitute of lactose-free milk
    (9, {"product_id": "MOCK-OAT-1L"}, 404, "not in rule set v3"),
    (2, {"product_id": "MOCK-YOG-NAT-1KG", "target_units": 4, "reorder_below": 4}, 422, "must be below"),
    (2, {"product_id": "MOCK-YOG-NAT-1KG", "target_units": 0}, 422, ""),
    (2, {"product_id": "MOCK-YOG-NAT-1KG", "group": "Bad Group!"}, 422, ""),
    (2, {"product_id": ["MOCK-YOG-NAT-1KG", "MOCK-OAT-1L"]}, 422, ""),  # exactly one product per slot
])
def test_an_invalid_change_is_refused_and_signs_nothing(env, n, body, status, words):
    m, c = start()
    r = c.put(f"/shelf/{n}", json={"pin": PIN} | body)
    assert r.status_code == status, r.text
    assert words in r.text
    assert not _versions(env).exists() and m.shelf.rules["version"] == 3 and m.agent.rules["version"] == 3


def test_a_new_product_with_its_own_group_is_accepted(env):
    _, c = start()
    r = c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-OAT-1L", "group": "plant_drink",
                                "product": "oat drink 1 L"}).json()
    assert r["status"] == "changed"
    assert r["assignment"] | {"group": "plant_drink", "product": "oat drink 1 L", "assignment_id": "v4:MOCK-OAT-1L"} \
        == r["assignment"]


def test_a_change_needs_a_right_pin(env, monkeypatch):
    m, c = start()
    assert c.put("/shelf/3", json={"pin": "0000", "product_id": "MOCK-MILK-LF-1L-B"}).status_code == 403
    assert c.put("/shelf/3", json={"product_id": "MOCK-MILK-LF-1L-B"}).status_code == 403
    for _ in range(3):
        c.put("/shelf/3", json={"pin": "0000", "product_id": "MOCK-MILK-LF-1L-B"})
    # Wrong PINs here count toward the same lock as wrong PINs on approvals
    assert c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"}).status_code == 429
    assert not _versions(env).exists() and m.shelf.rules["version"] == 3
    monkeypatch.setenv("APPROVER_PINS", "")
    _, c = start()
    assert c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"}).status_code == 503


# A pending order keeps its product and rule set version ------------------------------------------

def test_a_pending_order_survives_a_product_change(env):
    m, c = start()
    m.agent.auto_limit_eur = 1  # the order waits for the chef
    c.post("/events", json={"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    [approval] = c.get("/approvals/open").json()
    lines_before = [[l["product_id"] for l in o["lines"]] for o in approval["options"]]

    r = c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"}).json()
    assert r["status"] == "changed"
    assert r["pending_order"] == r["pending_order"] | {"slot": 3, "product_id": "MOCK-MILK-LF-1L", "rule_set_version": 3,
                                                       "run_id": approval["run_id"], "status": "approval"}
    p = slot(c.get("/shelf").json(), 3)["pending_order"]
    assert p["product_id"] == "MOCK-MILK-LF-1L" and p["rule_set_version"] == 3
    assert p["made_for_current_assignment"] is False
    entry = [e for e in c.get("/log?format=json").json() if e["step"] == "rule_set"][-1]
    assert entry["detail"]["pending_order"]["run_id"] == approval["run_id"] and "stays as it was" in entry["detail"]["summary"]

    # The request itself did not change, and approving it orders what it offered
    [still] = c.get("/approvals/open").json()
    assert still["id"] == approval["id"]
    assert [[l["product_id"] for l in o["lines"]] for o in still["options"]] == lines_before
    done = c.post(f"/approvals/{approval['id']}", json={"action": "approve", "pin": PIN}).json()
    assert done["status"] == "ordered"
    order = c.get("/orders").json()[-1]
    assert [l["product_id"] for l in order["lines"]] == lines_before[0] and order["slot"] == 3

    # After a restart the placed order is still tied to the old product and version
    _, after = start()
    p = slot(after.get("/shelf").json(), 3)["pending_order"]
    assert p == p | {"status": "ordered", "product_id": "MOCK-MILK-LF-1L", "rule_set_version": 3,
                     "run_id": approval["run_id"], "made_for_current_assignment": False}
    assert slot(after.get("/shelf").json(), 3)["product_id"] == "MOCK-MILK-LF-1L-B"


def test_the_pending_record_of_the_agent_is_used_when_it_exists(env):
    """Interface 8: once worktree 1's Agent.pending_for_slot exists, the shelf reads it instead of its fallback."""
    m, c = start()
    record = {"slot": 2, "product_id": "MOCK-YOG-NAT-1KG", "rule_set_version": 3, "run_id": "r1",
              "since": "2026-11-14T06:00:00+02:00", "status": "ordered"}
    m.agent.pending_for_slot = lambda s: record if s == 2 else None
    p = slot(c.get("/shelf").json(), 2)["pending_order"]
    assert p == record | {"made_for_current_assignment": True}
    assert slot(c.get("/shelf").json(), 1)["pending_order"] is None


def test_versions_live_next_to_the_audit_log_unless_set(env, monkeypatch):
    m, _ = start()
    assert m.shelf.versions_dir == env / "a-rule-versions"
    monkeypatch.setenv("RULE_VERSIONS_DIR", str(env / "elsewhere"))
    m, c = start()
    c.put("/shelf/3", json={"pin": PIN, "product_id": "MOCK-MILK-LF-1L-B"})
    assert (env / "elsewhere" / "school_kitchen.v4.yaml").exists()
