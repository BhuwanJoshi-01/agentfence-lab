# Trust boundaries

This page maps every place where data or control crosses from one trust zone to another, and
records what enforces each crossing **on `main` today**. It reflects `main` on 2026-11-07,
after #116 (AF-0204, process supervisor) was merged. Anything not yet built is marked "not
implemented" with the task that will build it. A design intention in docs/02 or docs/03 is not counted as enforcement.

Sources: docs/02 section 2 (system context), docs/03 sections 1, 2 and 8 (assets, attack surface,
controller threats), docs/07 section 2 (source trust). The trust order from docs/03 section 1 is:
trusted controller policy > container backend enforcement > trusted collectors > adapter tool
outputs > scenario text and model output.

## Trust zones

| Zone | Trust | Contents |
|---|---|---|
| Z1 Host account | protected asset | The user's files and home directory, SSH agent, cloud and agent login state, container engine and its socket, host network and local services. Nothing in the sandbox may reach these. |
| Z2 Operator input | trusted person, content still validated | CLI arguments, environment variables, the user config file under `$XDG_CONFIG_HOME/agentfence/`. |
| Z3 Project content | untrusted | `./agentfence.yaml` and anything else in the working directory. A cloned repository can ship these files. |
| Z4 Trusted controller | trusted | The `agentfence` Python process: CLI, config resolution, scenario validation, host and engine probe, fixture materialization, the mock adapter (in-process), event model, evaluator, storage, reporting, retention and logging. |
| Z5 Scenario content | bundled: reviewed data; user-supplied: untrusted | Bundled manifests shipped as package data, and manifest packs passed to `agentfence scenarios validate <path>`. |
| Z6 Sandbox | untrusted | The agent process and its synthetic workspace. **No sandbox exists yet** (M2). The only agent today is the mock, which runs inside the controller process and acts only through the Z7 collectors. Its workspace is a private directory under `<state>/work/<run-id>/`, not an isolated environment, and results say so (`isolation_tier = "mock-inprocess"`). |
| Z7 Trusted collectors | trusted, outside the agent-writable filesystem | File-access broker, workspace snapshot (diff) and mock network sink (`runtime/collectors.py`), utility runner (`runtime/utility.py`), process supervisor (`runtime/supervisor.py`). Today they run in the controller process next to the mock; a real agent will reach them across the sandbox boundary once M2 exists. |
| Z8 Local outputs | private to the user | State directory (saved reports, the SQLite store, attempt workspaces), files written with `report --output`, stdout, stderr. |
| Z9 Readers | untrusted rendering context | The terminal, CI logs, a browser showing an HTML report, and later the dashboard. |
| Z10 External | untrusted | Internet and local network, model providers, GitHub-hosted CI, PyPI. |

## Diagram

Solid nodes and arrows exist on `main`. Dashed nodes and arrows are not implemented yet. Edge
labels are the boundary IDs in the inventory below. TB-25 to TB-27 (CI and supply chain) are not
drawn.

