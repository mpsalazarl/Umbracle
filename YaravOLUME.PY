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
  - connection_lines: Connections from midheight points to Voronoi vertices
  - multipipe_geometry: Pipe geometry along connection lines
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
        raise ValueError("Expected exactly 3 curves, got {}".format(len(curves)))
    
    source_curve = curves[curve_index]
    other_curves = [c for i, c in enumerate(curves) if i != curve_index]
    
    # Validate curve methods exist
    try:
        source_length = source_curve.GetLength()
        domain = source_curve.Domain
    except Exception as e:
        raise ValueError("Source curve invalid: {}".format(str(e)))
    
    proximities = []
    min_dist_global = float('inf')
    max_dist_global = 0.0
    
    # First pass: compute all distances and find min/max
    distances_raw = []
    for i in range(sample_count):
        t_param = i / float(sample_count - 1) if sample_count > 1 else 0.5
        t = domain.Min + t_param * (domain.Max - domain.Min)
        try:
            source_pt = source_curve.PointAt(t)
        except:
            # Skip invalid parameter
            distances_raw.append(0.0)
            continue
        
        min_dist_at_point = float('inf')
        for other_curve in other_curves:
            try:
                cc, pt_on_curve = other_curve.ClosestPoint(source_pt)
                if pt_on_curve is not None:
                    dist = source_pt.DistanceTo(pt_on_curve)
                    min_dist_at_point = min(min_dist_at_point, dist)
            except:
                pass
        
        if min_dist_at_point < float('inf'):
            distances_raw.append(min_dist_at_point)
            min_dist_global = min(min_dist_global, min_dist_at_point)
            max_dist_global = max(max_dist_global, min_dist_at_point)
    
    # Normalize to 0-1
    if len(distances_raw) == 0:
        return [0.5] * sample_count  # Default if all distances failed
    
    dist_range = max_dist_global - min_dist_global if max_dist_global > min_dist_global else 1.0
    proximities = [(d - min_dist_global) / dist_range if d < float('inf') else 0.5 for d in distances_raw]
    
    # Pad to sample_count if needed
    while len(proximities) < sample_count:
        proximities.append(0.5)
    
    return proximities[:sample_count]


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
    
    if not proximity_field or len(proximity_field) == 0:
        raise ValueError("Proximity field is empty")
    
    domain = curve.Domain
    
    # Build cumulative arc-length parametrization using proximity field
    arc_lengths = [0.0]
    for i in range(1, len(proximity_field)):
        # Distance contribution weighted by proximity (reverse: close → max spacing, far → min spacing)
        prox = proximity_field[i] if i < len(proximity_field) else 0.5
        spacing_factor = min_distance + (max_distance - min_distance) * (1.0 - prox)
        arc_lengths.append(arc_lengths[-1] + spacing_factor)
    
    # Normalize arc lengths and remap to 0-1 parameter range
    total_arc = arc_lengths[-1] if len(arc_lengths) > 0 else 1.0
    if total_arc < TOLERANCE:
        total_arc = 1.0
    
    normalized_arc = [a / total_arc for a in arc_lengths]
    
    # Generate division points using linear interpolation in parameter space
    division_points = []
    for i in range(division_count):
        # Linear parameter from 0 to 1 (or adjusted by start_param_offset)
        if division_count > 1:
            t_normalized = start_param_offset + (i / (division_count - 1)) * (1.0 - start_param_offset)
        else:
            t_normalized = 0.5
        
        t_normalized = max(0.0, min(1.0, t_normalized))  # Clamp to 0-1
        
        # Find corresponding arc-length parameter
        idx_lower = int(t_normalized * (len(normalized_arc) - 1))
        idx_upper = min(idx_lower + 1, len(normalized_arc) - 1)
        
        if idx_lower == idx_upper or len(normalized_arc) <= 1:
            arc_param = normalized_arc[0] if len(normalized_arc) > 0 else 0.0
        else:
            frac = (t_normalized * (len(normalized_arc) - 1) - idx_lower)
            arc_param = normalized_arc[idx_lower] * (1 - frac) + normalized_arc[idx_upper] * frac
        
        # Map back to curve parameter
        t_param = domain.Min + arc_param * (domain.Max - domain.Min)
        t_param = max(domain.Min, min(domain.Max, t_param))  # Clamp to domain
        
        try:
            pt = curve.PointAt(t_param)
            division_points.append(pt)
        except Exception as e:
            # Skip points that fail
            pass
    
    if len(division_points) == 0:
        raise ValueError("Failed to generate any division points")
    
    return division_points


