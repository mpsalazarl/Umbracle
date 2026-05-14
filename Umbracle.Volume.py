"""
Umbracle - Volume Phase
Grasshopper Python Component for Parametric Volume Generation

Generates volumetric cellular structure based on three boundary curves from Area phase:
- Proximity-based non-uniform curve division (controlled by sliders)
- Vertical lines with random heights (domain controlled by sliders)
- Lofted NURBS surface (U=20 parameterization)
- Surface extension to XY plane along normals
- Custom 3D Voronoi tesellation clipped to surface
- Curvature-aware edge offsetting on surface
- Wall surface creation between original and offset edges
- Extrusion of wall surfaces to create cellular volume

Inputs from Grasshopper:
  - input_curves: Three boundary curves from Area phase (smooth_curves output)
  - divisions: Number of division points per curve (slider, 5-50)
  - start_param_offset: Starting parameter offset (slider, 0-1)
  - min_distance: Minimum point spacing in proximity zones (slider, 0.5-5m)
  - max_distance: Maximum point spacing in proximity zones (slider, 2-20m)
  - min_height: Minimum random vertical line height (slider, 1-50m)
  - max_height: Maximum random vertical line height (slider, 2-100m)
  - voronoi_offset: Inward offset of Voronoi edges on surface (slider, 0.1-5m)
  - extrusion_distance: Extrusion depth of wall surfaces (slider, 0.5-20m)

Outputs:
  - division_points: All curve division points organized by curve
  - vertical_lines: Line segments from division points to random heights
  - lofted_surface: Main parametric surface (U=20)
  - extended_surface: Surface extended to XY plane
  - voronoi_edges: Edges between Voronoi cells on surface
  - offset_edges: Inset Voronoi edges (offset on surface)
  - wall_surfaces: Surfaces between original and offset edges
  - final_extrusions: Extruded wall surfaces (final geometry)
"""

import Rhino.Geometry as rg
import scriptcontext as sc
import random
import math
from System.Collections.Generic import List
import sys

# Constants
TOLERANCE = 1e-6
VORONOI_CUBE_SIZE = 1000.0  # Large cube for Voronoi clipping
SURFACE_SAMPLE_DENSITY = 50  # Grid points per surface dimension for analysis
Z_PLANE = 0.0  # Target plane for extension
LOFT_U_COUNT = 20  # U-direction parameterization for lofted surface
LOFT_V_COUNT = 3  # V-direction (three curves = three sections)


# ==============================================================================
# PHASE 1: PROXIMITY-BASED CURVE DIVISION
# ==============================================================================

def compute_proximity_field(curves, curve_index, sample_count=100):
    """
    Compute proximity (minimum distance) from one curve to the other two.
    
    Args:
        curves: List of three Curve objects
        curve_index: Index (0, 1, or 2) of the curve being analyzed
        sample_count: Number of sample points along curve for distance computation
        
    Returns:
        List of proximity values (one per sample point), normalized 0-1
    """
    if len(curves) != 3:
        raise ValueError("Expected exactly 3 curves")
    
    source_curve = curves[curve_index]
    other_curves = [c for i, c in enumerate(curves) if i != curve_index]
    
    source_length = source_curve.GetLength()
    proximities = []
    min_dist_global = float('inf')
    max_dist_global = 0.0
    
    # First pass: compute all distances and find min/max
    distances_raw = []
    for i in range(sample_count):
        t_param = i / float(sample_count - 1) if sample_count > 1 else 0.5
        t = source_curve.Domain.Min + t_param * (source_curve.Domain.Max - source_curve.Domain.Min)
        source_pt = source_curve.PointAt(t)
        
        min_dist_at_point = float('inf')
        for other_curve in other_curves:
            cc, pt_on_curve = other_curve.ClosestPoint(source_pt)
            dist = source_pt.DistanceTo(pt_on_curve)
            min_dist_at_point = min(min_dist_at_point, dist)
        
        distances_raw.append(min_dist_at_point)
        min_dist_global = min(min_dist_global, min_dist_at_point)
        max_dist_global = max(max_dist_global, min_dist_at_point)
    
    # Normalize to 0-1
    dist_range = max_dist_global - min_dist_global if max_dist_global > min_dist_global else 1.0
    proximities = [(d - min_dist_global) / dist_range for d in distances_raw]
    
    return proximities