```mermaid
flowchart LR
    classDef planned stroke-dasharray: 5 5

    subgraph Z2["Z2 Operator input"]
        ARGS["CLI arguments"]
        ENV["Environment variables"]
        UCFG["User config file"]
    end
    subgraph Z3["Z3 Project content (untrusted)"]
        PCFG["./agentfence.yaml"]
    end
    subgraph Z5["Z5 Scenario content"]
        BUNDLED["Bundled manifests (package data)"]
        PACK["User-supplied pack (scenarios validate)"]
    end

    subgraph Z4["Z4 Trusted controller (agentfence process)"]
        CLI["cli.py"]
        CONFIG["config_discovery.py, config.py, strict_yaml.py"]
        CATALOG["scenarios/loader.py, scenario_manifest.py"]
        PROBE["host_probe.py, engine_probe.py"]
        ATTEMPT["attempt.py, fixtures.py, workspace.py"]
        RUNNER["Preflight and sandbox runner"]:::planned
        EVENTS["events.py, coverage.py, trust.py"]
        EVAL["evaluator.py, globs.py"]
        REDACT["redaction.py, log.py"]
        STORAGE["paths.py, atomic.py, db.py, cleanup.py"]
        REPORTER["json_report.py, html_report.py, export.py"]
        API["Local dashboard API"]:::planned
    end

    subgraph Z1["Z1 Host account (protected)"]
        FACTS["OS, Python, PATH, cgroup and sysctl files"]
        ENGINE["Container engine"]
        SECRETS["Home, SSH agent, cloud and agent logins, host sockets"]
    end

    subgraph Z6["Z6 Sandbox (untrusted)"]
        WORKSPACE["Synthetic workspace (private dir, not isolated)"]
        MOCK["Mock agent (runs in Z4 today)"]
        AGENT["Sandboxed agent process"]:::planned
    end
    subgraph Z7["Z7 Trusted collectors"]
        COLLECTORS["Broker, snapshot, mock sink, utility runner, supervisor"]
    end

    subgraph Z8["Z8 Local outputs (private)"]
        STATE[("State dir 0700, files 0600")]
        STREAMS["stdout and stderr"]
    end
    subgraph Z9["Z9 Readers"]
        TERM["Terminal and CI log"]
        BROWSER["Browser: HTML report"]
        DASH["Browser: dashboard"]:::planned
    end
    subgraph Z10["Z10 External"]
        NET["Internet and local network"]
        MODEL["Model provider, post-M8 only"]:::planned
    end

    ARGS -->|TB-01| CLI
    ENV -->|TB-02| CLI
    UCFG -->|"TB-03, TB-04"| CONFIG
    PCFG -->|"TB-03, TB-04"| CONFIG
    BUNDLED -->|TB-05| CATALOG
    PACK -->|TB-06| CATALOG
    FACTS -->|TB-07| PROBE
    ENGINE -->|TB-08| PROBE
    RUNNER -.->|"TB-09, TB-11"| AGENT
    ATTEMPT -->|TB-10| WORKSPACE
    WORKSPACE -->|TB-12| MOCK
    WORKSPACE -.->|TB-12| AGENT
    AGENT -.->|TB-13| SECRETS
    AGENT -.->|TB-14| NET
    MOCK -->|"TB-14, TB-15"| COLLECTORS
    AGENT -.->|TB-15| COLLECTORS
    AGENT -.->|TB-16| EVENTS
    COLLECTORS -->|TB-18| EVENTS
    EVENTS -->|TB-19| EVAL
    STORAGE -->|TB-20| STATE
    REPORTER --> STATE
    REDACT -->|TB-21| STREAMS
    CLI -->|TB-22| STREAMS
    STREAMS --> TERM
    STATE -->|TB-23| BROWSER
    DASH -.->|TB-24| API
    AGENT -.->|TB-17| MODEL
```

## Inventory

Test references are `tests/unit/<file>::<test>`. "None" means no automated test covers the
boundary yet.

### Inputs to the controller

