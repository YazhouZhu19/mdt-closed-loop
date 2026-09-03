# Closed-Loop Control for Multicomponent Music Digital Therapeutics: A Concise Technical Report

[简体中文](TECHNICAL_REPORT.zh-CN.md)

**Version:** 0.1.0  
**Date:** September 3, 2026  
**Status:** Research prototype; not a medical device and not supported by clinical efficacy evidence

## Abstract

This project implements a soft-real-time closed-loop control framework for multicomponent music digital therapeutics (MDT). Electrodermal activity (EDA) and inter-beat interval (RR/HRV) measurements are transformed into a continuous arousal estimate using personalized baseline normalization, multimodal fusion, and a one-dimensional Kalman filter. A response-adaptive ISO trajectory defines the therapeutic reference. A reliability-weighted PI controller converts tracking error into a bounded control signal, and a music-grammar layer maps that signal to musical parameter changes subject to rate, range, and structural-boundary constraints.

Unlike related systems centered on deep EEG decoding, song recommendation, or end-to-end generative music, this project emphasizes **interpretable state estimation, explicit uncertainty, stable feedback control, musically safe actuation, and experimentally distinguishable control conditions**. The current implementation supports algorithm review, offline simulation, and integration research. It is not suitable for direct clinical use.

## 1. Objective and system architecture

The system addresses a limitation of fixed playlists and open-loop ISO interventions: neither can adjust the intervention according to an individual's evolving physiological response. The implemented loop is:

```text
EDA and RR/HRV acquisition
          ↓
Signal quality and physiological features (L0)
          ↓
Personalized arousal and uncertainty estimation (L1)
          ↓
Response-adaptive ISO therapeutic reference (L2)
          ↓
Reliability-weighted PI control (L3)
          ↓
Musical parameter and structural constraints (L3.5)
          ↓
Music output → participant response → new measurement
```

The implementation uses a multi-rate, event-driven architecture. The fast EDA path is designed to update every two seconds, while the combined EDA/HRV path updates every 15 seconds. Musical changes are committed only when the audio clock reports a bar or phrase boundary, separating physiological computation time from musical execution time.

## 2. Core algorithms

### 2.1 Personalized state estimation

Rather than applying a population threshold directly, the system estimates feature means and standard deviations from several participant-specific resting calibration sessions:

```text
z_j = (x_j - mu_j) / sigma_j
```

EDA features and sign-inverted parasympathetic HRV features are first fused within and across modalities. A logistic transform maps the fused observation to normalized arousal in `[0, 1]`. A one-dimensional Kalman filter then smooths the observation and reports posterior variance. Prediction continues when measurements are missing, so uncertainty increases with signal loss instead of preserving stale confidence.

### 2.2 Response-adaptive ISO trajectory

The fixed ISO trajectory follows three phases: match the initial state, gradually descend, and hold at a lower reference. The full closed-loop arm adds response adaptation. Good tracking accelerates reference progress, a participant lag slows it, and an unreliable state estimate freezes it. The trajectory remains monotonic and does not raise arousal when the initial estimate is already below the configured floor. The fixed wall-clock ISO trajectory remains available as an open-loop comparator.

### 2.3 Uncertainty-aware PI control

The controller combines observation confidence and Kalman posterior uncertainty into an actuation reliability factor:

```text
e(k) = target(k) - arousal(k)
q(k) = confidence(k) * uncertainty_scale(P(k))
I(k) = clamp(I(k-1) + q(k) * e(k) * dt)
u(k) = clamp(q(k) * (Kp * e(k) + Ki * I(k)))
```

Inside the error deadband, actuation is held and integral memory leaks gradually. As uncertainty grows, output is continuously derated. Beyond the hard uncertainty limit, closed-loop actuation stops and pending commands are canceled. Only the safely actionable fraction of error enters the integrator, reducing wind-up during degraded sensing. No derivative term is used because differentiation would amplify high-frequency artifacts in noisy physiological measurements.

### 2.4 Music-grammar constraints

The raw control output is never sent directly to playback. The music-grammar layer constrains tempo range and rate of change, permits only bounded and reversible stem-layer transitions, and commits continuous parameters at bar boundaries and structural changes at phrase boundaries. Dynamics, harmonic brightness, rhythmic accent, and reverb are derived consistently from normalized tempo, reducing discontinuities and control-induced musical jitter.

## 3. Differences from related systems

