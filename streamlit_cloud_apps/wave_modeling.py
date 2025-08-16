import numpy as np
import streamlit as st
import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Quantum/Scalar Field Model — Gauss Units", layout="wide")

# -----------------------------
# Constants
# -----------------------------
MU0 = 4*np.pi*1e-7  # H/m
G2T = 1e-4          # 1 Gauss = 1e-4 Tesla

# -----------------------------
# Sidebar: Field & Grid
# -----------------------------
st.sidebar.header("Field & Grid")
half_extent = st.sidebar.number_input("Half-extent (ft) → domain [-E, +E]", 50.0, 2000.0, 250.0, step=50.0)
resolution  = st.sidebar.number_input("Grid resolution (ft)", 0.5, 10.0, 2.0, step=0.5)
wavelength  = st.sidebar.number_input("Model wavelength (ft)", 0.5, 2000.0, 28.0, step=0.5)
frequency   = st.sidebar.number_input("Model frequency (Hz)", 0.01, 5.0, 0.15, step=0.01)
samples     = st.sidebar.slider("Time samples per period", 8, 96, 24, step=4)

# Reference levels (Gauss)
st.sidebar.header("Reference Levels (Gauss)")
B_earth   = st.sidebar.number_input("Earth field", 0.0, 100.0, 0.5, step=0.1)
B_cancel  = st.sidebar.number_input("Magnet cancel level", 0.0, 100000.0, 3000.0, step=100.0)
B_quantum = st.sidebar.number_input("Quantum-wave stress test", 0.0, 500000.0, 50000.0, step=1000.0)

# Contours on B_rms,total
st.sidebar.header("Contours (on Bᵣₘₛ,total)")
draw_contours = st.sidebar.checkbox("Show Bᵣₘₛ,total contours", value=True)
contour_levels = st.sidebar.multiselect(
    "Contour levels (Gauss)",
    options=[B_earth, B_cancel, B_quantum, 1.0, 10.0, 100.0, 1000.0],
    default=[B_earth, B_cancel]
)

# -----------------------------
# Rings & Nodes (Gauss)
# -----------------------------
st.sidebar.header("Rings & Nodes (amplitudes in Gauss)")
n_rings = st.sidebar.slider("Number of rings", 1, 12, 4)
node_mode = st.sidebar.radio("Nodes per ring", ["4 (0/90/180/270°)", "6 (every 60°)"], index=0)
nodes_per_ring = 4 if node_mode.startswith("4") else 6

default_radii = [1.0, 75.0, 100.0, 125.0][:n_rings] or [40.0]
default_amps  = [100.0]*n_rings  # Gauss

st.sidebar.caption("Edit ring radii (ft) and amplitudes (Gauss). Phase=0 for all nodes on a ring.")
ring_df = pd.DataFrame({
    "radius_ft": default_radii + [np.nan]*(n_rings - len(default_radii)),
    "amplitude_G": default_amps + [np.nan]*(n_rings - len(default_amps)),
}).iloc[:n_rings]
ring_table = st.sidebar.data_editor(ring_df, use_container_width=True, num_rows="fixed", key="ring_table")

# Tie ring amplitudes to cancel strength
st.sidebar.subheader("Scalar coupling for rings")
use_scalar_rings = st.sidebar.checkbox("Tie ring amplitudes to B_cancel (Gauss)", value=False)
scalar_gain_rings = st.sidebar.number_input("Ring scalar proportionality (×B_cancel)", 0.0, 1000.0, 0.05, step=0.01)

# -----------------------------
# Propagation / Loss
# -----------------------------
st.sidebar.header("Propagation / Loss")
attenuation_mode = st.sidebar.selectbox("Attenuation", ["none", "spherical (1/r)", "cylindrical (1/√r)"], index=2)
use_decay = st.sidebar.checkbox("Exponential decay envelope", value=False)
decay_len = st.sidebar.number_input("Decay length (ft)", 1.0, 10000.0, 120.0, step=10.0) if use_decay else None

# -----------------------------
# External Wave (Gauss)
# -----------------------------
st.sidebar.header("External Wave (Gauss)")
use_external = st.sidebar.checkbox("Include external wave", value=True)
ext_type = st.sidebar.selectbox("External type", ["Plane wave", "Point source"], index=0, disabled=not use_external)