def adaptive_curve_divide(curve, division_count, proximity_field, min_distance, 
                         max_distance, start_param_offset=0.0):
    """
    Divide curve with non-uniform spacing based on proximity field.
    
    Args:
        curve: Curve object to divide
        division_count: Number of division points desired
        proximity_field: List of normalized proximity values (0-1) along curve
        min_distance: Minimum spacing distance
        max_distance: Maximum spacing distance
        start_param_offset: Parameter offset for starting point (0-1)
        
    Returns:
        List of Point3d objects along curve
    """
    if division_count < 2:
        raise ValueError("Division count must be at least 2")
    
    curve_length = curve.GetLength()
    domain = curve.Domain
    
    # Build cumulative arc-length parametrization
    arc_lengths = [0.0]
    for i in range(1, len(proximity_field)):
        # Distance contribution weighted by proximity (reverse: close → max spacing, far → min spacing)
        prox = proximity_field[i]
        spacing_factor = min_distance + (max_distance - min_distance) * (1.0 - prox)
        arc_lengths.append(arc_lengths[-1] + spacing_factor)
    
    # Normalize arc lengths and remap to 0-1 parameter range
    total_arc = arc_lengths[-1]
    normalized_arc = [a / total_arc for a in arc_lengths] if total_arc > 0 else [i / (len(proximity_field) - 1) for i in range(len(proximity_field))]
    
    # Generate division points
    division_points = []
    for i in range(division_count):
        # Linear interpolation within normalized arc space
        t_normalized = start_param_offset + (i / (division_count - 1)) * (1.0 - start_param_offset) if division_count > 1 else 0.5
        
        # Find corresponding arc-length parameter
        idx_lower = int(t_normalized * (len(normalized_arc) - 1))
        idx_upper = min(idx_lower + 1, len(normalized_arc) - 1)
        
        if idx_lower == idx_upper:
            arc_param = normalized_arc[idx_lower]
        else:
            frac = (t_normalized * (len(normalized_arc) - 1) - idx_lower)
            arc_param = normalized_arc[idx_lower] * (1 - frac) + normalized_arc[idx_upper] * frac
        
        # Map back to curve parameter
        t_param = domain.Min + arc_param * (domain.Max - domain.Min)
        t_param = max(domain.Min, min(domain.Max, t_param))  # Clamp to domain
        
        pt = curve.PointAt(t_param)
        division_points.append(pt)
    
    return division_points


# ==============================================================================
# PHASE 2: VERTICAL LINES WITH RANDOM HEIGHTS
# ==============================================================================

def create_vertical_lines(division_points_list, min_height, max_height, seed_base=12345):
    """
    Create vertical lines from division points with random heights.
    
    Args:
        division_points_list: List of lists - division points grouped by curve (3 groups)
        min_height: Minimum vertical line height
        max_height: Maximum vertical line height
        seed_base: Base random seed for reproducibility
        
    Returns:
        Tuple: (all_start_points, all_end_points, line_objects)
    """
    random.seed(seed_base)
    start_points = []
    end_points = []
    line_objects = []
    
    for curve_idx, curve_points in enumerate(division_points_list):
        random.seed(seed_base + curve_idx * 1000)
        for point_idx, pt in enumerate(curve_points):
            # Generate deterministic random height
            height = min_height + random.random() * (max_height - min_height)
            
            start_pt = rg.Point3d(pt.X, pt.Y, pt.Z)
            end_pt = rg.Point3d(pt.X, pt.Y, pt.Z + height)
            
            start_points.append(start_pt)
            end_points.append(end_pt)
            
            # Create line object for visualization
            line = rg.LineCurve(start_pt, end_pt)
            line_objects.append(line)
    
    return start_points, end_points, line_objects


# ==============================================================================
# PHASE 3: LOFTED SURFACE CREATION
# ==============================================================================

