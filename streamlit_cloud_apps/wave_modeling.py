import numpy as np
import streamlit as st
import matplotlib.pyplot as plt
import pandas as pd

st.set_page_config(page_title="Avg vs Baseline — Rings + External Wave", layout="wide")

# -----------------------------
# Sidebar controls
# -----------------------------
st.sidebar.header("Field & Grid")
half_extent = st.sidebar.number_input("Half-extent (ft) → domain [-E, +E]", 50, 2000, 250, step=50)
resolution = st.sidebar.number_input("Grid resolution (ft)", 0.5, 10.0, 2.0, step=0.5)
wavelength = st.sidebar.number_input("Wavelength (ft)", 1.0, 2000.0, 28.0, step=1.0)
frequency = st.sidebar.number_input("Frequency (Hz)", 0.01, 5.0, 0.15, step=0.01)
samples = st.sidebar.slider("Time samples per period", 8, 96, 24, step=4)

st.sidebar.header("Rings & Nodes")
n_rings = st.sidebar.slider("Number of rings", 1, 12, 4)
node_mode = st.sidebar.radio("Nodes per ring", ["4 (0/90/180/270°)", "6 (every 60°)"], index=0)
nodes_per_ring = 4 if node_mode.startswith("4") else 6

# Defaults
default_radii = [40.0, 75.0, 110.0, 145.0][:n_rings] or [40.0]
default_amps  = [100.0]*n_rings

st.sidebar.caption("Edit ring radii (ft) and amplitudes. All nodes on a ring share amplitude & phase=0.")
ring_df = pd.DataFrame({
    "radius_ft": default_radii + [np.nan]*(n_rings - len(default_radii)),
    "amplitude": default_amps  + [np.nan]*(n_rings - len(default_amps)),
}).iloc[:n_rings]
ring_table = st.sidebar.data_editor(ring_df, use_container_width=True, num_rows="fixed", key="ring_table")

st.sidebar.header("Propagation / Loss")
attenuation_mode = st.sidebar.selectbox("Attenuation", ["none", "spherical (1/r)", "cylindrical (1/√r)"], index=2)
use_decay = st.sidebar.checkbox("Exponential decay envelope", value=False)
decay_len = st.sidebar.number_input("Decay length (ft)", 1.0, 10000.0, 120.0, step=10.0) if use_decay else None

st.sidebar.header("External Wave")
use_external = st.sidebar.checkbox("Include external wave", value=True)
ext_type = st.sidebar.selectbox("External type", ["Plane wave", "Point source"], index=0, disabled=not use_external)
ext_amp = st.sidebar.number_input("External amplitude", 0.0, 1e6, 80.0, step=5.0, disabled=not use_external)

ext_angle_deg = 0.0
ext_px = ext_py = 0.0
if use_external:
    if ext_type == "Plane wave":
        ext_angle_deg = st.sidebar.slider("Angle (deg, 0°→+x, 90°→+y)", 0, 359, 45)
    else:
        ext_px = st.sidebar.number_input("Point source X (ft)", -half_extent, half_extent, -half_extent, step=10)
        ext_py = st.sidebar.number_input("Point source Y (ft)", -half_extent, half_extent, 0, step=10)

st.sidebar.header("Gain Map Display")
gain_pct_limit = st.sidebar.slider("Gain colormap ±% range", 1, 100, 10, step=1)
show_guides = st.sidebar.checkbox("Show ring guides", True)
show_nodes = st.sidebar.checkbox("Show node markers", False)

# -----------------------------
# Build grid & constants
# -----------------------------
xs = np.arange(-half_extent, half_extent + 1e-9, resolution)
ys = np.arange(-half_extent, half_extent + 1e-9, resolution)
X, Y = np.meshgrid(xs, ys)
cx, cy = 0.0, 0.0
k = 2*np.pi / wavelength
omega = 2*np.pi*frequency

