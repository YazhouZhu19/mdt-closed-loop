# Changelog

All notable changes to this project will be documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases should use semantic versioning once a stable public API is declared.

## [Unreleased]

### Planned

- Select and add a project license.
- Implement and validate a real music-engine adapter.
- Validate signal processing against reference software and device recordings under an approved protocol.
- Add measured end-to-end latency and fault-recovery tests.

## [0.1.0] - 2026-08-28

### Added

- Installable `mdt_core` package and public API.
- Validated EDA/HRV signal-cleaning and feature paths.
- Personalized baseline, multimodal arousal fusion, and Kalman smoothing.
- ISO/DIRECT trajectories, bounded PI control, and music-grammar constraints.
- Multi-rate session orchestration and explicit audio-boundary commits.
- Calibration, treatment-dose, outcome, futility, safety, and four-arm research logic.
- Atomic JSON recorder, null engine, and pre-registered SHAM engine.
- Deterministic isolated synthetic demonstration and 36-test suite.
- English and Simplified Chinese documentation.

### Safety

- Low-confidence input disables live feedback and cancels pending changes.
- Safety-aborted sessions do not count as completed dose.
- Calibration never actuates music or increments treatment dose.

### Known limitations

- Research prototype only; no clinical validation.
- No functioning vendor music engine or real-device latency validation.
- No production health-data security controls.