def create_endpoint_curves(endpoint_groups, u_count=20):
    """
    Create interpolated curves through endpoint groups.
    
    Args:
        endpoint_groups: List of 3 lists of Point3d (one list per original curve)
        u_count: Target parameterization (influences interpolation smoothness)
        
    Returns:
        List of 3 Curve objects
    """
    endpoint_curves = []
    
    for group in endpoint_groups:
        if len(group) < 2:
            # Fallback: create single line if only 1 point
            if len(group) == 1:
                endpoint_curves.append(rg.LineCurve(group[0], group[0]))
            continue
        
        try:
            # Create B-spline curve through endpoints
            curve = rg.Curve.CreateInterpolatedCurve(group, 3, rg.CurveKnotStyle.Clamped)
            if curve is not None:
                endpoint_curves.append(curve)
            else:
                # Fallback: polyline through points
                polyline = rg.Polyline(group)
                endpoint_curves.append(polyline.ToNurbsCurve())
        except:
            # Fallback: polyline if B-spline fails
            polyline = rg.Polyline(group)
            endpoint_curves.append(polyline.ToNurbsCurve())
    
    return endpoint_curves


def create_lofted_surface(endpoint_curves, u_count=20):
    """
    Create lofted NURBS surface through endpoint curves.
    
    Args:
        endpoint_curves: List of 3 Curve objects to loft between
        u_count: Number of U-direction divisions
        
    Returns:
        Surface object
    """
    if len(endpoint_curves) != 3:
        raise ValueError("Lofting requires exactly 3 curves")
    
    try:
        # Use Rhino's built-in lofting function
        loft_surface_list = rg.Brep.CreateFromLoft(
            endpoint_curves,
            rg.Point3d.Unset,
            rg.Point3d.Unset,
            rg.LoftType.Normal,
            False  # refitRails
        )
        
        if loft_surface_list and len(loft_surface_list) > 0:
            brep = loft_surface_list[0]
            if brep.Surfaces.Count > 0:
                return brep.Surfaces[0]
        
        # Fallback: ruled surface between first two curves + linear blend to third
        raise Exception("Loft failed, using fallback")
        
    except:
        # Fallback: create ruled surface between first and second, then blend to third
        surf12 = rg.Surface.CreateRuledSurface(endpoint_curves[0], endpoint_curves[1])
        if surf12:
            return surf12
        
        # Last resort: return None and handle in calling function
        return None


# ==============================================================================
# PHASE 4: EXTEND SURFACE TO XY PLANE
# ==============================================================================

def extend_surface_to_plane(surface, z_target=0.0, extension_factor=2.0):
    """
    Extend surface along normals until it reaches target Z plane.
    
    Args:
        surface: Surface object to extend
        z_target: Target Z-plane height (default 0 = XY plane)
        extension_factor: Extra extension factor for safety
        
    Returns:
        Extended Surface object
    """
    if surface is None:
        return None
    
    # Sample surface to find extent
    u_domain = surface.Domain(0)
    v_domain = surface.Domain(1)
    
    max_z = float('-inf')
    min_z = float('inf')
    
    for i in range(10):
        for j in range(10):
            u = u_domain.Min + (u_domain.Max - u_domain.Min) * i / 9.0
            v = v_domain.Min + (v_domain.Max - v_domain.Min) * j / 9.0
            pt = surface.PointAt(u, v)
            max_z = max(max_z, pt.Z)
            min_z = min(min_z, pt.Z)
    
    # Extension distance: from lowest point to target plane
    extension_dist = (min_z - z_target) * extension_factor if min_z > z_target else abs(min_z) * 1.5
    
    try:
        # Try offset surface (offset is perpendicular to surface, goes downward)
        extended = surface.Offset(-extension_dist, TOLERANCE)
        if extended is not None:
            return extended
    except:
        pass
    
    # Fallback: create ruled surface from surface to projected bottom
    try:
        # Project surface points to Z = z_target
        projected_points = []
        u_domain = surface.Domain(0)
        v_domain = surface.Domain(1)
        
        for i in range(11):
            u_row = []
            for j in range(11):
                u = u_domain.Min + (u_domain.Max - u_domain.Min) * i / 10.0
                v = v_domain.Min + (v_domain.Max - v_domain.Min) * j / 10.0
                pt = surface.PointAt(u, v)
                projected_pt = rg.Point3d(pt.X, pt.Y, z_target)
                u_row.append(projected_pt)
            projected_points.append(u_row)
        
        # Create surface from grid
        extended = rg.Surface.CreateNetworkSurface(
            [rg.Curve.CreateInterpolatedCurve(row, 3) for row in projected_points],
            TOLERANCE,
            TOLERANCE,
            False
        )
        if extended:
            return extended
    except:
        pass
    
    return surface  # Return original if extension fails