# Parse rings
rings = []
for i in range(n_rings):
    r = float(ring_table.loc[i, "radius_ft"])
    a = float(ring_table.loc[i, "amplitude"])
    rings.append((r, a))

def ring_nodes(radius, count):
    if count == 4:
        ang = np.deg2rad([0, 90, 180, 270])
    elif count == 6:
        ang = np.deg2rad([0, 60, 120, 180, 240, 300])
    else:
        raise ValueError("Unsupported node count")
    return cx + radius*np.cos(ang), cy + radius*np.sin(ang)

def attenuation(R):
    if attenuation_mode.startswith("none"):
        att = 1.0
    elif attenuation_mode.startswith("spherical"):
        att = 1.0 / (R + 1e-9)
    else:
        att = 1.0 / np.sqrt(R + 1e-9)
    if decay_len is not None:
        att = att * np.exp(-R / decay_len)
    return att

def field_from_ring_nodes(SX, SY, amplitude, t, phase_offset=0.0):
    dxs = X[..., None] - SX[None, None, :]
    dys = Y[..., None] - SY[None, None, :]
    R = np.hypot(dxs, dys)
    phi = k*R - omega*t + phase_offset
    return np.sum(amplitude * np.cos(phi) * attenuation(R), axis=-1)

def external_field(t):
    if not use_external or ext_amp == 0:
        return np.zeros_like(X)
    if ext_type == "Plane wave":
        th = np.deg2rad(ext_angle_deg)
        # plane wave phase = k * (x cosθ + y sinθ) - ωt
        phase = k*(X*np.cos(th) + Y*np.sin(th)) - omega*t
        return ext_amp * np.cos(phase)
    else:  # Point source
        dx = X - ext_px
        dy = Y - ext_py
        R = np.hypot(dx, dy)
        phase = k*R - omega*t
        return ext_amp * np.cos(phase) * attenuation(R)

@st.cache_data(show_spinner=False)
def compute_gain_map(xs, ys, rings, nodes_per_ring, frequency, samples,
                     use_external, ext_type, ext_amp, ext_angle_deg, ext_px, ext_py,
                     attenuation_mode, decay_len):
    X, Y = np.meshgrid(xs, ys)

    def _atten(R):
        if attenuation_mode.startswith("none"):
            att = 1.0
        elif attenuation_mode.startswith("spherical"):
            att = 1.0 / (R + 1e-9)
        else:
            att = 1.0 / np.sqrt(R + 1e-9)
        if decay_len is not None:
            att = att * np.exp(-R / decay_len)
        return att

    def ring_field(SX, SY, A, t):
        dxs = X[..., None] - SX[None, None, :]
        dys = Y[..., None] - SY[None, None, :]
        R = np.hypot(dxs, dys)
        return np.sum(A * np.cos(k*R - (2*np.pi*frequency)*t) * _atten(R), axis=-1)

    def external(t):
        if not use_external or ext_amp == 0:
            return np.zeros_like(X)
        if ext_type == "Plane wave":
            th = np.deg2rad(ext_angle_deg)
            return ext_amp * np.cos(k*(X*np.cos(th) + Y*np.sin(th)) - (2*np.pi*frequency)*t)
        else:
            dx = X - ext_px
            dy = Y - ext_py
            R = np.hypot(dx, dy)
            return ext_amp * np.cos(k*R - (2*np.pi*frequency)*t) * _atten(R)

    # precompute ring node coords
    ring_nodes_list = []
    for (R, A) in rings:
        SX, SY = ring_nodes(R, nodes_per_ring)
        ring_nodes_list.append((SX, SY, A))

    T = 1.0 / frequency
    times = np.linspace(0, T, samples, endpoint=False)

    I_baseline = np.zeros_like(X, dtype=float)
    I_total_accum = np.zeros_like(X, dtype=float)

    for t in times:
        Ft = np.zeros_like(X, dtype=float)

        # Baseline: add each ring's intensity alone
        for (SX, SY, A) in ring_nodes_list:
            Fr = ring_field(SX, SY, A, t)
            I_baseline += (Fr**2) / samples
            Ft += Fr  # build total

        # Baseline: add external alone
        Fext = external(t)
        I_baseline += (Fext**2) / samples

        # Total: rings + external together
        Ft += Fext
        I_total_accum += (Ft**2) / samples

    I_total = I_total_accum
    eps = 1e-12
    gain_pct = 100.0 * (I_total - I_baseline) / (I_baseline + eps)
    return gain_pct

