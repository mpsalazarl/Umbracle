# Quick Reference: Grasshopper Setup

## File to Use
📄 **[Umbracle.Volume.py](Umbracle.Volume.py)** — Ready to copy into Grasshopper Python component

## Grasshopper Component Setup

### Step 1: Create Python Component
- Add "Python Script" component to canvas
- Copy entire contents of `Umbracle.Volume.py` into the component editor

### Step 2: Create Input Sliders (in order)

| Input Name | Type | Range | Default | Notes |
|---|---|---|---|---|
| `input_curves` | Curve (x3) | — | — | 3 closed curves in XY plane |
| `divisions` | Integer Slider | 5 to 50 | 10 | Points per curve |
| `start_param_offset` | Number Slider | 0.0 to 1.0 | 0.0 | Start position on curve |
| `min_distance` | Number Slider | 0.5 to 5.0 | 1.0 | Min spacing (meters) |
| `max_distance` | Number Slider | 2.0 to 20.0 | 5.0 | Max spacing (meters) |
| `min_height` | Number Slider | 1.0 to 50.0 | 5.0 | Min vertical height (m) |
| `max_height` | Number Slider | 2.0 to 100.0 | 20.0 | Max vertical height (m) |
| `voronoi_offset` | Number Slider | 0.1 to 5.0 | 0.5 | Voronoi edge inset (m) |
| `extrusion_distance` | Number Slider | 0.5 to 20.0 | 5.0 | Wall extrusion depth (m) |

### Step 3: Connect Outputs

**Primary Outputs** (most important):
- `lofted_surface` → Surface viewer (main geometry)
- `vertical_lines` → Line viewer (structure)
- `wall_surfaces` → Surface viewer (cellular structure)
- `multipipe_geometry` → Brep viewer (final form)

**Debug Outputs**:
- `error_messages` → Text panel (see execution log)
- `division_points` → Point viewer (verify curve sampling)

---

## Key Features

| Feature | Input | Range | Effect |
|---------|-------|-------|--------|
| **Proximity-Based Spacing** | min/max_distance | 0.5–20m | Closer curves → larger gaps |
| **Height Offset** | min/max_height | 1–100m | Each curve +1m automatically |
| **Voronoi Cells** | voronoi_offset | 0.1–5m | Smaller = tighter cells |
| **Wall Extrusion** | extrusion_distance | 0.5–20m | Deeper = more volume |
| **Pipe Diameter** | Fixed @ 0.3m | — | Edit code if needed |

---

## Expected Output

✅ **Full 10-Phase Pipeline**:

1. Division points on curves (non-uniform, proximity-aware)
2. Vertical lines from each point (height varies by curve: +0/+1/+2m)
3. Lofted surface through line endpoints
4. Extended surface reaching XY plane
5. Voronoi tessellation creating cellular regions
6. Offset Voronoi edges (inward)
7. Wall surfaces enclosing cells
8. Extruded walls creating volume
9. Connections from 2/3-height points to Voronoi vertices
10. Multipipe geometry along connections

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No output | Check error_messages panel; ensure 3 curves provided |
| Geometry seems uniform | Adjust min_distance < max_distance |
| No cells visible | Increase divisions (20+) and voronoi_offset |
| Script too slow | Reduce divisions or sampling density (edit SURFACE_SAMPLE_DENSITY in code) |
| Surface doesn't extend to Z=0 | Check that curves are in XY plane (Z ≈ 0) |

---

## Height Domain Offset Verification

To verify the 1m offset per curve is working:
1. Set `min_height = 5` and `max_height = 10`
2. Look at vertical_lines output
3. Lines from Curve A: Z ∈ [5, 10]m
4. Lines from Curve B: Z ∈ [6, 11]m ✓ (+1m)
5. Lines from Curve C: Z ∈ [7, 12]m ✓ (+2m)

---

## Tips for Best Results

✓ Use smooth, non-intersecting curves  
✓ Start with mid-range slider values  
✓ Divisions = 15–25 is good balance  
✓ Keep distance ratio (max/min) moderate (≤ 5x)  
✓ Test incrementally — adjust one slider at a time  

---

**Ready to integrate!** 🚀