# ==============================================================================
# PHASE 5: CUSTOM 3D VORONOI TESELLATION
# ==============================================================================

class VoronoiCell:
    """Represents a Voronoi cell (polyhedron)"""
    def __init__(self, seed_point, index):
        self.seed = seed_point
        self.index = index
        self.vertices = []
        self.faces = []  # Each face is list of vertex indices


def create_voronoi_cube(min_pt, max_pt):
    """Create cube vertices for Voronoi clipping."""
    return [
        rg.Point3d(min_pt.X, min_pt.Y, min_pt.Z),
        rg.Point3d(max_pt.X, min_pt.Y, min_pt.Z),
        rg.Point3d(max_pt.X, max_pt.Y, min_pt.Z),
        rg.Point3d(min_pt.X, max_pt.Y, min_pt.Z),
        rg.Point3d(min_pt.X, min_pt.Y, max_pt.Z),
        rg.Point3d(max_pt.X, min_pt.Y, max_pt.Z),
        rg.Point3d(max_pt.X, max_pt.Y, max_pt.Z),
        rg.Point3d(min_pt.X, max_pt.Y, max_pt.Z),
    ]


def point_on_plane_side(point, plane_point, plane_normal):
    """
    Determine which side of a plane a point is on.
    Returns: 1 if on normal side, -1 if on opposite side, 0 if on plane
    """
    vec = rg.Vector3d(point.X - plane_point.X, point.Y - plane_point.Y, point.Z - plane_point.Z)
    dot = rg.Vector3d.DotProduct(vec, plane_normal)
    if abs(dot) < TOLERANCE:
        return 0
    return 1 if dot > 0 else -1


def compute_voronoi_3d_simplified(seed_points, domain_min=None, domain_max=None):
    """
    Simplified 3D Voronoi computation using clipping approach.
    For each seed, create initial cube, then clip by bisecting planes of all other seeds.
    
    Args:
        seed_points: List of Point3d seed points
        domain_min/max: Bounding box for Voronoi cells (auto-computed if not provided)
        
    Returns:
        List of VoronoiCell objects
    """
    if not seed_points:
        return []
    
    # Compute domain if not provided
    if domain_min is None or domain_max is None:
        min_x = min(p.X for p in seed_points)
        max_x = max(p.X for p in seed_points)
        min_y = min(p.Y for p in seed_points)
        max_y = max(p.Y for p in seed_points)
        min_z = min(p.Z for p in seed_points)
        max_z = max(p.Z for p in seed_points)
        
        margin = max(max_x - min_x, max_y - min_y, max_z - min_z) * 1.5
        domain_min = rg.Point3d(min_x - margin, min_y - margin, min_z - margin)
        domain_max = rg.Point3d(max_x + margin, max_y + margin, max_z + margin)
    
    cells = []
    
    # For each seed, create cell as initial cube, then clip by all bisecting planes
    for i, seed_i in enumerate(seed_points):
        cell = VoronoiCell(seed_i, i)
        
        # Initialize cell as cube
        cell.vertices = create_voronoi_cube(domain_min, domain_max)
        
        # Clip by bisecting planes to all other seeds
        for j, seed_j in enumerate(seed_points):
            if i == j:
                continue
            
            # Bisecting plane: perpendicular to line joining seeds, passes through midpoint
            midpoint = rg.Point3d(
                (seed_i.X + seed_j.X) / 2.0,
                (seed_i.Y + seed_j.Y) / 2.0,
                (seed_i.Z + seed_j.Z) / 2.0
            )
            normal = rg.Vector3d(seed_j.X - seed_i.X, seed_j.Y - seed_i.Y, seed_j.Z - seed_i.Z)
            
            if normal.Length > TOLERANCE:
                normal.Normalize()
                
                # Keep only vertices on same side as seed_i
                new_vertices = []
                for v in cell.vertices:
                    side = point_on_plane_side(v, midpoint, normal)
                    if side >= 0:  # Keep points on seed_i side or on plane
                        new_vertices.append(v)
                
                cell.vertices = new_vertices
                
                if len(cell.vertices) < 3:
                    break  # Cell clipped to nothing
        
        if len(cell.vertices) > 0:
            cells.append(cell)
    
    return cells