# Couple external to cancel strength
use_scalar_external = st.sidebar.checkbox("Tie external amplitude to B_cancel", value=True, disabled=not use_external)
if use_external:
    if use_scalar_external:
        scalar_gain_ext = st.sidebar.number_input("External scalar proportionality (×B_cancel)", 0.0, 1000.0, 0.02, step=0.01)
        ext_amp_G = scalar_gain_ext * B_cancel
    else:
        ext_amp_G = st.sidebar.number_input("External amplitude (Gauss)", 0.0, 1e6, 80.0, step=5.0)
else:
    ext_amp_G = 0.0

ext_angle_deg = 0.0
ext_px = ext_py = 0.0
if use_external:
    if ext_type == "Plane wave":
        ext_angle_deg = st.sidebar.slider("Plane wave angle (deg, 0°→+x, 90°→+y)", 0, 359, 45)
    else:
        ext_px = st.sidebar.number_input(
            "Point source X (ft)",
            float(-half_extent), float(half_extent), float(-half_extent), step=10.0
        )
        ext_py = st.sidebar.number_input(
            "Point source Y (ft)",
            float(-half_extent), float(half_extent), float(0.0), step=10.0
        )

# -----------------------------
# Rate of Loss / Superposition
# -----------------------------
st.sidebar.header("Rate of Loss / Superposition extent")
K_loss = st.sidebar.number_input("K (constant)", 0.0, 1e3, 12.0, step=0.5)
emit_size_ft = st.sidebar.number_input("Emission size D_emit (ft)", 0.0, 1e6, 0.167, step=0.001)

driver_choice = st.sidebar.selectbox(
    "Driver field B_driver (Gauss)",
    ["Use B_cancel", "Use external amplitude", "Custom (enter below)"],
    index=0
)

if driver_choice == "Use B_cancel":
    B_driver = B_cancel
elif driver_choice == "Use external amplitude":
    B_driver = ext_amp_G if use_external else 0.0
else:
    B_driver = st.sidebar.number_input("Custom B_driver (Gauss)", 0.0, 1e9, 3000.0, step=100.0)

L_sup_ft = emit_size_ft * (B_driver / max(B_earth, 1e-12)) * K_loss
L_sup_mi = L_sup_ft / 5280.0

st.sidebar.markdown(
    f"**Predicted superposition extent:** {L_sup_ft:,.2f} ft  "
    f"(**{L_sup_mi:,.3f} miles**)"
)

# Option: use L_sup_ft as decay length
use_sup_as_decay = st.sidebar.checkbox("Use L_sup as decay length (exp envelope)", value=True)
if use_sup_as_decay:
    use_decay = True
    decay_len = max(L_sup_ft, 1e-9)

# -----------------------------
# Gain Map Display
# -----------------------------
st.sidebar.header("Gain Map Display")
gain_pct_limit = st.sidebar.slider("Gain colormap ±% range", 1, 100, 10, step=1)

# -----------------------------
# House Overlay
# -----------------------------
st.sidebar.header("House Overlay")
show_house = st.sidebar.checkbox("Show house footprint", value=True)
house_len = st.sidebar.number_input("House length (ft)", 1.0, 500.0, 60.0, step=1.0)
house_wid = st.sidebar.number_input("House width (ft)", 1.0, 500.0, 40.0, step=1.0)
house_rot = st.sidebar.slider("House rotation (deg)", -180, 180, 0)
house_cx = st.sidebar.number_input(
    "House center X (ft)",
    float(-half_extent), float(half_extent), float(0.0), step=5.0
)
house_cy = st.sidebar.number_input(
    "House center Y (ft)",
    float(-half_extent), float(half_extent), float(0.0), step=5.0
)

# -----------------------------
# Grid & constants
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
    aG = float(ring_table.loc[i, "amplitude_G"])
    if use_scalar_rings:
        aG = scalar_gain_rings * B_cancel
    rings.append((r, aG))

# -----------------------------
# Helpers
# -----------------------------
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
    if use_decay and decay_len is not None:
        att = att * np.exp(-R / decay_len)
    return att

def rotated_rect(cxr, cyr, L, W, theta_deg):
    th = np.deg2rad(theta_deg)
    halfL, halfW = L/2.0, W/2.0
    corners = np.array([
        [-halfL, -halfW],
        [ halfL, -halfW],
        [ halfL,  halfW],
        [-halfL,  halfW],
        [-halfL, -halfW],
    ])
    Rm = np.array([[np.cos(th), -np.sin(th)],
                   [np.sin(th),  np.cos(th)]])
    rot = corners @ Rm.T
    return rot[:,0] + cxr, rot[:,1] + cyr

