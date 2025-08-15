import math
import numpy as np
import streamlit as st
import matplotlib.pyplot as plt

st.set_page_config(page_title="Concentric Ring Interference", layout="wide")

# -----------------------------
# Sidebar controls
# -----------------------------
st.sidebar.header("Field & Grid")
extent = st.sidebar.number_input("Half-extent (ft) – domain is [-E, +E] in both axes", 50, 2000, 250, step=50)
resolution = st.sidebar.number_input("Grid resolution (ft per cell)", 0.5, 10.0, 2.0, step=0.5)
wavelength = st.sidebar.number_input("Wavelength (ft)", 1.0, 1000.0, 28.0, step=1.0)
frequency = st.sidebar.number_input("Frequency (Hz, used for averaging window)", 0.01, 10.0, 0.15, step=0.01)
samples = st.sidebar.slider("Time samples per period", 8, 72, 24, step=4)

st.sidebar.header("Attenuation & Envelope")
attenuation_mode = st.sidebar.selectbox("Attenuation mode", ["none", "spherical", "cylindrical"], index=2)
use_decay = st.sidebar.checkbox("Use exponential decay envelope", value=False)
decay_len = st.sidebar.number_input("Exponential decay length (ft)", 1.0, 10000.0, 120.0, step=10.0) if use_decay else None

st.sidebar.header("Rings")
n_rings = st.sidebar.slider("Number of rings", 1, 12, 4)
default_radii = [40.0, 75.0, 110.0, 145.0][:n_rings] or [40.0]
default_amps  = [100.0]*n_rings
default_phase = [0.0]*n_rings

# Use a mini table to edit ring params
import pandas as pd
df = pd.DataFrame({
    "radius_ft": default_radii + [np.nan]*(n_rings - len(default_radii)),
    "amplitude": default_amps  + [np.nan]*(n_rings - len(default_amps)),
    "phase_rad": default_phase + [np.nan]*(n_rings - len(default_phase)),
}).iloc[:n_rings]

st.sidebar.caption("Edit ring radii, amplitudes, and phase (radians). Leave phase 0 for in-phase.")
ring_table = st.sidebar.data_editor(df, use_container_width=True, num_rows="fixed", key="ring_table")

points_per_ring = st.sidebar.slider("Points per ring (discretization)", 12, 180, 60, step=12)

st.sidebar.header("Gain Map Display")
gain_pct_limit = st.sidebar.slider("Gain colormap ±% range", 1, 100, 10, step=1)
show_guides = st.sidebar.checkbox("Show ring guides on plots", True)

# -----------------------------
# Build grid
# -----------------------------
xs = np.arange(-extent, extent + 1e-9, resolution)
ys = np.arange(-extent, extent + 1e-9, resolution)
X, Y = np.meshgrid(xs, ys)
cx, cy = 0.0, 0.0  # center rings at origin for simplicity
k = 2*np.pi / wavelength
omega = 2*np.pi * frequency

# -----------------------------
# Parse ring params
# -----------------------------
rings = []
for i in range(n_rings):
    r = float(ring_table.loc[i, "radius_ft"])
    a = float(ring_table.loc[i, "amplitude"])
    p = float(ring_table.loc[i, "phase_rad"])
    rings.append((r, a, p))

def ring_sources(R, points):
    theta = np.linspace(0, 2*np.pi, points, endpoint=False)
    sx = cx + R*np.cos(theta)
    sy = cy + R*np.sin(theta)
    return sx, sy

def attenuation(R):
    if attenuation_mode == "none":
        att = 1.0
    elif attenuation_mode == "spherical":
        att = 1.0 / (R + 1e-9)
    elif attenuation_mode == "cylindrical":
        att = 1.0 / np.sqrt(R + 1e-9)
    else:
        att = 1.0
    if decay_len is not None:
        att = att * np.exp(-R / decay_len)
    return att