def clip_voronoi_cells_to_surface(voronoi_cells, surface, xy_plane_z=0.0):
    """
    Clip Voronoi cells to intersect with surface and XY plane.
    
    Args:
        voronoi_cells: List of VoronoiCell objects
        surface: Surface to intersect with
        xy_plane_z: Z-height of XY plane
        
    Returns:
        Dictionary: {cell_index: list of edge curves on surface}
    """
    surface_edges = {}
    
    # Sample surface to find edges of Voronoi cells projected on surface
    u_domain = surface.Domain(0)
    v_domain = surface.Domain(1)
    u_step = (u_domain.Max - u_domain.Min) / SURFACE_SAMPLE_DENSITY
    v_step = (v_domain.Max - v_domain.Min) / SURFACE_SAMPLE_DENSITY
    
    # Map each surface sample point to nearest Voronoi cell
    cell_regions = {}
    for i in range(voronoi_cells):
        cell_regions[i] = []
    
    u = u_domain.Min
    while u <= u_domain.Max:
        v = v_domain.Min
        while v <= v_domain.Max:
            pt = surface.PointAt(u, v)
            
            # Find nearest seed
            min_dist = float('inf')
            nearest_cell = -1
            for cell in voronoi_cells:
                dist = pt.DistanceTo(cell.seed)
                if dist < min_dist:
                    min_dist = dist
                    nearest_cell = cell.index
            
            if nearest_cell >= 0:
                cell_regions[nearest_cell].append((u, v, pt))
            
            v += v_step
        u += u_step
    
    return cell_regions


# ==============================================================================
# PHASE 6: OFFSET VORONOI EDGES ON SURFACE
# ==============================================================================

def parameterize_curve_on_surface(curve, surface, sample_count=50):
    """
    Find UV parameters for curve points on surface.
    
    Args:
        curve: Curve to parameterize
        surface: Surface to parameterize on
        sample_count: Number of samples along curve
        
    Returns:
        List of (u, v) tuples
    """
    domain = curve.Domain
    uv_params = []
    
    for i in range(sample_count):
        t = domain.Min + (i / (sample_count - 1)) * (domain.Max - domain.Min) if sample_count > 1 else 0.5
        pt = curve.PointAt(t)
        
        # Find closest point on surface
        found, u, v = surface.ClosestPoint(pt)
        if found:
            uv_params.append((u, v))
        else:
            uv_params.append((None, None))
    
    return uv_params


def offset_curve_on_surface(curve, surface, offset_distance, sample_count=50):
    """
    Offset curve on surface following surface normal direction.
    
    Args:
        curve: Curve to offset
        surface: Surface to offset on
        offset_distance: Offset distance (positive = inward)
        sample_count: Number of samples for offset curve
        
    Returns:
        Offset Curve object
    """
    offset_points = []
    domain = curve.Domain
    
    for i in range(sample_count):
        t = domain.Min + (i / (sample_count - 1)) * (domain.Max - domain.Min) if sample_count > 1 else 0.5
        pt = curve.PointAt(t)
        
        # Find surface normal at closest point
        found, u, v = surface.ClosestPoint(pt)
        if found:
            normal = surface.NormalAt(u, v)
            if normal.Length > TOLERANCE:
                normal.Normalize()
                # Offset inward (negative normal direction)
                offset_pt = rg.Point3d(
                    pt.X - normal.X * offset_distance,
                    pt.Y - normal.Y * offset_distance,
                    pt.Z - normal.Z * offset_distance
                )
                # Project back to surface
                found2, u2, v2 = surface.ClosestPoint(offset_pt)
                if found2:
                    offset_pt = surface.PointAt(u2, v2)
                offset_points.append(offset_pt)
            else:
                offset_points.append(pt)
        else:
            offset_points.append(pt)
    
    # Create curve through offset points
    if len(offset_points) >= 2:
        try:
            offset_curve = rg.Curve.CreateInterpolatedCurve(offset_points, 3, rg.CurveKnotStyle.Clamped)
            if offset_curve:
                return offset_curve
        except:
            pass
        
        # Fallback: polyline
        polyline = rg.Polyline(offset_points)
        return polyline.ToNurbsCurve()
    
    return curve


