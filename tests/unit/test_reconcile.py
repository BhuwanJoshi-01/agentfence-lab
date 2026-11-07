# SPDX-License-Identifier: Apache-2.0
"""Stale container and volume reconciliation by run label (AF-0207).

Docker records mirror ``docker ps -a --format '{{json .}}'`` and ``docker volume ls``
on the maintainer's Docker 29.9.2 (``ID``, ``Name``, ``Labels`` as a ``k=v,...``
string). Podman records follow its documented ``--format json`` (an array; ``Id`` and a
``Labels`` object); Podman is not installed there, so the parser accepts both shapes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from agentfence.core.sandbox_policy import IsolationPolicy
from agentfence.runtime.containers import build_volume_create_argv
from agentfence.runtime.engine_probe import QueryResult
from agentfence.runtime.reconcile import (
    LabelledResource,
    ReconcileError,
    list_labelled,
    plan_stale,
    remove_stale,
)

OLD = "11111111-2222-4333-8444-555555555555"
ACTIVE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
CID_OLD = "a" * 64
CID_ACTIVE = "b" * 64
VOL_OLD = "agentfence-11111111-att-0001"
VOL_ACTIVE = "agentfence-aaaaaaaa-att-0001"


def docker_lines(*records: dict[str, object]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in records).encode()


def docker_label(run: str) -> str:
    return f"io.agentfence.attempt=att-0001,io.agentfence.run={run},other=x"


class FakeEngine:
    def __init__(
        self, outputs: dict[str, QueryResult], fail_remove: frozenset[str] = frozenset()
    ) -> None:
        self.outputs = outputs
        self.fail_remove = fail_remove
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> QueryResult:
        self.calls.append(list(argv))
        if "rm" in argv:
            return QueryResult(1 if argv[-1] in self.fail_remove else 0, b"")
        key = "volume" if argv[1] == "volume" else "container"
        return self.outputs[key]


def docker_engine(fail_remove: frozenset[str] = frozenset()) -> FakeEngine:
    return FakeEngine(
        {
            "container": QueryResult(
                0,
                docker_lines(
                    {"ID": CID_OLD, "Names": "x", "Labels": docker_label(OLD), "State": "exited"},
                    {"ID": CID_ACTIVE, "Labels": docker_label(ACTIVE), "State": "running"},
                ),
            ),
            "volume": QueryResult(
                0,
                docker_lines(
                    {"Name": VOL_OLD, "Labels": docker_label(OLD)},
                    {"Name": VOL_ACTIVE, "Labels": docker_label(ACTIVE)},
                ),
            ),
        },
        fail_remove,
    )


def test_docker_records_are_listed_by_run() -> None:
    assert list_labelled("docker", "/usr/bin/docker", docker_engine()) == (
        LabelledResource("container", CID_OLD, OLD),
        LabelledResource("container", CID_ACTIVE, ACTIVE),
        LabelledResource("volume", VOL_OLD, OLD),
        LabelledResource("volume", VOL_ACTIVE, ACTIVE),
    )


def test_docker_list_commands_filter_by_label_without_a_shell() -> None:
    engine = docker_engine()
    list_labelled("docker", "/usr/bin/docker", engine)
    assert engine.calls == [
        [
            "/usr/bin/docker",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            "label=io.agentfence.run",
            "--format",
            "{{json .}}",
        ],
        [
            "/usr/bin/docker",
            "volume",
            "ls",
            "--filter",
            "label=io.agentfence.run",
            "--format",
            "{{json .}}",
        ],
    ]


def test_podman_json_arrays_are_parsed() -> None:
    labels = {"io.agentfence.run": OLD, "io.agentfence.attempt": "att-0001"}
    engine = FakeEngine(
        {
            "container": QueryResult(
                0, json.dumps([{"Id": CID_OLD, "Names": ["x"], "Labels": labels}]).encode()
            ),
            "volume": QueryResult(0, json.dumps([{"Name": VOL_OLD, "Labels": labels}]).encode()),
        }
    )
    assert set(list_labelled("podman", "/usr/bin/podman", engine)) == {
        LabelledResource("container", CID_OLD, OLD),
        LabelledResource("volume", VOL_OLD, OLD),
    }
    assert engine.calls[0][1:] == [
        "ps",
        "--all",
        "--filter",
        "label=io.agentfence.run",
        "--format",
        "json",
    ]


def test_empty_output_means_nothing_to_do() -> None:
    engine = FakeEngine({"container": QueryResult(0, b""), "volume": QueryResult(0, b"[]")})
    assert list_labelled("podman", "/usr/bin/podman", engine) == ()


@pytest.mark.parametrize(
    "record",
    [
        {"ID": CID_OLD, "Labels": "other=1"},  # no run label: the engine filter was ignored
        {"ID": CID_OLD, "Labels": "io.agentfence.run="},  # empty run label
        {"ID": CID_OLD, "Labels": "io.agentfence.run=../../x y"},  # malformed run label
        {"ID": "not-hex; rm -rf /", "Labels": docker_label(OLD)},
        {"ID": "abc", "Labels": docker_label(OLD)},  # too short to be an ID
        {"Labels": docker_label(OLD)},  # no ID at all
        {"ID": ["a" * 64], "Labels": docker_label(OLD)},
    ],
)
def test_records_that_are_not_clearly_ours_are_ignored(record: dict[str, object]) -> None:
    engine = FakeEngine(
        {"container": QueryResult(0, docker_lines(record)), "volume": QueryResult(0, b"")}
    )
    assert list_labelled("docker", "/usr/bin/docker", engine) == ()


@pytest.mark.parametrize("name", ["user-data", "agentfence", "agentfence-UPPER", "/home/x"])
def test_volumes_without_our_prefix_are_ignored(name: str) -> None:
    engine = FakeEngine(
        {
            "container": QueryResult(0, b""),
            "volume": QueryResult(0, docker_lines({"Name": name, "Labels": docker_label(OLD)})),
        }
    )
    assert list_labelled("docker", "/usr/bin/docker", engine) == ()


@pytest.mark.parametrize(
    "result",
    [QueryResult(1, b"Cannot connect"), QueryResult(0, b"{not json"), QueryResult(0, b"\xff")],
)
def test_listing_failures_raise(result: QueryResult) -> None:
    engine = FakeEngine({"container": result, "volume": QueryResult(0, b"")})
    with pytest.raises(ReconcileError):
        list_labelled("docker", "/usr/bin/docker", engine)


def test_the_active_run_is_never_stale_and_containers_go_first() -> None:
    found = list_labelled("docker", "/usr/bin/docker", docker_engine())
    assert plan_stale(found, active_run=ACTIVE) == (
        LabelledResource("container", CID_OLD, OLD),
        LabelledResource("volume", VOL_OLD, OLD),
    )
    assert len(plan_stale(found)) == 4


def test_removal_order_commands_and_failures() -> None:
    engine = docker_engine(fail_remove=frozenset({VOL_OLD}))
    stale = plan_stale(list_labelled("docker", "/usr/bin/docker", engine), active_run=ACTIVE)
    engine.calls.clear()
    result = remove_stale("/usr/bin/docker", stale, engine, engine="docker")
    assert engine.calls == [
        ["/usr/bin/docker", "rm", "--force", CID_OLD],
        ["/usr/bin/docker", "volume", "rm", "--force", VOL_OLD],
    ]
    assert result.removed == (LabelledResource("container", CID_OLD, OLD),)
    assert result.failed == (LabelledResource("volume", VOL_OLD, OLD),)


def test_a_forged_resource_is_never_passed_to_the_engine() -> None:
    engine = docker_engine()
    forged = [
        LabelledResource("container", "--privileged", OLD),
        LabelledResource("volume", "/home/user", OLD),
    ]
    result = remove_stale("/usr/bin/docker", forged, engine, engine="docker")
    assert engine.calls == []
    assert result.failed == tuple(sorted(forged, key=lambda r: (r.kind != "container", r.ref)))


def test_workspace_volumes_are_created_with_the_run_labels() -> None:
    policy = IsolationPolicy(
        run_id=OLD,
        attempt_id="att-0001",
        image="localhost/agentfence-mock@sha256:" + "c" * 64,
        workspace_volume=VOL_OLD,
    )
    argv = build_volume_create_argv("podman", policy)
    assert argv == (
        "volume",
        "create",
        "--label=io.agentfence.attempt=att-0001",
        f"--label=io.agentfence.run={OLD}",
        VOL_OLD,
    )
    assert build_volume_create_argv("docker", policy) == argv


# --- creation times and the grace period ------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"Created": 1794059321}, 1794059321.0),
        ({"CreatedAt": "2026-11-07T19:33:41.349787129+05:45"}, 1794059321.349787),
        ({"CreatedAt": "2026-11-07 13:48:41 +0000 UTC"}, 1794059321.0),
        ({"CreatedAt": "Less than a second ago"}, None),
        ({"Created": True}, None),
        ({}, None),
    ],
)
def test_creation_times_from_both_engines(
    record: dict[str, object], expected: float | None
) -> None:
    from agentfence.runtime.reconcile import _created  # noqa: PLC0415

    got = _created(record)
    assert got == pytest.approx(expected) if expected is not None else got is None


def test_a_recent_resource_keeps_its_whole_run() -> None:
    now = 1_000_000.0
    resources = [
        LabelledResource("container", CID_ACTIVE, ACTIVE, created=now - 60),  # in progress
        LabelledResource("volume", VOL_ACTIVE, ACTIVE, created=None),
        LabelledResource("container", CID_OLD, OLD, created=now - 3600),
        LabelledResource("volume", VOL_OLD, OLD, created=None),
    ]
    assert plan_stale(resources, now=now) == (
        LabelledResource("container", CID_OLD, OLD),
        LabelledResource("volume", VOL_OLD, OLD),
    )
    assert len(plan_stale(resources)) == 4  # without a clock nothing counts as recent
    assert plan_stale(resources, now=now, grace=30) == plan_stale(resources)


def test_podman_removes_without_waiting_for_sigterm() -> None:
    calls: list[list[str]] = []

    def run(argv: Sequence[str]) -> QueryResult:
        calls.append(list(argv))
        return QueryResult(0, b"")

    stale = [LabelledResource("container", CID_OLD, OLD), LabelledResource("volume", VOL_OLD, OLD)]
    remove_stale("/usr/bin/podman", stale, run)
    assert calls == [
        ["/usr/bin/podman", "rm", "--force", "--time", "0", CID_OLD],
        ["/usr/bin/podman", "volume", "rm", "--force", VOL_OLD],
    ]