with st.spinner("Computing average vs baseline..."):
    gain_pct = compute_gain_map(
        xs, ys, rings, nodes_per_ring, frequency, samples,
        use_external, ext_type, ext_amp, ext_angle_deg, ext_px, ext_py,
        attenuation_mode, decay_len
    )

# -----------------------------
# Plot: Interference Gain (%)
# -----------------------------
st.subheader("Average vs Baseline — Interference Gain (%)")
fig, ax = plt.subplots(figsize=(7.5, 7.0))
v = float(gain_pct_limit)
im = ax.imshow(gain_pct, extent=[xs.min(), xs.max(), ys.min(), ys.max()],
               origin='lower', cmap='coolwarm', vmin=-v, vmax=v, interpolation='bilinear')

def draw_guides():
    if not show_guides:
        return
    th = np.linspace(0, 2*np.pi, 360)
    for (R, _) in rings:
        ax.plot(cx + R*np.cos(th), cy + R*np.sin(th), color='k', lw=0.8, alpha=0.5)

def draw_nodes():
    if not show_nodes:
        return
    for (R, _) in rings:
        SX, SY = ring_nodes(R, nodes_per_ring)
        ax.scatter(SX, SY, s=40, edgecolor='k', facecolor='yellow', zorder=3)

draw_guides()
draw_nodes()

ax.set_xlabel("x (ft)"); ax.set_ylabel("y (ft)")
cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="Gain (%)")
st.pyplot(fig, clear_figure=True)

st.caption(
    "Baseline = sum of time-averaged intensities of each ring alone + external alone (no cross-terms). "
    "Total = time-averaged intensity with all emitters on together. "
    "Gain(%) = (Total − Baseline)/Baseline × 100. Red = above baseline (constructive), Blue = below (destructive)."
)

# Optional instantaneous preview (like your example)
with st.expander("Instantaneous Field (preview)"):
    t_slider = st.slider("Time within one period", 0.0, 1.0, 0.0, 0.01)
    t = t_slider / max(frequency, 1e-6)

    # Build instantaneous field (rings + external)
    F_now = np.zeros_like(X)
    for (R, A) in rings:
        SX, SY = ring_nodes(R, nodes_per_ring)
        dxs = X[..., None] - SX[None, None, :]
        dys = Y[..., None] - SY[None, None, :]
        RR = np.hypot(dxs, dys)
        F_now += np.sum(A * np.cos(k*RR - (2*np.pi*frequency)*t) * attenuation(RR), axis=-1)

    # external
    if use_external and ext_amp != 0:
        if ext_type == "Plane wave":
            th = np.deg2rad(ext_angle_deg)
            F_now += ext_amp * np.cos(k*(X*np.cos(th) + Y*np.sin(th)) - (2*np.pi*frequency)*t)
        else:
            dx = X - ext_px
            dy = Y - ext_py
            Rpt = np.hypot(dx, dy)
            F_now += ext_amp * np.cos(k*Rpt - (2*np.pi*frequency)*t) * attenuation(Rpt)

    fig2, ax2 = plt.subplots(figsize=(7, 6))
    im2 = ax2.imshow(F_now, extent=[xs.min(), xs.max(), ys.min(), ys.max()],
                     origin='lower', cmap='RdBu', interpolation='bilinear')
    draw_guides(); draw_nodes()
    ax2.set_xlabel("x (ft)"); ax2.set_ylabel("y (ft)")
    st.pyplot(fig2, clear_figure=True)