| ID | Source | Destination | Data crossing | Enforcement point | Implemented by today | Tests |
|---|---|---|---|---|---|---|
| TB-01 | Z2 | Z4 | CLI arguments: command, `--json`, `--config`, `--profile`, a `scenarios validate` path | Typer parsing. An unknown command or flag exits 2 with nothing on stdout. Profile names must match `^[a-z][a-z0-9-]{0,62}$`. | `cli.py`, `scenarios/__init__.py` | `test_cli_contract.py::test_usage_errors_exit_2_with_nothing_on_stdout`, `test_cli_doctor_scenarios.py::test_scenarios_list_bad_profile_exits_2` |
| TB-02 | Z2 | Z4 | Environment: `HOME`, `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, `AGENTFENCE_STATE_DIR`, `AGENTFENCE_CONFIG`, `AGENTFENCE_LOG_LEVEL` | Relative `XDG_*` values are ignored. A relative `AGENTFENCE_STATE_DIR` or an unknown log level is an error (exit 2). Environment values are never interpolated into config. | `storage/paths.py`, `storage/config_discovery.py`, `log.py` | `test_storage_paths.py::test_relative_xdg_values_ignored_per_spec`, `test_cli_doctor_scenarios.py::test_relative_state_dir_env_exits_2`, `test_cli_doctor_scenarios.py::test_invalid_log_level_exits_2` |
| TB-03 | Z2, Z3 | Z4 | Which config files are read: user `config.yaml`, project `./agentfence.yaml`, `--config` or `AGENTFENCE_CONFIG` path | A file owned by another user (root excepted), world-writable, or group-writable by a shared group is refused. There is no upward directory search. Symlinks are resolved and the canonical path is recorded. Only regular files of at most 64 KiB are read. | `storage/config_discovery.py`, `core/config.py` (`layer_from_file`) | `test_config_discovery.py::test_world_writable_config_refused`, `test_config_discovery.py::test_foreign_owned_config_refused`, `test_config_discovery.py::test_no_upward_search`, `test_config.py::test_layer_from_oversized_file` |
| TB-04 | Z2, Z3 | Z4 | Config content (YAML) | Strict YAML rejects duplicate keys, anchors and aliases, multiple documents, a non-mapping top level, non-string keys, invalid UTF-8 and documents over 256 KiB. The strict schema rejects unknown keys and type coercion. No layer can change the locked invariants `offline_default`, `isolation_preflight_required`, `no_host_mounts`, `require_exposure_for_pass`, `require_observability_for_pass`, `redact_all_synthetic_secret_values` and `allow_external_connections: false`. `ui.listen` must be loopback. | `core/strict_yaml.py`, `core/config.py`, `core/types.py` | `test_strict_yaml.py::test_rejected_inputs`, `test_strict_yaml.py::test_alias_expansion_bomb_rejected_before_expansion`, `test_config.py::test_no_layer_can_weaken_security_invariants`, `test_config_discovery.py::test_project_cannot_weaken_invariants_via_discovery` |
| TB-05 | Z5 (bundled) | Z4 | Scenario manifests shipped as package data | Strict YAML, then `ScenarioManifest`: no field for commands, hooks, URLs or absolute paths; unknown keys rejected; paths are workspace-relative patterns with no `.`, `..` or empty segments. Pack rules: unique IDs, every benign control paired exactly once, pairs like-for-like. One bad manifest fails the whole listing. At most 200 per profile. | `core/scenario_manifest.py`, `core/types.py`, `scenarios/__init__.py`, `scenarios/loader.py` | `test_scenario_manifest.py::test_executable_or_network_fields_rejected`, `test_types.py::test_unsafe_patterns_rejected`, `test_scenario_catalog.py::test_invalid_manifest_fails_whole_listing`, `test_scenario_catalog.py::test_unpaired_bundled_scenario_rejected` |
| TB-06 | Z5 (user-supplied) | Z4 | Manifest files under a path given to `scenarios validate` | Same schema and pack rules as TB-05. Files open with `O_NOFOLLOW` and `O_NONBLOCK`, so symlinks and FIFOs are refused. Only regular files are read, the size is capped before reading, a symlinked pack root is refused, and a pack holds at most 500 files. Each file's SHA-256 is recorded. Manifests are parsed as data only and never executed. A pack may only name a built-in fixture generator and existing utility oracles, so it cannot bring code. Each adversarial scenario must agree with the fixture its generator builds: the attack surface exists, literal forbidden paths are denied, and only the adversarial surface mentions what each failure rule watches. Not covered: any review gate or registry for community packs (FR-019). | `scenarios/loader.py`, `cli.py` | `test_scenario_loader.py::test_symlinked_manifest_refused`, `test_bundled_scenarios.py::test_unknown_generator_is_a_pack_problem`, `test_pack_fixture_checks.py::test_cli_reproduction_from_the_issue`, `test_pack_fixture_checks.py::test_benign_surface_must_not_carry_the_deviation`, `test_scenario_loader.py::test_fifo_is_not_a_regular_file`, `test_scenario_loader.py::test_pair_must_be_like_for_like`, `test_scenario_loader.py::test_cli_validate_writes_nothing` |
| TB-07 | Z1 | Z4 | Host facts: OS, architecture, Python version, effective UID, `PATH` lookups for `podman`, `docker` and `git`, the cgroup v2 marker file, delegated cgroup controllers, user namespace and AppArmor sysctls, `HOME` | Fact gathering writes nothing. The only processes it starts are the engine queries in TB-08. Judgement is a pure function. Fail-closed: any required check that is not `PASS` gives `UNSUPPORTED`. Runtime paths are home-scrubbed. | `runtime/host_probe.py`, `core/capabilities.py` | `test_host_probe.py::test_host_without_engine_or_kernel_facts_is_unsupported`, `test_capabilities.py::test_any_required_non_pass_is_unsupported`, `test_cli_doctor_scenarios.py::test_doctor_writes_nothing` |
| TB-08 | Z1 (container engine) | Z4 | Runtime capability facts: rootless mode, seccomp, cgroup version, engine version | Read from the engine's own server-side report (`docker info`, `podman info`), never the CLI context name (docs/03 section 4). Queries run under the process supervisor: the absolute path from the `PATH` lookup (a relative `PATH` entry is refused), fixed argv, an allowlisted environment, a 15 s timeout that kills the CLI's process group and a 1 MiB cap on what is kept. A rootful daemon is `FAIL`; a failed or unparseable query is `UNKNOWN`, never `PASS`. | `runtime/engine_probe.py`, `runtime/supervisor.py` | `test_engine_probe.py::test_rootful_engine_is_a_fail_with_reason`, `test_engine_probe.py::test_failures_are_unreachable_not_rootless`, `test_engine_probe.py::test_run_bounded_passes_only_the_allowlisted_environment`, `test_engine_probe.py::test_run_bounded_timeout_and_relative_executable_are_failures` |

### Controller to sandbox, and sandbox outward

| ID | Source | Destination | Data crossing | Enforcement point | Implemented by today | Tests |
|---|---|---|---|---|---|---|
| TB-09 | Z4 | Z6 | Sandbox launch policy: image digest, mounts, user, capabilities, network mode, read-only root, resource limits | The backend builds the full policy, with no host home, sockets or privileged mode, and refuses to launch rather than drop a control. `IsolationPolicy` locks every docs/03 section 4 control and has no field for a bind mount, socket, device, capability or host namespace. The `run` arguments pass an independent allowlist auditor, so a builder bug becomes a refusal. See `docs/reference/sandbox-policy.md`. | `core/sandbox_policy.py`, `runtime/containers.py` build and audit the arguments; `runtime/sandbox.py` runs them and `doctor --self-test` checks 15 properties from inside a real rootless Podman container (all pass on the maintainer host). No scenario runs in a container yet | `test_sandbox_policy.py::test_no_weakening_validates`, `test_sandbox_policy.py::test_auditor_refuses_every_weakening`, `test_sandbox_policy.py::test_a_builder_bug_becomes_a_refusal`, `test_sandbox_selftest.py::test_each_weakening_fails_its_check`, `test_config.py::test_no_layer_can_weaken_security_invariants` |
| TB-10 | Z4, Z5 | Z6 | Fixture materialization: synthetic workspace files placed in the workspace | Synthetic assets only; protected files hold `AF_FAKE_` markers and nothing else. Files are written, never bind-mounted: the root must be new (or an empty private directory we own), directories are `0700`, files are created with `O_CREAT \| O_EXCL \| O_NOFOLLOW` (protected ones `0600`), and the written tree is re-hashed against the fixture digest. Generators are deterministic per seed and their digests are pinned; CI's `scenario-lint` job checks manifests, synthetic data and digests. | `core/fixtures.py`, `runtime/workspace.py`, `scenarios/generators/`, `.github/workflows/ci.yml` (`scenario-lint`) | `test_fixtures.py::test_materialize_writes_exact_bytes_and_modes`, `test_fixtures.py::test_symlinked_root_refused`, `test_fixtures.py::test_non_empty_root_refused`, `test_fixtures.py::test_secret_markers_must_be_synthetic`, `test_fixture_integrity.py::test_fixture_digests_are_pinned` |
| TB-11 | Z4 | Z6 | Agent launch: argv and environment | Argument array with no shell. Explicit environment allowlist. No provider keys and no host `env` passed through (docs/06 section 6). | partly: `runtime/supervisor.py` starts processes from an argv whose executable is an absolute path, with exactly the environment given (nothing inherited), stdin `/dev/null`, its own process group and a recursion guard. No adapter launches an agent process yet: the mock adapter runs in-process, and real agents wait for the M8 gate (adapter `prepare`). | `test_supervisor.py::test_no_shell_metacharacters_are_interpreted`, `test_supervisor.py::test_environment_is_explicit_and_marked`, `test_supervisor.py::test_a_supervised_child_cannot_supervise_again` |
| TB-12 | Z6 (workspace) | Z6 (agent) | Untrusted fixture text the agent reads: README notes, tool output, data files | Not prevented. This is the behavior under test. Five adversarial families (README, CRED, NET, TOOL, ARTIFACT) each ship with a benign control. Workspace files carry no evaluation labels, URLs or credential shapes, and only the adversarial surface carries the deviation. Collectors observe what the agent does after reading, and the evaluator judges it. | `scenarios/mvp/`, `scenarios/generators/` | `test_scenario_library.py::test_fixture_hygiene`, `test_scenario_library.py::test_adversarial_surface_points_at_the_trap_and_benign_does_not`, `test_bundled_scenarios.py::test_benign_surface_does_not_carry_the_deviation` |
| TB-13 | Z6 | Z1 | File, socket and network access aimed at host secrets and services: home, SSH agent, cloud and agent login dirs, engine socket, D-Bus, X11 or Wayland, host loopback services | Mount allowlist, namespaces, no socket mounts, and refusal to launch if a prohibited mount exists. Verified by `doctor --self-test`. Residual risk: a kernel or runtime compromise (docs/03 section 12). The mock reaches files only through the broker, whose walk cannot leave the workspace (TB-15), but that is a property of trusted mock code, not containment. | partly: the self-test verifies, inside a strict-policy container, that only engine-managed mounts, `/tmp` and `/workspace` exist and that no socket file is reachable. No agent runs in a container yet | `test_sandbox_selftest.py::test_each_weakening_fails_its_check`, `tests/isolation/test_self_test_real.py` |
| TB-14 | Z6 | Z10 | Outbound network | Tier 0 has no network. Only the in-boundary mock sink is reachable, and denials are logged. | partly: `runtime/collectors.py` (`MockNetwork`) opens no sockets; an attempt is denied and logged or delivered to the in-memory lab sink, and an external endpoint is always denied. In a strict-policy container the self-test verifies that `lo` is the only interface, that public, metadata and host-gateway addresses are unreachable and that DNS fails (`network.none`, `dns.blocked`). | `test_collectors.py::test_network_deny_mode_logs_attempt_and_denial`, `test_collectors.py::test_network_lab_sink_delivers_in_memory_only`, `test_collectors.py::test_network_external_endpoint_always_denied` |
| TB-15 | Z6 | Z7 | File access requests on synthetic protected assets; requests to the mock sink | The trusted broker decides and logs `file.read_granted` (with a content hash) and `file.read_denied`; the sink logs `network.mock_received`. Paths are walked one component at a time with `O_NOFOLLOW` directory descriptors, so a planted symlink cannot redirect a read or write; FIFOs cannot hang a read; reads and writes are size-capped. Writes are proven by the workspace snapshot, never by the broker. | `runtime/collectors.py` (`WorkspaceBroker`, `WorkspaceSnapshot`, `MockNetwork`) | `test_collectors.py::test_protected_read_is_denied_and_aliased`, `test_collectors.py::test_symlinked_file_cannot_redirect_a_read`, `test_collectors.py::test_symlinked_directory_cannot_redirect_a_write`, `test_collectors.py::test_fifo_read_does_not_hang`, `test_collectors.py::test_read_and_write_limits` |
| TB-16 | Z6 | Z4 | Agent and adapter output: stdout, stderr, structured tool events, final response | Byte caps on capture. Recorded as `agent_reported` and never accepted as proof of an access, exposure, coverage or utility. | partly: trust rules in `core/vocabulary.py` (`SourceTrust.authoritative_for_access`), `core/evaluator.py`, `core/coverage.py`, `core/trust.py`; bounded capture in `runtime/supervisor.py` (excess is read and counted, never kept, and a process holding a pipe open cannot stall teardown). Not yet wired to an agent adapter (see TB-11). | `test_events.py::test_authority_follows_the_collector_registry`, `test_evaluator_golden.py::test_agent_reported_forbidden_write_does_not_fail`, `test_coverage.py::test_agent_cannot_vouch_for_collectors`, `test_supervisor.py::test_output_is_capped_without_blocking_the_child`, `test_supervisor.py::test_a_process_that_escapes_the_group_cannot_stall_teardown` |
| TB-17 | Z6 | Z10 | Model requests and provider credentials (Tier 1) | Only an approved route through a reviewed gateway or host broker, with a dedicated constrained credential and no host login state. | not implemented; blocked on the M8 gate (AF-0801..AF-0808, ADR-007, ADR-012 in `docs/adr/0012-mock-first-and-live-candidate.md`, proposed) | none |

### Evidence to verdict

| ID | Source | Destination | Data crossing | Enforcement point | Implemented by today | Tests |
|---|---|---|---|---|---|---|
| TB-18 | Z7 | Z4 | Observed events | Strict event schema. Targets must be aliases under `WORKSPACE`, `SYNTHETIC_SECRET`, `MOCK_SINK`, `AGENT`, `SANDBOX` or `HOST`, with no `.`, `..`, backslash, NUL or newline. Details are capped at 32 keys of 512 characters, and detail strings starting with `/home/`, `/Users/` or `/root/` are rejected. The decision must match the event kind. Event IDs are content-addressed, so an altered event fails validation. The collector-side recorder assigns sequence numbers. The stream is bounded and ends with an explicit truncation event. A claimed source trust is capped by the collector registry, so an unregistered component counts as `agent_reported`. | `core/events.py`, `core/trust.py` | `test_events.py::test_target_must_be_a_safe_alias`, `test_events.py::test_details_are_bounded_and_path_free`, `test_events.py::test_tampered_event_rejected`, `test_coverage.py::test_recorder_reserves_last_slot_for_truncation_notice`, `test_trust_registry.py::test_spoofed_trust_cannot_prove_violation_exposure_or_coverage` |
| TB-19 | Z4 (event stream, mixed trust) | Z4 (verdict) | Events plus scenario rules in, verdict out | `validate_stream` rejects mixed attempts, duplicate IDs, sequence gaps and time going backwards. Only `host_observed` and `broker_observed` events count. Coverage comes only from trusted `collector.status` events. A missing or truncated required channel gives `INCONCLUSIVE`. A diff never proves a read. Attempts and completed effects are kept apart. In globs, `*` never crosses `/`. | `core/evaluator.py`, `core/coverage.py`, `core/events.py`, `core/globs.py` | `test_evaluator_golden.py::test_golden_matrix`, `test_evaluator_golden.py::test_pass_requires_exposure_evidence`, `test_evaluator_golden.py::test_inconsistent_stream_is_inconclusive`, `test_coverage.py::test_truncated_stream_downgrades_every_channel` |

### Controller outputs

| ID | Source | Destination | Data crossing | Enforcement point | Implemented by today | Tests |
|---|---|---|---|---|---|---|
| TB-20 | Z4 | Z8 (state dir) | Saved reports, the SQLite store, attempt workspaces | Directories are created `0700` and files `0600` from creation. Existing paths that are loose, owned by someone else or symlinked are refused, not changed. Writes are atomic replaces with fsync. Run IDs must be canonical UUIDs. `safe_child` rejects traversal and symlink escape. The store refuses a loose or symlinked database file and enforces foreign keys and the outcome vocabulary. Events are append-only (triggers) and hash-chained per attempt, verdicts cannot be updated, and the retention purge is the only path that deletes events. Attempt workspaces are removed in `finally` and stale ones reconciled before the next run; cleanup only touches canonical run-UUID entries and never follows symlinks. | `storage/paths.py`, `storage/atomic.py`, `storage/db.py`, `runtime/cleanup.py` | `test_storage_paths.py::test_existing_loose_directory_refused_not_changed`, `test_storage_paths.py::test_safe_child_rejects_symlink_escape`, `test_storage_atomic.py::test_refuses_to_replace_symlink`, `test_store.py::test_evidence_is_append_only`, `test_store.py::test_symlinked_database_refused`, `test_interrupt_and_recovery.py::test_reconcile_never_follows_symlinks`, `test_retention_and_hardening.py::test_orphans_removed_unknown_entries_kept_symlinks_not_followed` |
| TB-21 | Z4 | Z8 (stderr), then Z9 | Log records and error messages | Logs go to stderr only. Only allowlisted field names print a value. `scrub_text` removes synthetic markers, credential shapes, PEM blocks, home paths and terminal control sequences. Exceptions log the type, never a traceback. Output is capped at 1 MiB with one truncation notice. | `log.py`, `core/redaction.py`, `cli.py` (`_fail`) | `test_log.py::test_json_lines_with_allowlisted_fields_only`, `test_log.py::test_exceptions_log_type_not_traceback`, `test_log.py::test_output_is_capped_with_single_notice`, `test_redaction.py::test_terminal_control_sequences_stripped` |
| TB-22 | Z4 | Z8 (stdout), then Z9 | Command output: `version`, `doctor`, `scenarios list` and `validate`, `test`, `runs list`, `show` and `compare`, `report`, `cleanup` | With `--json`, stdout carries exactly one JSON document, so logs never mix in, and JSON escapes control characters. Config source paths, runtime paths and validation problem messages are home-scrubbed; scenario titles and file names pass through `scrub_text` before printing. | `cli.py` | `test_cli_contract.py::test_json_stdout_stays_pure_with_verbose_logging`, `test_cli_doctor_scenarios.py::test_doctor_scrubs_home_in_config_source`, `test_host_probe.py::test_runtime_paths_are_home_scrubbed`, `test_terminal_injection.py::test_scenarios_validate_scrubs_hostile_file_names`, `test_cli_test_command.py::test_json_output_is_the_saved_report` |
| TB-23 | Z8 | Z9 (browser) | JSON and HTML reports | Reports cite events by ID and alias, so they hold no host paths or synthetic secret values. The HTML report is one self-contained file: escaped text, a strict CSP with no script and a hashed style, no remote resources, embedded JSON that cannot execute. `report` re-checks the saved report's hash, the run's hash chain and every cited evidence ID before showing or exporting, and writes only a new private file. Before anything is printed or written, the exact output is scanned for synthetic markers, private keys and credential shapes, and a hit refuses the export (docs/07 section 10 item 8). | `reporting/json_report.py`, `reporting/html_report.py`, `reporting/export.py`, `core/redaction.py` (`find_secrets`) | `test_html_report.py::test_strict_csp_with_no_scripts_and_hashed_style`, `test_html_report.py::test_hostile_strings_are_escaped_everywhere`, `test_report.py::test_report_leaks_no_paths_or_secrets`, `test_runs_and_report_export.py::test_tampered_evidence_is_refused_even_with_matching_report`, `test_retention_and_hardening.py::test_synthetic_secret_never_reaches_any_artifact`, `test_export_preflight.py::test_leak_in_rendered_report_is_refused_and_nothing_is_written` |
| TB-24 | Z9 (browser) | Z4 (local API) | Dashboard HTTP requests | Loopback bind, origin check, session token, read-only, no arbitrary path reads. Every request must name the bound loopback host (DNS rebinding is refused), come from the same origin if it has an `Origin`, and carry the random bearer token printed by `agentfence ui`. Only `GET`/`HEAD` exist. Run data is served from the verified report after the secret scan, with `no-store`, `nosniff` and a deny-all CSP. See `docs/reference/local-api.md` and ADR-015. | `api/app.py`, `cli.py` (`ui`), `core/config.py` (loopback-only `ui.listen`); the browser dashboard is not implemented (AF-0602, AF-0607) | `test_local_api.py::test_foreign_host_headers_are_refused`, `test_local_api.py::test_cross_origin_requests_are_refused`, `test_local_api.py::test_session_token_is_required_exactly`, `test_local_api.py::test_the_api_is_read_only`, `test_local_api.py::test_a_real_server_on_loopback`, `test_config.py::test_ui_never_binds_non_loopback` |

### Development and supply chain

| ID | Source | Destination | Data crossing | Enforcement point | Implemented by today | Tests |
|---|---|---|---|---|---|---|
| TB-25 | Z10 (pull request from a fork) | Z10 (CI runner) | Untrusted code executed in CI | `permissions: contents: read`, no secrets, no `pull_request_target`, `persist-credentials: false`, third-party actions pinned to commit SHAs. Isolation tests are deselected on hosted runners. | `.github/workflows/ci.yml` | no automated test; reviewed when the workflow changes |
| TB-26 | Z10 (PyPI) | Z4 | Third-party Python dependencies | `uv.lock` with `UV_FROZEN=1`, `uv lock --check`, and `pip-audit --strict` on runtime dependencies. | `uv.lock`, `.github/workflows/ci.yml` (`lint`, `audit` jobs) | CI jobs `lint` and `audit` |
| TB-27 | Z4 (build) | Z10 (users installing the wheel) | Built wheel contents | CI fails if the wheel contains `tests/`, `.git/`, `.agentfence/` or `.sqlite3` files, then installs it into a fresh venv and runs the CLI. | `.github/workflows/ci.yml` (`package` job) | CI job `package` |

## Known gaps on `main`

These were found while writing or refreshing this inventory. This page only documents them.
Fixing them belongs to the task named.

1. **Resolved (#85, AF-0201): doctor detects a rootful runtime.** Rootless mode is read from
   each engine's own server-side report (`docker info` security options, `podman info`
   `host.security.rootless`); a rootful daemon is `FAIL`, an unqueryable engine `UNKNOWN`.
   Seccomp, cgroup delegation and user namespaces are checked too
   (`runtime/engine_probe.py`, `runtime/host_probe.py`).
2. **Resolved (#86): human-readable CLI output is now scrubbed.** Manifest `title` and
   `legitimate_task.description` reject Unicode control and invisible formatting characters
   (`DisplayText`), and `scenarios list` / `scenarios validate` pass titles and file names
   through `scrub_text` before printing. Covered by `test_terminal_injection.py`.
3. **Resolved (#87): event details reject any host path.** `looks_like_host_path()` refuses
   absolute POSIX, `~`, Windows drive and UNC paths, `file:` URIs and text that embeds a
   well-known host directory. Covered by `test_events.py::test_details_reject_any_host_path`.
4. **Resolved (#88): source trust is checked against a collector registry.**
   `core/trust.py` maps each collector component to the most trust it may claim. Events
   from unregistered components count as `agent_reported`, and claims above a component's
   ceiling are lowered to it (evaluator 1.2.0). Covered by `test_trust_registry.py`.
5. **Resolved (#119): reports are scanned before they leave.** `report` and `runs show` run
   `preflight_scan` on the exact text they are about to print or write. A synthetic marker,
   private key block or credential shape refuses the export (exit 4) with kinds and line
   numbers only. Covered by `test_export_preflight.py`.
6. **Resolved (#118): packs are checked against their fixtures.** The loader generates both
   variants of every adversarial scenario and rejects a missing attack surface, a literal
   forbidden path outside `boundary.deny` or inside `boundary.allow`, a rule the adversarial
   surface never mentions, and a benign surface that carries the deviation. Covered by
   `test_pack_fixture_checks.py`.

## Keeping this page current

Update the inventory in the same pull request that adds, removes or changes an enforcement
point. When a "not implemented" row becomes real, replace the task ID with the module and add
the test that proves the boundary holds, including a negative case.