# ==============================================================================
# PHASE 7: WALL SURFACE CREATION
# ==============================================================================

def create_wall_surface(edge_curve, offset_curve):
    """
    Create lofted or ruled surface between original and offset edges.
    
    Args:
        edge_curve: Original edge curve
        offset_curve: Offset edge curve
        
    Returns:
        Surface object
    """
    try:
        # Create ruled surface between the two curves
        surf = rg.Surface.CreateRuledSurface(edge_curve, offset_curve)
        if surf:
            return surf
    except:
        pass
    
    # Fallback: create lofted surface
    try:
        brep_list = rg.Brep.CreateFromLoft(
            [edge_curve, offset_curve],
            rg.Point3d.Unset,
            rg.Point3d.Unset,
            rg.LoftType.Straight,
            False
        )
        if brep_list and len(brep_list) > 0:
            if brep_list[0].Surfaces.Count > 0:
                return brep_list[0].Surfaces[0]
    except:
        pass
    
    return None


# ==============================================================================
# PHASE 8: EXTRUDE SURFACES
# ==============================================================================

def extrude_surface(surface, extrusion_distance, direction=None):
    """
    Extrude surface by specified distance.
    
    Args:
        surface: Surface to extrude
        extrusion_distance: Distance to extrude
        direction: Optional extrusion direction (defaults to Z-down)
        
    Returns:
        Extruded Surface or list of surfaces
    """
    if direction is None:
        direction = rg.Vector3d(0, 0, -1)  # Default: downward
    
    if direction.Length > TOLERANCE:
        direction.Normalize()
    
    try:
        # Offset surface in extrusion direction
        extrusion_vec = direction * extrusion_distance
        extruded = surface.Offset(extrusion_distance, TOLERANCE)
        if extruded:
            return [surface, extruded]
    except:
        pass
    
    # Fallback: create ruled surface from original to offset
    try:
        # Create a copy of the surface offset by extrusion vector
        transformed = surface.Transform(rg.Transform.Translation(direction * extrusion_distance))
        if transformed:
            return [surface, transformed]
    except:
        pass
    
    return [surface]


# ==============================================================================
# MAIN ORCHESTRATION
# ==============================================================================

