import Rhino.Geometry as rg
import Grasshopper as gh
import Grasshopper.Kernel.Data as ghd
import random

def get_smooth_division(target_crv, other_curves, num_pts, d_min, d_max):
    # Parámetros de muestreo
    samples = 150
    params = target_crv.DivideByCount(samples, True)
    dist_map = []
    
    for t in params:
        pt = target_crv.PointAt(t)
        min_d = float("inf")
        for other in other_curves:
            if other == target_crv: continue
            _, t_close = other.ClosestPoint(pt)
            dist = pt.DistanceTo(other.PointAt(t_close))
            if dist < min_d: min_d = dist
        dist_map.append(min_d)
    
    d_low, d_high = min(dist_map), max(dist_map)
    range_d = (d_high - d_low) if d_high > d_low else 1.0
    
    cumulative_lengths, current_length = [0.0], 0.0
    for i in range(len(params) - 1):
        t_map = (dist_map[i] - d_low) / range_d
        density = 1.0 / (d_min + (d_max - d_min) * t_map)
        current_length += (params[i+1] - params[i]) * density
        cumulative_lengths.append(current_length)
    
    final_pts = []
    for j in range(num_pts):
        target_val = (j / float(num_pts - 1)) * cumulative_lengths[-1]
        for k in range(len(cumulative_lengths) - 1):
            if cumulative_lengths[k] <= target_val <= cumulative_lengths[k+1]:
                t = params[k] + (target_val - cumulative_lengths[k]) / (cumulative_lengths[k+1] - cumulative_lengths[k]) * (params[k+1] - params[k])
                final_pts.append(target_crv.PointAt(t))
                break
    return final_pts

# --- PROCESAMIENTO PRINCIPAL ---
p_base_tree = gh.DataTree[rg.Point3d]()
p_top_tree = gh.DataTree[rg.Point3d]()
l_tree = gh.DataTree[rg.Line]()
arc_tree = gh.DataTree[rg.ArcCurve]()
all_top_geo = []
temp_offsets = []
used_ids = set()
all_groups = []

# 1. Generación de Puntos Elevados y Líneas Base
for i in range(len(Curves)):
    path = ghd.GH_Path(i)
    # Offsets para el contorno final
    off_res = Curves[i].Offset(rg.Plane.WorldXY, offset_dist, 0.01, rg.CurveOffsetCornerStyle.Round)
    if off_res: temp_offsets.extend(off_res)
    
    pts = get_smooth_division(Curves[i], Curves, count, d_min, d_max)
    group = []
    for j, p in enumerate(pts):
        h = random.uniform(h_start + i, h_end + i)
        p_top = p + rg.Vector3d(0, 0, h)
        p_base_tree.Add(p, path)
        p_top_tree.Add(p_top, path)
        l_tree.Add(rg.Line(p, p_top), path)
        all_top_geo.append(rg.Point(p_top))
        group.append({'pt': p_top, 'id': (i, j)})
    all_groups.append(group)

# 2. Conexiones en Arco (Emparejamiento 1 a 1 entre ramas)
for i in range(len(all_groups)):
    num_to_process = int(len(all_groups[i]) * ratio)
    count_proc = 0
    for item in all_groups[i]:
        if count_proc >= num_to_process: break
        if item['id'] in used_ids: continue
        
        pt_a, best_partner, min_d = item['pt'], None, float("inf")
        for other_idx in range(len(all_groups)):
            if i == other_idx: continue
            for other_item in all_groups[other_idx]:
                if other_item['id'] not in used_ids:
                    d = pt_a.DistanceTo(other_item['pt'])
                    if d < min_d:
                        min_d = d
                        best_partner = other_item
        
        if best_partner:
            used_ids.add(item['id'])
            used_ids.add(best_partner['id'])
            mid = (pt_a + best_partner['pt']) / 2.0
            mid.Z += pt_a.DistanceTo(best_partner['pt']) * 0.25
            arc_tree.Add(rg.ArcCurve(rg.Arc(pt_a, mid, best_partner['pt'])), ghd.GH_Path(i))
            count_proc += 1

# 3. Creación de Superficie de Cobertura y Recorte
raw_surf = rg.Brep.CreatePatch(all_top_geo, 20, 20, 0.01)
f_surf = None
ext_brep = None
comb_bound = None

if raw_surf:
    # Asegurar objeto individual desde Patch
    srf_base = raw_surf[0] if hasattr(raw_surf, "__len__") else raw_surf
    face = srf_base.Faces[0]
    # Extensión para asegurar que el recorte sea limpio
    ext = face.Extend(rg.IsoStatus.North, offset_dist, True).Extend(rg.IsoStatus.South, offset_dist, True).Extend(rg.IsoStatus.East, offset_dist, True).Extend(rg.IsoStatus.West, offset_dist, True)
    ext_brep = ext.ToBrep()
    
    # Unión de offsets para el contorno de recorte
    comb_bound = rg.Curve.CreateBooleanUnion(temp_offsets, 0.01)
    if comb_bound:
        projs = []
        for crv in comb_bound:
            res = rg.Curve.ProjectToBrep(crv, ext_brep, rg.Vector3d(0,0,1), 0.01)
            if res: projs.extend(res)
        
        if projs:
            splits = ext_brep.Split(projs, 0.01)
            if splits:
                # Seleccionar la pieza central por proximidad al centroide del contorno
                mid_pt = rg.AreaMassProperties.Compute(comb_bound[0]).Centroid
                f_surf = sorted(splits, key=lambda s: rg.AreaMassProperties.Compute(s).Centroid.DistanceTo(mid_pt))[0]

# Salidas
a = p_base_tree
b = l_tree
c = p_top_tree
d = arc_tree
e = ext_brep
f = f_surf
g = comb_bound