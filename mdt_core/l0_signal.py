"""L0 signal validation, cleaning and feature extraction.

The implementation remains intentionally lightweight, but it fails safe: invalid
or non-finite samples never reach the state estimator as normal measurements.
"""

from __future__ import annotations

import numpy as np
from scipy import integrate
from scipy import signal as sps

from .config import SignalConfig
from .types import Features, RawWindow, SignalQuality


def _validate_time(t: float) -> None:
    if not np.isfinite(t) or t < 0:
        raise ValueError(f"window.t must be finite and >= 0, got {t!r}")


def _clean_eda(eda: np.ndarray, cfg: SignalConfig) -> tuple[np.ndarray | None, bool]:
    """Return interpolated EDA and whether the source was degraded."""
    if eda.size == 0:
        return None, False
    finite = np.isfinite(eda)
    missing_fraction = 1.0 - float(finite.mean())
    if finite.sum() < 8 or missing_fraction > cfg.max_missing_fraction:
        return None, True
    degraded = not finite.all()
    if degraded:
        index = np.arange(eda.size)
        eda = np.interp(index, index[finite], eda[finite])
    return eda, degraded


def _clean_rr(rr_ms: np.ndarray, cfg: SignalConfig) -> tuple[np.ndarray, bool]:
    if rr_ms.size == 0:
        return rr_ms, False
    lo, hi = cfg.rr_range_ms
    keep = np.isfinite(rr_ms) & (rr_ms >= lo) & (rr_ms <= hi)
    degraded = not keep.all()
    rr_ms = rr_ms[keep]
    cleaned = remove_ectopic(rr_ms)
    degraded = degraded or cleaned.size != rr_ms.size
    return cleaned, degraded


def decompose_eda(
    eda: np.ndarray, fs: float, cutoff: float
) -> tuple[np.ndarray, np.ndarray]:
    """分解为 tonic (SCL) 与 phasic (SCR)。"""
    if eda.size < 8:
        return eda.copy(), np.zeros_like(eda)
    nyq = fs / 2.0
    wn = min(cutoff / nyq, 0.99)
    b, a = sps.butter(2, wn, btype="low")
    smoothed = sps.filtfilt(
        b, a, eda, padlen=min(3 * max(len(a), len(b)), eda.size - 1)
    )
    tonic_wn = min(0.05 / nyq, 0.99)
    bt, at = sps.butter(1, tonic_wn, btype="low")
    tonic = sps.filtfilt(
        bt, at, smoothed, padlen=min(3 * max(len(at), len(bt)), smoothed.size - 1)
    )
    return tonic, smoothed - tonic


def eda_features(eda: np.ndarray, fs: float, cfg: SignalConfig) -> dict:
    tonic, phasic = decompose_eda(eda, fs, cfg.eda_lowpass_hz)
    t = np.arange(tonic.size) / fs
    slope = float(np.polyfit(t, tonic, 1)[0]) if tonic.size > 2 else 0.0
    peaks, _ = sps.find_peaks(
        phasic,
        height=cfg.scr_amplitude_threshold,
        distance=max(1, int(fs)),
    )
    duration_min = max(eda.size / fs / 60.0, 1e-6)
    return {"scl_slope": slope, "scr_rate": float(len(peaks) / duration_min)}


def hrv_features(rr_ms: np.ndarray) -> dict:
    """rr_ms 为毫秒制 RR 间期，已做异位搏动剔除。"""
    if rr_ms.size < 4:
        return {"rmssd": None, "hf_power": None, "sd1": None}
    diff = np.diff(rr_ms)
    rmssd = float(np.sqrt(np.mean(diff**2)))
    sd1 = float(np.sqrt(0.5) * np.std(diff, ddof=1))

    t = np.cumsum(rr_ms) / 1000.0
    fs_interp = 4.0
    grid = np.arange(t[0], t[-1], 1.0 / fs_interp)
    hf = None
    if grid.size >= 32:
        series = np.interp(grid, t, rr_ms)
        series = series - series.mean()
        freqs, psd = sps.welch(series, fs=fs_interp, nperseg=min(256, series.size))
        band = (freqs >= 0.15) & (freqs <= 0.40)
        if band.any():
            hf = float(integrate.trapezoid(psd[band], freqs[band]))
    return {
        "rmssd": rmssd if np.isfinite(rmssd) else None,
        "hf_power": hf if hf is None or np.isfinite(hf) else None,
        "sd1": sd1 if np.isfinite(sd1) else None,
    }