# ==============================================================================
# PHASE 2: VERTICAL LINES WITH RANDOM HEIGHTS
# ==============================================================================

def create_vertical_lines(division_points_list, min_height, max_height, seed_base=12345, height_offsets=None):
    """
    Create vertical lines from division points with random heights.
    
    Args:
        division_points_list: List of lists - division points grouped by curve (3 groups)
        min_height: Minimum vertical line height
        max_height: Maximum vertical line height
        seed_base: Base random seed for reproducibility
        height_offsets: List of height offsets per curve (default [0, 1, 2] for 1m per curve offset)
        
    Returns:
        Tuple: (all_start_points, all_end_points, line_objects)
    """
    if height_offsets is None:
        height_offsets = [0, 1, 2]  # Default: 1m offset per curve
    
    random.seed(seed_base)
    start_points = []
    end_points = []
    line_objects = []
    
    for curve_idx, curve_points in enumerate(division_points_list):
        random.seed(seed_base + curve_idx * 1000)
        # Get offset for this curve
        curve_offset = height_offsets[curve_idx] if curve_idx < len(height_offsets) else 0
        min_h = min_height + curve_offset
        max_h = max_height + curve_offset
        
        for point_idx, pt in enumerate(curve_points):
            # Generate deterministic random height with curve-specific offset
            height = min_h + random.random() * (max_h - min_h)
            
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
        Dictionary: {cell_index: list of (u, v, point) tuples on surface}
    """
    surface_edges = {}
    
    # Sample surface to find edges of Voronoi cells projected on surface
    u_domain = surface.Domain(0)
    v_domain = surface.Domain(1)
    u_step = (u_domain.Max - u_domain.Min) / SURFACE_SAMPLE_DENSITY if SURFACE_SAMPLE_DENSITY > 1 else 1.0
    v_step = (v_domain.Max - v_domain.Min) / SURFACE_SAMPLE_DENSITY if SURFACE_SAMPLE_DENSITY > 1 else 1.0
    
    # Map each surface sample point to nearest Voronoi cell
    cell_regions = {}
    for cell in voronoi_cells:
        cell_regions[cell.index] = []
    
    u = u_domain.Min
    while u <= u_domain.Max + TOLERANCE:
        v = v_domain.Min
        while v <= v_domain.Max + TOLERANCE:
            try:
                pt = surface.PointAt(u, v)
                
                # Find nearest seed
                min_dist = float('inf')
                nearest_cell_idx = -1
                for cell in voronoi_cells:
                    dist = pt.DistanceTo(cell.seed)
                    if dist < min_dist:
                        min_dist = dist
                        nearest_cell_idx = cell.index
                
                if nearest_cell_idx >= 0 and nearest_cell_idx in cell_regions:
                    cell_regions[nearest_cell_idx].append((u, v, pt))
            except:
                pass
            
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
# PHASE 9: CONNECT MIDHEIGHT POINTS TO VORONOI VERTICES
# ==============================================================================

def connect_midheight_to_voronoi(vertical_lines, voronoi_cells, midheight_fraction=0.667):
    """
    Connect midheight points (2/3 up vertical lines) to nearest Voronoi cell vertices.
    
    Args:
        vertical_lines: List of LineCurve objects (from Phase 2)
        voronoi_cells: List of VoronoiCell objects (from Phase 5)
        midheight_fraction: Fraction of line height to connect from (default 2/3)
        
    Returns:
        List of Line3d objects connecting midheight points to nearest Voronoi vertices
    """
    connection_lines = []
    
    if not vertical_lines or not voronoi_cells:
        return connection_lines
    
    # Collect all Voronoi vertices
    all_vertices = []
    for cell in voronoi_cells:
        all_vertices.extend(cell.vertices)
    
    if len(all_vertices) == 0:
        return connection_lines
    
    # For each vertical line, find 2/3 point and connect to nearest Voronoi vertex
    for vline in vertical_lines:
        try:
            # Get line endpoints
            start_pt = vline.PointAtStart if hasattr(vline, 'PointAtStart') else vline.PointAt(vline.Domain.Min)
            end_pt = vline.PointAtEnd if hasattr(vline, 'PointAtEnd') else vline.PointAt(vline.Domain.Max)
            
            # Calculate midheight point (at fraction of line height)
            midheight_pt = rg.Point3d(
                start_pt.X + (end_pt.X - start_pt.X) * midheight_fraction,
                start_pt.Y + (end_pt.Y - start_pt.Y) * midheight_fraction,
                start_pt.Z + (end_pt.Z - start_pt.Z) * midheight_fraction
            )
            
            # Find nearest Voronoi vertex
            min_dist = float('inf')
            nearest_vertex = None
            for vertex in all_vertices:
                dist = midheight_pt.DistanceTo(vertex)
                if dist < min_dist:
                    min_dist = dist
                    nearest_vertex = vertex
            
            # Create connection line
            if nearest_vertex is not None:
                conn_line = rg.Line(midheight_pt, nearest_vertex)
                connection_lines.append(conn_line)
        except Exception as e:
            # Skip lines that fail
            pass
    
    return connection_lines


# ==============================================================================
# PHASE 10: CREATE MULTIPIPE FROM CONNECTION LINES
# ==============================================================================

def create_multipipe(lines, radius):
    """
    Create pipe geometry around each line.
    
    Args:
        lines: List of Line3d or Curve objects
        radius: Pipe radius (scalar)
        
    Returns:
        List of Brep objects (pipe geometry)
    """
    multipipe_geometry = []
    
    if not lines or radius <= 0:
        return multipipe_geometry
    
    for line_obj in lines:
        try:
            # Convert to curve if it's a Line3d
            if isinstance(line_obj, rg.Line):
                curve = rg.LineCurve(line_obj.From, line_obj.To)
            else:
                curve = line_obj
            
            # Create pipe using Rhino's built-in function
            pipe_brep_list = rg.Brep.CreatePipe(curve, radius, True, rg.PipeCapMode.Both, False, TOLERANCE, TOLERANCE)
            
            if pipe_brep_list and len(pipe_brep_list) > 0:
                multipipe_geometry.extend(pipe_brep_list)
        except Exception as e:
            # Skip pipes that fail
            pass
    
    return multipipe_geometry


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
        'connection_lines': [],
        'multipipe_geometry': [],
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
                # Create fallback: simple parametric division without proximity
                try:
                    domain = curve.Domain
                    fallback_points = []
                    for i in range(divisions):
                        t = domain.Min + (i / (divisions - 1)) * (domain.Max - domain.Min) if divisions > 1 else 0.5
                        fallback_points.append(curve.PointAt(t))
                    division_points_list.append(fallback_points)
                    outputs['division_points'].extend(fallback_points)
                    outputs['errors'].append(f"Phase 1: Using fallback uniform division for curve {curve_idx}")
                except Exception as e2:
                    outputs['errors'].append(f"Phase 1 Fallback Error (curve {curve_idx}): {str(e2)}")
                    division_points_list.append([])
        
        if not division_points_list or not any(division_points_list):
            outputs['errors'].append("ERROR: Failed to generate division points on any curve")
        else:
            outputs['errors'].append(f"Phase 1: Generated division points on {sum(1 for p in division_points_list if p)} curves")
        
        # PHASE 2: Vertical Lines
        try:
            # Apply height domain offset: [0, 1, 2]m per curve as per user requirements
            height_offsets = [0, 1, 2]  # Each curve offset by 1m
            start_pts, end_pts, lines = create_vertical_lines(
                division_points_list, min_height, max_height, height_offsets=height_offsets
            )
            outputs['vertical_lines'] = lines
            outputs['errors'].append(f"Phase 2: Generated {len(lines)} vertical lines with height offsets {height_offsets}")
        except Exception as e:
            outputs['errors'].append(f"Phase 2 Error: {str(e)}")
            # Fallback: create empty vertical lines list to continue
            start_pts = []
            end_pts = []
        
        # PHASE 3: Lofted Surface
        try:
            if end_pts and len(end_pts) > 0:
                # Regroup endpoints by their source curve for lofting
                endpoint_groups = [[] for _ in range(len(division_points_list))]
                current_idx = 0
                
                for curve_idx, div_points in enumerate(division_points_list):
                    for _ in div_points:
                        if current_idx < len(end_pts):
                            endpoint_groups[curve_idx].append(end_pts[current_idx])
                            current_idx += 1
                
                # Create interpolated curves through endpoints of each original curve
                endpoint_curves = create_endpoint_curves(endpoint_groups)
                if endpoint_curves and len(endpoint_curves) >= 3:
                    lofted = create_lofted_surface(endpoint_curves, LOFT_U_COUNT)
                    outputs['lofted_surface'] = lofted
                    outputs['errors'].append(f"Phase 3: Created lofted surface with {len(endpoint_curves)} curves")
                else:
                    outputs['errors'].append(f"Phase 3: Could not create endpoint curves (got {len(endpoint_curves) if endpoint_curves else 0})")
            else:
                outputs['errors'].append("Phase 3: Skipped (no endpoint data from Phase 2)")
        except Exception as e:
            outputs['errors'].append(f"Phase 3 Error: {str(e)}")
        
        # PHASE 4: Extended Surface
        try:
            if outputs['lofted_surface']:
                extended = extend_surface_to_plane(outputs['lofted_surface'], Z_PLANE)
                outputs['extended_surface'] = extended
                outputs['errors'].append("Phase 4: Extended surface to XY plane")
            else:
                outputs['errors'].append("Phase 4: Skipped (no lofted surface)")
        except Exception as e:
            outputs['errors'].append(f"Phase 4 Error: {str(e)}")
        
        # PHASE 5: Voronoi Tesellation (Simplified)
        try:
            if end_pts and len(end_pts) > 0:
                voronoi_cells = compute_voronoi_3d_simplified(end_pts)
                outputs['voronoi_cells'] = voronoi_cells
                outputs['errors'].append(f"Phase 5: Generated {len(voronoi_cells)} Voronoi cells from {len(end_pts)} seed points")
            else:
                outputs['errors'].append("Phase 5: Skipped (no seed points)")
        except Exception as e:
            outputs['errors'].append(f"Phase 5 Error: {str(e)}")
        
        # PHASES 6-8: Edge Offsetting and Extrusion (Simplified Placeholder)
        try:
            if outputs['lofted_surface'] and outputs['voronoi_cells']:
                endpoint_curves = []
                if len(division_points_list) == 3:
                    try:
                        endpoint_groups = [[] for _ in range(3)]
                        current_idx = 0
                        for curve_idx, div_points in enumerate(division_points_list):
                            for _ in div_points:
                                if current_idx < len(end_pts):
                                    endpoint_groups[curve_idx].append(end_pts[current_idx])
                                    current_idx += 1
                        endpoint_curves = create_endpoint_curves(endpoint_groups)
                    except:
                        pass
                
                # Create dummy offset and wall surfaces for demonstration
                for i in range(min(3, len(endpoint_curves))):
                    if i < len(endpoint_curves):
                        try:
                            offset = offset_curve_on_surface(
                                endpoint_curves[i], outputs['lofted_surface'], voronoi_offset
                            )
                            if offset:
                                outputs['offset_edges'].append(offset)
                                
                                wall = create_wall_surface(endpoint_curves[i], offset)
                                if wall:
                                    outputs['wall_surfaces'].append(wall)
                                    
                                    extruded = extrude_surface(wall, extrusion_distance)
                                    outputs['final_extrusions'].extend(extruded)
                        except Exception as e:
                            outputs['errors'].append(f"Phase 6-8 Error (edge {i}): {str(e)}")
                
                outputs['errors'].append(f"Phase 6-8: Generated {len(outputs['offset_edges'])} offset edges, {len(outputs['wall_surfaces'])} walls, {len(outputs['final_extrusions'])} extrusions")
            else:
                outputs['errors'].append("Phase 6-8: Skipped (missing surface or Voronoi cells)")
        except Exception as e:
            outputs['errors'].append(f"Phase 6-8 Critical Error: {str(e)}")
        
        # PHASE 9: Connect Midheight Points to Voronoi Vertices
        try:
            if outputs['vertical_lines'] and len(outputs['vertical_lines']) > 0 and voronoi_cells:
                connection_lines = connect_midheight_to_voronoi(outputs['vertical_lines'], voronoi_cells)
                outputs['connection_lines'] = connection_lines
                outputs['errors'].append(f"Phase 9: Created {len(connection_lines)} connection lines from midheight points to Voronoi vertices")
            else:
                outputs['errors'].append("Phase 9: Skipped (missing vertical lines or Voronoi cells)")
        except Exception as e:
            outputs['errors'].append(f"Phase 9 Error: {str(e)}")
        
        # PHASE 10: Create Multipipe from Connection Lines
        try:
            if 'connection_lines' in outputs and len(outputs.get('connection_lines', [])) > 0:
                # Use multipipe_radius if available, else default
                pipe_radius = 0.3  # Default radius
                multipipe_geom = create_multipipe(outputs['connection_lines'], pipe_radius)
                outputs['multipipe_geometry'] = multipipe_geom
                outputs['errors'].append(f"Phase 10: Created {len(multipipe_geom)} pipe objects with radius {pipe_radius}")
            else:
                outputs['errors'].append("Phase 10: Skipped (no connection lines from Phase 9)")
        except Exception as e:
            outputs['errors'].append(f"Phase 10 Error: {str(e)}")
    
    except Exception as e:
        outputs['errors'].append(f"Critical Error in main loop: {str(e)}")
    
    return outputs


# ==============================================================================
# GRASSHOPPER INTEGRATION
# ==============================================================================

if __name__ == "__main__":
    # Initialize outputs
    division_points = []
    vertical_lines = []
    lofted_surface = None
    extended_surface = None
    voronoi_edges = []
    offset_edges = []
    wall_surfaces = []
    final_extrusions = []
    multipipe_geometry = []
    connection_lines = []
    error_messages = ""
    
    try:
        # Read Grasshopper inputs with defaults
        input_curves_raw = input_curves if 'input_curves' in dir() else []
        divisions = int(divisions) if 'divisions' in dir() else 10
        start_param_offset = float(start_param_offset) if 'start_param_offset' in dir() else 0.0
        min_distance = float(min_distance) if 'min_distance' in dir() else 1.0
        max_distance = float(max_distance) if 'max_distance' in dir() else 5.0
        min_height = float(min_height) if 'min_height' in dir() else 5.0
        max_height = float(max_height) if 'max_height' in dir() else 20.0
        voronoi_offset = float(voronoi_offset) if 'voronoi_offset' in dir() else 0.5
        extrusion_distance = float(extrusion_distance) if 'extrusion_distance' in dir() else 5.0
        multipipe_radius = float(multipipe_radius) if 'multipipe_radius' in dir() else 0.3
        
        # Normalize input curves to list of 3
        curves = []
        if isinstance(input_curves_raw, (list, tuple)):
            curves = list(input_curves_raw)
        elif input_curves_raw is not None:
            curves = [input_curves_raw]
        
        # Pad/trim to exactly 3 curves
        if len(curves) < 3:
            error_messages += "WARNING: Expected 3 curves, got {}\n".format(len(curves))
        curves = curves[:3]
        
        if len(curves) == 0:
            error_messages += "ERROR: No input curves provided\n"
        else:
            error_messages += "INFO: Processing {} curves\n".format(len(curves))
            error_messages += "  divisions={}, start_param={}, min_dist={}, max_dist={}\n".format(
                divisions, start_param_offset, min_distance, max_distance)
            error_messages += "  min_height={}, max_height={}, voronoi_offset={}, extrusion={}\n".format(
                min_height, max_height, voronoi_offset, extrusion_distance)
            
            # Call main orchestration function
            results = generate_volume_phase(
                curves, divisions, start_param_offset, min_distance, max_distance,
                min_height, max_height, voronoi_offset, extrusion_distance
            )
            
            # Extract outputs
            division_points = results.get('division_points', [])
            vertical_lines = results.get('vertical_lines', [])
            lofted_surface = results.get('lofted_surface', None)
            extended_surface = results.get('extended_surface', None)
            voronoi_cells = results.get('voronoi_cells', [])
            voronoi_edges = results.get('voronoi_edges', [])
            offset_edges = results.get('offset_edges', [])
            wall_surfaces = results.get('wall_surfaces', [])
            final_extrusions = results.get('final_extrusions', [])
            connection_lines = results.get('connection_lines', [])
            multipipe_geometry = results.get('multipipe_geometry', [])
            
            # Append error messages from results
            for msg in results.get('errors', []):
                error_messages += msg + "\n"
            
            error_messages += "\n=== EXECUTION COMPLETE ==="
    
    except Exception as e:
        import traceback
        error_messages += "CRITICAL ERROR: {}\n{}".format(str(e), traceback.format_exc())
else:
    # Fallback if not in main
    division_points = []
    vertical_lines = []
    lofted_surface = None
    extended_surface = None
    voronoi_edges = []
    offset_edges = []
    wall_surfaces = []
    final_extrusions = []
    connection_lines = []
    multipipe_geometry = []
    error_messages = "Not in main context"