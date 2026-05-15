import Rhino.Geometry as rg
import math
import random

# Initialize output lists
main_circles = []
secondary_circles = []
boundary_curves = []
individual_surfaces = []

# --- INPUTS ---
# tri_side, main_rad, count_A, count_B, count_C, r (list), fillet_val, seed (int)

# 1. Fixing Randomness (Seed)
# This ensures that when you change tri_side, the SHAPES stay the same
random.seed(seed)

# 2. Setup Centers (Equilateral Triangle)
h = (math.sqrt(3) / 3) * tri_side
centers = [
    rg.Point3d(0, h, 0),
    rg.Point3d(-tri_side / 2, -h / 2, 0),
    rg.Point3d(tri_side / 2, -h / 2, 0)
]
counts = [int(count_A), int(count_B), int(count_C)]

# Extract radius domain from list 'r'
# Extract radius domain safely
if isinstance(r, list):
    r_min, r_max = min(r), max(r)
else:
    # If r is just a single number, min and max are the same
    r_min = r_max = r

for i in range(3):
    center = centers[i]
    
    # Output 1: Main Base Circles
    main_c = rg.Circle(center, main_rad).ToNurbsCurve()
    main_circles.append(main_c)
    
    # 3. Create Secondary Circles
    group_circles = []
    for _ in range(counts[i]):
        # Random distribution relative to (0,0,0) first to keep shape static
        r_dist = main_rad * math.sqrt(random.random())
        theta = random.random() * 2 * math.pi
        
        # Local point (before moving to triangle center)
        local_pt = rg.Point3d(r_dist * math.cos(theta), r_dist * math.sin(theta), 0)
        
        # Random radius for this specific circle
        sec_r = random.uniform(r_min, r_max)
        sec_c = rg.Circle(local_pt, sec_r).ToNurbsCurve()
        group_circles.append(sec_c)

    # 4. Create Boundary (Union) at Origin for Stability
    # We union them at 0,0,0 so the calculation is more precise
    union_results = rg.Curve.CreateBooleanUnion(group_circles, 0.001)
    
    if union_results:
        # Get the outermost boundary
        crv = union_results[0]
        
        # Smooth the joins
        smoothed = rg.Curve.CreateFilletCornersCurve(crv, fillet_val, 0.001, 0.001)
        final_crv = smoothed if smoothed else crv
        
        # --- ALIGNMENT LOGIC ---
        # Move the static organic shape to the triangle vertex center
        # This keeps the shape identical even when tri_side changes
        final_crv.Translate(rg.Vector3d(center))
        boundary_curves.append(final_crv)
        
        # Move the secondary circles to the center for visualization
        for c_sec in group_circles:
            c_sec.Translate(rg.Vector3d(center))
            secondary_circles.append(c_sec)
            
        # Output 4: Individual Surfaces
        srf = rg.Brep.CreatePlanarBreps(final_crv, 0.001)
        if srf:
            individual_surfaces.append(srf[0])

# Assign to Grasshopper Outputs
a = main_circles
b = secondary_circles
c = boundary_curves
d = individual_surfaces