def remove_ectopic(rr_ms: np.ndarray, tol: float = 0.2) -> np.ndarray:
    """相邻差超过 tol 比例的搏动视为异位，直接剔除。"""
    if rr_ms.size < 3:
        return rr_ms
    med = np.median(rr_ms)
    keep = np.abs(rr_ms - med) <= tol * med
    return rr_ms[keep]


def assess_quality(
    window: RawWindow,
    cfg: SignalConfig,
    *,
    n_rr: int,
    has_eda: bool,
    eda_degraded: bool,
    rr_degraded: bool,
    require_rr: bool,
) -> SignalQuality:
    if window.contact_impedance is not None and (
        not np.isfinite(window.contact_impedance)
        or window.contact_impedance < 0
        or window.contact_impedance > cfg.max_impedance
    ):
        return SignalQuality.LOST
    if not has_eda and (not require_rr or n_rr < 4):
        return SignalQuality.LOST
    if window.accel_rms is not None and (
        not np.isfinite(window.accel_rms)
        or window.accel_rms < 0
        or window.accel_rms > cfg.max_accel_rms
    ):
        return SignalQuality.NOISY
    if eda_degraded or rr_degraded or (require_rr and n_rr < cfg.min_rr_count):
        return SignalQuality.NOISY
    return SignalQuality.OK


def extract(window: RawWindow, cfg: SignalConfig) -> Features:
    """L0 主入口：原始窗口 -> 特征。"""
    _validate_time(window.t)
    fs = float(window.eda_fs)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"eda_fs must be finite and > 0, got {fs!r}")
    eda, eda_degraded = _clean_eda(np.asarray(window.eda, dtype=float).reshape(-1), cfg)
    rr, rr_degraded = _clean_rr(
        np.asarray(window.rr_intervals, dtype=float).reshape(-1), cfg
    )
    quality = assess_quality(
        window,
        cfg,
        n_rr=rr.size,
        has_eda=eda is not None,
        eda_degraded=eda_degraded,
        rr_degraded=rr_degraded,
        require_rr=True,
    )

    if quality is SignalQuality.LOST:
        return Features(t=window.t, quality=quality)

    feats = Features(t=window.t, quality=quality)
    if eda is not None:
        for k, v in eda_features(eda, fs, cfg).items():
            setattr(feats, k, v)
    for k, v in hrv_features(rr).items():
        setattr(feats, k, v)
    return feats


def extract_eda(window: RawWindow, cfg: SignalConfig) -> Features:
    """Fast-path extraction for an EDA-only window.

    RR intervals are deliberately ignored so an EDA update is not marked noisy
    merely because the slower HRV window has not arrived yet.
    """
    _validate_time(window.t)
    fs = float(window.eda_fs)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"eda_fs must be finite and > 0, got {fs!r}")
    eda, eda_degraded = _clean_eda(np.asarray(window.eda, dtype=float).reshape(-1), cfg)
    quality = assess_quality(
        window,
        cfg,
        n_rr=0,
        has_eda=eda is not None,
        eda_degraded=eda_degraded,
        rr_degraded=False,
        require_rr=False,
    )
    feats = Features(t=window.t, quality=quality)
    if quality is not SignalQuality.LOST and eda is not None:
        for key, value in eda_features(eda, fs, cfg).items():
            setattr(feats, key, value if np.isfinite(value) else None)
    return feats