| Related system | Primary approach | Similarity | Distinguishing characteristics of this project |
|---|---|---|---|
| [MindMelody](https://arxiv.org/abs/2605.01235) | EEG, Transformer-GNN decoding, LLM intervention planning, and controllable music generation | Both sense state, plan an intervention, produce music, and update from feedback | This project uses a lightweight, interpretable EDA/HRV state-space model and PI controller, without requiring a large generative stack; uncertainty and musical execution boundaries are explicit |
| [LUCID AMRS](https://www.lucidtherapeutics.com/tech) | Affective music recommendation, the ISO principle, and personalized sequencing | Both adapt music toward an affective or arousal target | LUCID is proprietary; this project exposes its control logic and implements full-loop, fixed-ISO, direct-guidance, and sham research paths |
| [EEG reinforcement-learning music sequencing](https://pubmed.ncbi.nlm.nih.gov/33019236/) | EEG-derived emotion states and Q-learning for song selection | Both model intervention as a state-action-feedback process | The published study focuses on offline song selection; this project addresses continuous online control, degrading signal quality, and parameter-level musical constraints |
| [Timeflux](https://github.com/timeflux/timeflux), [BrainFlow](https://github.com/brainflow-dev/brainflow), and [LSL](https://github.com/sccn/labstreaminglayer) | Real-time biosignal streaming, device access, and synchronization infrastructure | They can provide acquisition and streaming foundations for this project | They do not implement MDT-specific therapeutic references, control policy, music grammar, or experimental arms |

The general pattern of sensing, inference, music selection or generation, and feedback already appears in public research. The project should therefore not claim to be the first closed-loop music intervention. A more accurate positioning is:

> A model-driven, uncertainty-aware, and auditable closed-loop control framework for multicomponent music digital therapeutics.

Within the scope of the public-source search conducted for this report, no other open-source project was identified that combines personalized EDA/HRV baselines, Kalman posterior uncertainty, a response-adaptive ISO reference, reliability-weighted PI control, musical structural constraints, and multiple research arms. This finding indicates a differentiated engineering combination; it is not a comprehensive academic novelty or patentability determination.

## 4. Project characteristics

1. **Interpretable closed loop.** State, reference, error, reliability, control output, and musical action all have explicit meanings, making decisions reconstructable rather than dependent on an opaque end-to-end mapping.
2. **Uncertainty is part of control.** Posterior uncertainty is used to derate both trajectory progress and actuation, with safe hold behavior during signal loss.
3. **Therapeutic progress follows individual response.** The ISO reference is not driven solely by wall-clock time; it adapts to tracking performance while preserving fixed ISO as a comparator.
4. **Control actions must remain musically valid.** Range and rate limits plus bar- and phrase-boundary commits make musical continuity a first-class system constraint.
5. **Designed for experimental discrimination.** `FULL_LOOP`, `DIRECT`, `ISO`, and `SHAM` paths, together with dose, outcome, safety escalation, and synchronized logging, can help distinguish closed-loop, ISO-specific, and nonspecific intervention effects.

## 5. Software validation and current boundaries

All 47 deterministic tests in the current repository pass. They cover missing signals, baseline readiness, numerical stability, uncertainty growth and controller derating, adaptive trajectories, parameter bounds, session lifecycle, safety aborts, sham feedback, and a 40-minute synthetic closed-loop run. Test data are generated mathematically with fixed random seeds, isolated in temporary directories, and contain no participant data.

These results establish software behavior only under the tested conditions. They do not demonstrate clinical efficacy or production real-time performance. The project still lacks validated sensor drivers, a functional low-latency music engine, end-to-end latency and jitter measurements, calibration on clinical data, secure health-data infrastructure, a staffed safety-response workflow, and regulated medical-software lifecycle evidence.

## 6. Conclusion

The project implements a complete algorithmic chain from physiological sensing and latent-state estimation to therapeutic reference generation, feedback control, and constrained musical execution. Its principal distinction is not a larger AI model, but a lightweight and transparent control architecture that can degrade safely when its inputs become unreliable while retaining explicit paths for controlled comparison and decision audit. The next priority should be real-device and music-engine integration, followed by ethically governed validation of the estimator and controller parameters. Machine learning can then be considered selectively for personalized response prediction or safety-constrained policy learning.

## References

1. *MindMelody: A Closed-Loop EEG-Driven Framework for Personalized Music Intervention.* [arXiv:2605.01235](https://arxiv.org/abs/2605.01235)
2. LUCID Therapeutics. [Affective Music Recommendation and Biological Music Information Retrieval](https://www.lucidtherapeutics.com/tech)
3. Ehrlich et al. *Reinforcement Learning using EEG signals for Therapeutic Use of Music in Emotion Management.* [PubMed](https://pubmed.ncbi.nlm.nih.gov/33019236/)
4. Official documentation for [Timeflux](https://timeflux.io/), [BrainFlow](https://brainflow.org/), and the [Lab Streaming Layer](https://labstreaminglayer.org/).