@st.cache_data(show_spinner=False)
def compute_maps(xs, ys, rings, points_per_ring, frequency, samples, k, omega,
                 attenuation_mode, decay_len):
    X, Y = np.meshgrid(xs, ys)
    T = 1.0 / frequency
    times = np.linspace(0, T, samples, endpoint=False)

    # Precompute ring source coords
    ring_coords = []
    for (R, A, P) in rings:
        SX, SY = ring_sources(R, points_per_ring)
        ring_coords.append((R, A, P, SX, SY))

    def field_from_ring(SX, SY, A, phase_offset, t):
        dxs = X[..., None] - SX[None, None, :]
        dys = Y[..., None] - SY[None, None, :]
        R = np.hypot(dxs, dys)
        phi = k*R - omega*t + phase_offset
        return np.sum(A * np.cos(phi) * attenuation(R), axis=-1)

    I_baseline = np.zeros_like(X, dtype=float)
    I_total_accum = np.zeros_like(X, dtype=float)

    for t in times:
        Ft = np.zeros_like(X, dtype=float)
        for (R, A, P, SX, SY) in ring_coords:
            Fr = field_from_ring(SX, SY, A, P, t)
            I_baseline += (Fr**2) / samples
            Ft += Fr
        I_total_accum += (Ft**2) / samples

    return I_baseline, I_total_accum

with st.spinner("Computing maps..."):
    I_baseline, I_total = compute_maps(xs, ys, rings, points_per_ring, frequency, samples, k, omega,
                                       attenuation_mode, decay_len)

I_gain = I_total - I_baseline
eps = 1e-12
gain_pct = 100.0 * I_gain / (I_baseline + eps)

def draw_guides(ax):
    if not show_guides:
        return
    th = np.linspace(0, 2*np.pi, 360)
    for (R, _, _) in rings:
        ax.plot(cx + R*np.cos(th), cy + R*np.sin(th), color='k', lw=0.8, alpha=0.6)

# -----------------------------
# Layout: three plots side-by-side
# -----------------------------
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("Baseline (no interference)")
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    im = ax.imshow(I_baseline, extent=[xs.min(), xs.max(), ys.min(), ys.max()],
                   origin='lower', interpolation='bilinear')
    draw_guides(ax)
    ax.set_xlabel("x (ft)"); ax.set_ylabel("y (ft)")
    st.pyplot(fig, clear_figure=True)

with col2:
    st.subheader("Total (with interference)")
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    im = ax.imshow(I_total, extent=[xs.min(), xs.max(), ys.min(), ys.max()],
                   origin='lower', interpolation='bilinear')
    draw_guides(ax)
    ax.set_xlabel("x (ft)"); ax.set_ylabel("y (ft)")
    st.pyplot(fig, clear_figure=True)

with col3:
    st.subheader("Interference Gain (%)")
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    v = float(gain_pct_limit)
    im = ax.imshow(gain_pct, extent=[xs.min(), xs.max(), ys.min(), ys.max()],
                   origin='lower', cmap='coolwarm', vmin=-v, vmax=v, interpolation='bilinear')
    draw_guides(ax)
    ax.set_xlabel("x (ft)"); ax.set_ylabel("y (ft)")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="Gain (%)")
    st.pyplot(fig, clear_figure=True)

st.caption(
    "Notes: Baseline = sum of each ring’s intensity alone (no cross-terms). "
    "Total = intensity of the sum of all rings. Gain(%) = (Total−Baseline)/Baseline×100."
)

# -----------------------------
# Optional instantaneous field preview with a time slider
# -----------------------------
with st.expander("Instantaneous Field (preview)"):
    t_slider = st.slider("Time within one period", 0.0, 1.0, 0.0, 0.01)
    t = t_slider / max(frequency, 1e-6)
    X, Y = np.meshgrid(xs, ys)

    def field_at_time(t):
        F = np.zeros_like(X, dtype=float)
        for (R, A, P) in rings:
            SX, SY = ring_sources(R, points_per_ring)
            dxs = X[..., None] - SX[None, None, :]
            dys = Y[..., None] - SY[None, None, :]
            RR = np.hypot(dxs, dys)
            phi = k*RR - omega*t + P
            F += np.sum(A * np.cos(phi) * attenuation(RR), axis=-1)
        return F

    F_now = field_at_time(t)
    fig, ax = plt.subplots(figsize=(5.5, 5.5))
    im = ax.imshow(F_now, extent=[xs.min(), xs.max(), ys.min(), ys.max()],
                   origin='lower', cmap='RdBu', interpolation='bilinear')
    draw_guides(ax)
    ax.set_xlabel("x (ft)"); ax.set_ylabel("y (ft)")
    st.pyplot(fig, clear_figure=True)
