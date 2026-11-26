# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-11-26

### Added

- Initial repository scaffold: bilingual documentation set, admission
  manifest, repository gate, no-hardware/no-DDS/no-network static scan and
  `applications-gate` CI.
- Offline recipe model and validator for the `jcheng-interfaces` v0.1.0
  `MotionCommand` contract, including per-kind payload rules, scope checks and
  the bus-verification requirement for arm and gripper steps.
- Deterministic planner with run-bound SHA-256 idempotency keys.
- Fail-closed runner over an abstract `GatewayPort`: explicit per-session
  authorization, contract and safety preflight, one submission per step, no
  retry, stop request after a failed or indeterminate step, and a `RunReport`
  that preserves the gateway's reason codes.
- Offline `jcheng-applications validate` and `jcheng-applications plan` CLI.
- Example recipe `recipes/example-supervised-transfer.yaml`, explicitly marked
  as an uncalibrated offline example.
- Architecture, integration and recipe documents covering the L4 boundary,
  gateway contract, authorization rules and failure semantics.

### Security

- The tree contains no gateway token, operator environment file, model weight
  or recorded data; the gate rejects them.