# -----------------------------
# Core computation
# -----------------------------
@st.cache_data(show_spinner=False)
def compute_maps(xs, ys, rings, nodes_per_ring, wavelength, frequency, samples,
                 use_external, ext_type, ext_amp_G, ext_angle_deg, ext_px, ext_py):
    X, Y = np.meshgrid(xs, ys)
    k = 2*np.pi / wavelength
    omega = 2*np.pi * frequency

    def ring_field(SX, SY, A_G, t):
        dxs = X[..., None] - SX[None, None, :]
        dys = Y[..., None] - SY[None, None, :]
        R = np.hypot(dxs, dys)
        return np.sum(A_G * np.cos(k*R - omega*t) * attenuation(R), axis=-1)

    def external(t):
        if not use_external or ext_amp_G == 0:
            return np.zeros_like(X)
        if ext_type == "Plane wave":
            th = np.deg2rad(ext_angle_deg)
            return ext_amp_G * np.cos(k*(X*np.cos(th) + Y*np.sin(th)) - omega*t)
        else:
            dx = X - ext_px
            dy = Y - ext_py
            R = np.hypot(dx, dy)
            return ext_amp_G * np.cos(k*R - omega*t) * attenuation(R)

    ring_nodes_list = []
    for (R, A_G) in rings:
        SX, SY = ring_nodes(R, nodes_per_ring)
        ring_nodes_list.append((SX, SY, A_G))

    T = 1.0 / max(frequency, 1e-9)
    times = np.linspace(0, T, samples, endpoint=False)

    I_baseline = np.zeros_like(X, dtype=float)
    I_total_acc = np.zeros_like(X, dtype=float)

    for t in times:
        Ft = np.zeros_like(X, dtype=float)
        for (SX, SY, A_G) in ring_nodes_list:
            Fr = ring_field(SX, SY, A_G, t)
            I_baseline += (Fr**2) / samples
            Ft += Fr

        Fext = external(t)
        I_baseline += (Fext**2) / samples

        Ft += Fext
        I_total_acc += (Ft**2) / samples

    I_total = I_total_acc
    B_rms_total = np.sqrt(np.maximum(I_total, 0.0))
    B_rms_base  = np.sqrt(np.maximum(I_baseline, 0.0))

    gain_pct = 100.0 * (I_total - I_baseline) / (I_baseline + 1e-12)
    u_total = (B_rms_total*G2T)**2 / (2*MU0)
    u_base  = (B_rms_base*G2T)**2  / (2*MU0)

    return gain_pct, B_rms_total, B_rms_base, u_total, u_base

with st.spinner("Computing..."):
    gain_pct, B_rms_total, B_rms_base, u_total, u_base = compute_maps(
        xs, ys, rings, nodes_per_ring, wavelength, frequency, samples,
        use_external, ext_type, ext_amp_G, ext_angle_deg, ext_px, ext_py
    )

# -----------------------------
# Plotly: Gain% with overlays
# -----------------------------
custom = np.stack([B_rms_total, B_rms_base, u_total, u_base], axis=-1)
fig = go.Figure(
    data=go.Heatmap(
        x=xs, y=ys, z=gain_pct,
        colorscale="RdBu", zmin=-gain_pct_limit, zmax=gain_pct_limit,
        colorbar=dict(title="Gain (%)"),
        customdata=custom,
        hovertemplate=(
            "x: %{x:.1f} ft<br>"
            "y: %{y:.1f} ft<br>"
            "gain: %{z:.2f}%<br>"
            "Bᵣₘₛ,total: %{customdata[0]:.3g} G<br>"
            "Bᵣₘₛ,base: %{customdata[1]:.3g} G<br>"
            "u_total: %{customdata[2]:.3g} J/m³<br>"
            "u_base: %{customdata[3]:.3g} J/m³"
            "<extra></extra>"
        ),
    )
)
fig.update_yaxes(scaleanchor="x", scaleratio=1)
fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), xaxis_title="x (ft)", yaxis_title="y (ft)")

# ----- Initial viewing window & quick view buttons -----
# Start focused on a 500x500 ft box around the house center, but allow zooming out.
initial_half_window = 250.0  # 250 ft each side -> 500 x 500 ft window

# Set initial axis ranges centered on the house
fig.update_xaxes(range=[house_cx - initial_half_window, house_cx + initial_half_window])
fig.update_yaxes(range=[house_cy - initial_half_window, house_cy + initial_half_window])