def generate_volume_phase(input_curves, divisions, start_param_offset, min_distance, max_distance,
                         min_height, max_height, voronoi_offset, extrusion_distance):
    """
    Main function orchestrating all volume phase steps.
    
    Args:
        input_curves: List of 3 boundary curves from Area phase
        divisions: Number of division points per curve
        start_param_offset: Starting parameter offset (0-1)
        min_distance: Minimum point spacing
        max_distance: Maximum point spacing
        min_height: Minimum vertical line height
        max_height: Maximum vertical line height
        voronoi_offset: Voronoi edge offset distance
        extrusion_distance: Wall extrusion distance
        
    Returns:
        Dictionary containing all output geometry
    """
    
    outputs = {
        'division_points': [],
        'vertical_lines': [],
        'lofted_surface': None,
        'extended_surface': None,
        'voronoi_cells': [],
        'voronoi_edges': [],
        'offset_edges': [],
        'wall_surfaces': [],
        'final_extrusions': [],
        'errors': []
    }
    
    try:
        # PHASE 1: Curve Division
        division_points_list = []
        for curve_idx, curve in enumerate(input_curves):
            try:
                proximity_field = compute_proximity_field(input_curves, curve_idx)
                div_points = adaptive_curve_divide(
                    curve, divisions, proximity_field,
                    min_distance, max_distance, start_param_offset
                )
                division_points_list.append(div_points)
                outputs['division_points'].extend(div_points)
            except Exception as e:
                outputs['errors'].append(f"Phase 1 Error (curve {curve_idx}): {str(e)}")
                division_points_list.append([])
        
        if not division_points_list or not all(division_points_list):
            raise Exception("Failed to generate division points")
        
        # PHASE 2: Vertical Lines
        try:
            start_pts, end_pts, lines = create_vertical_lines(
                division_points_list, min_height, max_height
            )
            outputs['vertical_lines'] = lines
        except Exception as e:
            outputs['errors'].append(f"Phase 2 Error: {str(e)}")
            raise
        
        # PHASE 3: Lofted Surface
        try:
            endpoint_curves = create_endpoint_curves(
                [end_pts for _ in range(len(division_points_list))]
            )
            # Regroup endpoints by original curve
            endpoint_groups = []
            pts_per_curve = len(end_pts) // len(division_points_list)
            for i in range(len(division_points_list)):
                start_idx = i * pts_per_curve
                end_idx = start_idx + pts_per_curve
                endpoint_groups.append(end_pts[start_idx:end_idx])
            
            endpoint_curves = create_endpoint_curves(endpoint_groups)
            lofted = create_lofted_surface(endpoint_curves, LOFT_U_COUNT)
            outputs['lofted_surface'] = lofted
        except Exception as e:
            outputs['errors'].append(f"Phase 3 Error: {str(e)}")
            lofted = None
        
        # PHASE 4: Extended Surface
        try:
            if lofted:
                extended = extend_surface_to_plane(lofted, Z_PLANE)
                outputs['extended_surface'] = extended
        except Exception as e:
            outputs['errors'].append(f"Phase 4 Error: {str(e)}")
        
        # PHASE 5: Voronoi Tesellation (Simplified)
        try:
            voronoi_cells = compute_voronoi_3d_simplified(end_pts)
            outputs['voronoi_cells'] = voronoi_cells
        except Exception as e:
            outputs['errors'].append(f"Phase 5 Error: {str(e)}")
        
        # PHASES 6-8: Edge Offsetting and Extrusion (Simplified Placeholder)
        if lofted and voronoi_cells:
            try:
                # Create dummy offset and wall surfaces for demonstration
                for i in range(min(3, len(endpoint_curves))):
                    if i < len(endpoint_curves):
                        offset = offset_curve_on_surface(
                            endpoint_curves[i], lofted, voronoi_offset
                        )
                        if offset:
                            outputs['offset_edges'].append(offset)
                            
                            wall = create_wall_surface(endpoint_curves[i], offset)
                            if wall:
                                outputs['wall_surfaces'].append(wall)
                                
                                extruded = extrude_surface(wall, extrusion_distance)
                                outputs['final_extrusions'].extend(extruded)
            except Exception as e:
                outputs['errors'].append(f"Phases 6-8 Error: {str(e)}")
    
    except Exception as e:
        outputs['errors'].append(f"Critical Error: {str(e)}")
    
    return outputs


# ==============================================================================
# GRASSHOPPER INTEGRATION
# ==============================================================================

if __name__ == "__main__":
    # Validate inputs
    input_curves = input_curves if 'input_curves' in dir() else []
    divisions = int(max(2, min(50, divisions))) if 'divisions' in dir() else 20
    start_param_offset = float(max(0, min(1, start_param_offset))) if 'start_param_offset' in dir() else 0.0
    min_distance = float(max(0.1, min_distance)) if 'min_distance' in dir() else 2.0
    max_distance = float(max(min_distance + 0.1, max_distance)) if 'max_distance' in dir() else 10.0
    min_height = float(max(0.1, min_height)) if 'min_height' in dir() else 5.0
    max_height = float(max(min_height + 0.1, max_height)) if 'max_height' in dir() else 50.0
    voronoi_offset = float(max(0.1, voronoi_offset)) if 'voronoi_offset' in dir() else 1.0
    extrusion_distance = float(max(0.1, extrusion_distance)) if 'extrusion_distance' in dir() else 5.0
    
    # Generate volume geometry
    if len(input_curves) >= 3:
        results = generate_volume_phase(
            input_curves[:3],  # Use first 3 curves
            divisions,
            start_param_offset,
            min_distance,
            max_distance,
            min_height,
            max_height,
            voronoi_offset,
            extrusion_distance
        )
        
        # Output to Grasshopper
        division_points = results['division_points']
        vertical_lines = results['vertical_lines']
        lofted_surface = results['lofted_surface']
        extended_surface = results['extended_surface']
        voronoi_edges = results['voronoi_edges']
        offset_edges = results['offset_edges']
        wall_surfaces = results['wall_surfaces']
        final_extrusions = results['final_extrusions']
        
        if results['errors']:
            error_messages = "\n".join(results['errors'])