# Also add quick buttons to jump between "House 500 ft" and "Full extent"
fig.update_layout(
    updatemenus=[
        dict(
            type="buttons",
            direction="right",
            x=0.5, xanchor="center",
            y=1.05, yanchor="bottom",
            buttons=[
                dict(
                    label="House 500 ft view",
                    method="relayout",
                    args=[{
                        "xaxis.range": [house_cx - initial_half_window, house_cx + initial_half_window],
                        "yaxis.range": [house_cy - initial_half_window, house_cy + initial_half_window],
                    }],
                ),
                dict(
                    label="Full extent",
                    method="relayout",
                    args=[{
                        "xaxis.range": [float(xs.min()), float(xs.max())],
                        "yaxis.range": [float(ys.min()), float(ys.max())],
                    }],
                ),
            ],
        )
    ]
)

# Ring guides
for (R, _) in rings:
    fig.add_shape(type="circle", xref="x", yref="y", x0=-R, y0=-R, x1=R, y1=R,
                  line=dict(color="black", width=1))

# Predicted superposition extent
if L_sup_ft > 0:
    fig.add_shape(
        type="circle", xref="x", yref="y",
        x0=-L_sup_ft, y0=-L_sup_ft, x1=L_sup_ft, y1=L_sup_ft,
        line=dict(color="purple", width=2, dash="dash")
    )
    fig.add_trace(go.Scatter(
        x=[L_sup_ft], y=[0],
        mode="markers+text",
        marker=dict(size=1, color="purple"),
        text=[f"L_sup ≈ {L_sup_mi:.3f} mi"],
        textposition="top center",
        showlegend=False,
        hoverinfo="skip"
    ))

# House overlay
if show_house:
    hx, hy = rotated_rect(house_cx, house_cy, house_len, house_wid, house_rot)
    fig.add_trace(go.Scatter(
        x=hx, y=hy, mode="lines",
        fill="toself",
        line=dict(color="black", width=2),
        fillcolor="rgba(255, 255, 0, 0.25)",
        name="house", hovertemplate="House footprint<extra></extra>"
    ))

# Contours
if draw_contours and len(contour_levels) > 0:
    fig.add_trace(go.Contour(
        x=xs, y=ys, z=B_rms_total,
        contours=dict(
            start=min(contour_levels),
            end=max(contour_levels),
            size=max(1e-9, (max(contour_levels)-min(contour_levels))/max(1,len(contour_levels)-1)),
            coloring="none", showlabels=True, labelfont=dict(color="black")
        ),
        line=dict(color="black", width=1),
        showscale=False, hoverinfo="skip"
    ))

st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True})

st.caption(
    "All amplitudes in Gauss. Purple dashed circle = predicted superposition extent "
    "L_sup = D_emit × (B_driver / B_earth) × K. Optionally used as decay length."
)

# -----------------------------
# Instantaneous preview
# -----------------------------
with st.expander("Instantaneous Field (Gauss)"):
    t_slider = st.slider("Time within one period", 0.0, 1.0, 0.0, 0.01)
    t = t_slider / max(frequency, 1e-6)

    def inst_field():
        F = np.zeros_like(X)
        for (R, A_G) in rings:
            SX, SY = ring_nodes(R, nodes_per_ring)
            dxs = X[..., None] - SX[None, None, :]   # (Ny, Nx, Nnodes)
            dys = Y[..., None] - SY[None, None, :]   # (Ny, Nx, Nnodes)
            RR  = np.hypot(dxs, dys)
            F  += np.sum(A_G * np.cos(k*RR - omega*t) * attenuation(RR), axis=-1)

        if use_external and ext_amp_G != 0:
            if ext_type == "Plane wave":
                thp = np.deg2rad(ext_angle_deg)
                F += ext_amp_G * np.cos(k*(X*np.cos(thp) + Y*np.sin(thp)) - omega*t)
            else:
                dx = X - ext_px
                dy = Y - ext_py
                Rpt = np.hypot(dx, dy)
                F += ext_amp_G * np.cos(k*Rpt - omega*t) * attenuation(Rpt)
        return F

    F_now = inst_field()
    fig2, ax2 = plt.subplots(figsize=(7, 6))
    im2 = ax2.imshow(F_now, extent=[xs.min(), xs.max(), ys.min(), ys.max()],
                     origin='lower', cmap='RdBu', interpolation='bilinear')
    ax2.set_xlabel("x (ft)"); ax2.set_ylabel("y (ft)")
    st.pyplot(fig2, clear_figure=True)
