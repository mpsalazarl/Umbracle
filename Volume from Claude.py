"""
Grasshopper Python Script: Parametric Surface from Curves with Voronoi Tessellation
Inputs:
    Curves: List of 3 curves (Curve)
    DivisionCount: Number of divisions per curve - must be even (int)
    StartParam: Starting parameter for division 0-1 (float)
    MinDist: Minimum distance between division points (float)
    MaxDist: Maximum distance between division points (float)
    HeightMin1: Minimum height for vertical lines - Curve 1 (float)
    HeightMax1: Maximum height for vertical lines - Curve 1 (float)
    VoronoiOffset: Offset distance for Voronoi edges (float)
    ExtrudeDistance: Extrusion distance for offset surfaces (float)
    PipeRadius: Radius for multipipe (float)
    
Outputs:
    DivisionPoints: Points on curves after non-uniform division
    VerticalLines: Vertical lines from division points
    LoftSurface: Surface created from vertical line endpoints
    ExtendedSurface: Surface extended to XY plane
    VoronoiCells: Voronoi tessellation on surface
    OffsetEdges: Offset Voronoi edges
    OffsetSurfaces: Surfaces between original and offset edges
    ExtrudedSurfaces: Extruded offset surfaces
    ConnectionLines: Lines connecting 2/3 height points to Voronoi vertices
    Pipes: Multipipe geometry from connection lines
"""

import Rhino
import Rhino.Geometry as rg
import ghpythonlib.components as ghcomp
import random
import math
import scriptcontext as sc

# Set tolerance
tol = sc.doc.ModelAbsoluteTolerance

def get_curve_proximity_factor(curve, point, other_curves):
    """
    Calculate proximity factor based on distance to other curves.
    Returns a value between 0 and 1, where 1 means close to other curves.
    """
    min_dist = float('inf')
    
    for other_curve in other_curves:
        if other_curve.GetHashCode() != curve.GetHashCode():
            result, param = other_curve.ClosestPoint(point)
            if result:
                closest_pt = other_curve.PointAt(param)
                dist = point.DistanceTo(closest_pt)
                min_dist = min(min_dist, dist)
    
    # Normalize distance (assuming max relevant distance is 100 units)
    max_relevant_dist = 100.0
    proximity = 1.0 - min(min_dist / max_relevant_dist, 1.0)
    return proximity

def divide_curve_nonuniform(curve, division_count, start_param, min_dist, max_dist, other_curves):
    """
    Divide curve with non-uniform spacing based on proximity to other curves.
    Larger spacing where closer to other curves.
    """
    if division_count % 2 != 0:
        division_count += 1  # Ensure even number
    
    points = []
    parameters = []
    
    # Get curve domain
    domain = curve.Domain
    curve_length = curve.GetLength()
    
    # Normalize start parameter to curve domain
    start_t = domain.T0 + (domain.T1 - domain.T0) * start_param
    
    # Sample points along curve to determine proximity-based spacing
    sample_count = 100
    proximity_values = []
    sample_params = []
    
    for i in range(sample_count + 1):
        t = domain.T0 + (domain.T1 - domain.T0) * (i / float(sample_count))
        pt = curve.PointAt(t)
        prox = get_curve_proximity_factor(curve, pt, other_curves)
        proximity_values.append(prox)
        sample_params.append(t)
    
    # Calculate non-uniform spacing
    # Higher proximity = larger distance (spacing)
    target_lengths = []
    total_target = 0
    
    for i in range(division_count):
        # Map division index to sample index
        sample_idx = int((i / float(division_count)) * sample_count)
        prox = proximity_values[min(sample_idx, sample_count)]
        
        # Interpolate between min and max distance based on proximity
        spacing = min_dist + (max_dist - min_dist) * prox
        target_lengths.append(spacing)
        total_target += spacing
    
    # Normalize to fit curve length
    scale_factor = curve_length / total_target if total_target > 0 else 1.0
    
    # Generate division points
    current_length = curve.GetLength() * start_param
    
    for i in range(division_count + 1):
        # Find point at current length
        result, param = curve.LengthParameter(current_length % curve_length)
        if result:
            pt = curve.PointAt(param)
            points.append(pt)
            parameters.append(param)
        
        if i < division_count:
            current_length += target_lengths[i] * scale_factor
    
    return points, parameters

def create_vertical_lines(points, height_min, height_max):
    """
    Create vertical lines from points with random heights within domain.
    """
    lines = []
    end_points = []
    
    for pt in points:
        height = random.uniform(height_min, height_max)
        end_pt = rg.Point3d(pt.X, pt.Y, pt.Z + height)
        line = rg.Line(pt, end_pt)
        lines.append(line)
        end_points.append(end_pt)
    
    return lines, end_points

def create_surface_from_points(all_endpoints, u_count=20):
    """
    Create a lofted surface from endpoint groups.
    """
    # Organize points into rows for lofting
    curves_for_loft = []
    
    for curve_endpoints in all_endpoints:
        if len(curve_endpoints) >= 2:
            # Create interpolated curve through endpoints
            curve = rg.Curve.CreateInterpolatedCurve(curve_endpoints, 3)
            if curve:
                curves_for_loft.append(curve)
    
    if len(curves_for_loft) >= 2:
        # Loft curves to create surface
        loft = rg.Brep.CreateFromLoft(curves_for_loft, rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False)
        if loft and len(loft) > 0:
            return loft[0], curves_for_loft
    
    return None, curves_for_loft

def extend_surface_to_xy_plane(surface):
    """
    Extend surface following its curvature until it intersects XY plane.
    """
    if surface is None:
        return None
    
    # Get surface edges
    extended_surfaces = [surface]
    
    # Create XY plane
    xy_plane = rg.Plane.WorldXY
    
    # Get naked edges of the surface
    edges = surface.Edges
    
    for edge in edges:
        # Check if edge needs extension (not on XY plane)
        mid_pt = edge.PointAtNormalizedLength(0.5)
        if abs(mid_pt.Z) > tol:
            # Get surface at this edge
            face = surface.Faces[0]
            
            # Try to extend the surface
            extended = rg.BrepFace.CreateExtrusion(edge, rg.Vector3d(0, 0, -mid_pt.Z), True)
            if extended:
                extended_surfaces.append(extended)
    
    # Join all surfaces
    if len(extended_surfaces) > 1:
        joined = rg.Brep.JoinBreps(extended_surfaces, tol)
        if joined and len(joined) > 0:
            return joined[0]
    
    return surface

def create_voronoi_on_surface(surface, points):
    """
    Create Voronoi tessellation on surface using the construction points.
    """
    if surface is None:
        return [], []
    
    # Flatten points to surface UV space
    face = surface.Faces[0]
    uv_points = []
    
    for pt in points:
        result, u, v = face.ClosestPoint(pt)
        if result:
            uv_points.append(rg.Point3d(u, v, 0))
    
    if len(uv_points) < 3:
        return [], []
    
    # Get surface domain for boundary
    u_domain = face.Domain(0)
    v_domain = face.Domain(1)
    
    # Create boundary rectangle in UV space
    boundary = rg.Rectangle3d(
        rg.Plane.WorldXY,
        rg.Interval(u_domain.T0, u_domain.T1),
        rg.Interval(v_domain.T0, v_domain.T1)
    ).ToNurbsCurve()
    
    # Create Voronoi in UV space
    try:
        voronoi_cells = ghcomp.Voronoi(uv_points, boundary)
        
        # Map Voronoi cells back to surface
        surface_cells = []
        cell_edges = []
        
        if voronoi_cells:
            cells = voronoi_cells if isinstance(voronoi_cells, list) else [voronoi_cells]
            
            for cell in cells:
                if cell:
                    # Get cell vertices in UV space and map to surface
                    if hasattr(cell, 'Vertices'):
                        surface_pts = []
                        for vertex in cell.Vertices:
                            uv = vertex.Location
                            surface_pt = face.PointAt(uv.X, uv.Y)
                            surface_pts.append(surface_pt)
                        
                        if len(surface_pts) >= 3:
                            # Create curve on surface
                            surface_pts.append(surface_pts[0])  # Close the curve
                            cell_curve = rg.PolylineCurve(surface_pts)
                            surface_cells.append(cell_curve)
                            cell_edges.extend(get_cell_edges(surface_pts[:-1]))
        
        return surface_cells, cell_edges
    except:
        # Fallback: create simple Voronoi
        return [], []

def get_cell_edges(vertices):
    """
    Get edges from cell vertices.
    """
    edges = []
    for i in range(len(vertices)):
        start = vertices[i]
        end = vertices[(i + 1) % len(vertices)]
        edge = rg.Line(start, end)
        edges.append(edge)
    return edges

def offset_edges_on_surface(edges, surface, offset_distance):
    """
    Offset edges on the surface.
    """
    offset_edges = []
    
    if surface is None:
        return offset_edges
    
    face = surface.Faces[0]
    
    for edge in edges:
        # Get edge midpoint and find surface normal
        mid_pt = edge.PointAt(0.5)
        result, u, v = face.ClosestPoint(mid_pt)
        
        if result:
            normal = face.NormalAt(u, v)
            
            # Get edge direction
            edge_dir = edge.Direction
            edge_dir.Unitize()
            
            # Calculate offset direction (perpendicular to edge on surface)
            offset_dir = rg.Vector3d.CrossProduct(normal, edge_dir)
            offset_dir.Unitize()
            
            # Create offset edge
            offset_start = edge.From + offset_dir * offset_distance
            offset_end = edge.To + offset_dir * offset_distance
            
            # Project back to surface
            result1, u1, v1 = face.ClosestPoint(offset_start)
            result2, u2, v2 = face.ClosestPoint(offset_end)
            
            if result1 and result2:
                offset_start = face.PointAt(u1, v1)
                offset_end = face.PointAt(u2, v2)
                offset_edge = rg.Line(offset_start, offset_end)
                offset_edges.append(offset_edge)
    
    return offset_edges

def create_surfaces_between_edges(original_edges, offset_edges):
    """
    Create surfaces between original and offset edges.
    """
    surfaces = []
    
    for i in range(min(len(original_edges), len(offset_edges))):
        orig = original_edges[i]
        offs = offset_edges[i]
        
        # Create four corner points
        corners = [orig.From, orig.To, offs.To, offs.From]
        corners.append(corners[0])  # Close
        
        # Create surface
        polyline = rg.Polyline(corners)
        curve = polyline.ToNurbsCurve()
        
        # Create planar surface
        breps = rg.Brep.CreatePlanarBreps(curve, tol)
        if breps and len(breps) > 0:
            surfaces.append(breps[0])
    
    return surfaces

def extrude_surfaces(surfaces, distance):
    """
    Extrude surfaces by given distance.
    """
    extruded = []
    
    direction = rg.Vector3d(0, 0, distance)
    
    for surface in surfaces:
        if surface:
            extrusion = surface.Faces[0].CreateExtrusion(
                rg.LineCurve(rg.Point3d.Origin, rg.Point3d(0, 0, distance)),
                True
            )
            if extrusion:
                extruded.append(extrusion)
    
    return extruded

def get_two_thirds_points(vertical_lines):
    """
    Get points at 2/3 height of vertical lines.
    """
    points = []
    
    for line in vertical_lines:
        pt = line.PointAt(2.0 / 3.0)
        points.append(pt)
    
    return points

def find_closest_voronoi_vertices(point, voronoi_cells):
    """
    Find vertices of the closest Voronoi cell to a point.
    """
    closest_cell = None
    min_dist = float('inf')
    
    for cell in voronoi_cells:
        if cell:
            # Get cell centroid
            if hasattr(cell, 'GetBoundingBox'):
                bbox = cell.GetBoundingBox(True)
                centroid = bbox.Center
                dist = point.DistanceTo(centroid)
                
                if dist < min_dist:
                    min_dist = dist
                    closest_cell = cell
    
    vertices = []
    if closest_cell:
        if hasattr(closest_cell, 'Points'):
            for i in range(closest_cell.PointCount):
                vertices.append(closest_cell.Point(i))
    
    return vertices

def create_connection_lines(two_thirds_points, voronoi_cells):
    """
    Create lines connecting 2/3 height points to closest Voronoi vertices.
    """
    lines = []
    
    for pt in two_thirds_points:
        vertices = find_closest_voronoi_vertices(pt, voronoi_cells)
        
        for vertex in vertices:
            line = rg.Line(pt, vertex)
            lines.append(line)
    
    return lines

def create_multipipe(lines, radius):
    """
    Create multipipe geometry from lines.
    """
    if not lines:
        return None
    
    # Convert lines to curves
    curves = [rg.LineCurve(line) for line in lines]
    
    # Create pipes for each curve
    pipes = []
    for curve in curves:
        circle = rg.Circle(curve.PointAtStart, radius)
        pipe = rg.Brep.CreatePipe(curve, radius, False, rg.PipeCapMode.Round, True, tol, tol)
        if pipe:
            pipes.extend(pipe)
    
    return pipes


# ============== MAIN EXECUTION ==============

# Validate inputs
if not Curves or len(Curves) < 3:
    raise ValueError("Please provide exactly 3 curves")

if DivisionCount % 2 != 0:
    DivisionCount = DivisionCount + 1  # Ensure even

# Initialize random seed for reproducibility
random.seed(42)

# Step 1: Divide curves with non-uniform spacing
all_division_points = []
all_parameters = []

for i, curve in enumerate(Curves[:3]):
    points, params = divide_curve_nonuniform(
        curve, 
        DivisionCount, 
        StartParam, 
        MinDist, 
        MaxDist, 
        Curves[:3]
    )
    all_division_points.append(points)
    all_parameters.append(params)

# Flatten division points for output
DivisionPoints = [pt for curve_pts in all_division_points for pt in curve_pts]

# Step 2: Create vertical lines with 1m difference in domain per curve
all_vertical_lines = []
all_endpoints = []

height_domains = [
    (HeightMin1, HeightMax1),
    (HeightMin1 + 1, HeightMax1 + 1),
    (HeightMin1 + 2, HeightMax1 + 2)
]

for i, curve_points in enumerate(all_division_points):
    h_min, h_max = height_domains[i]
    lines, endpoints = create_vertical_lines(curve_points, h_min, h_max)
    all_vertical_lines.append(lines)
    all_endpoints.append(endpoints)

# Flatten vertical lines for output
VerticalLines = [line.ToNurbsCurve() for curve_lines in all_vertical_lines for line in curve_lines]

# Step 3: Create surface from endpoints (U = 20)
# Reorganize endpoints for proper surface creation
surface_points = []
for endpoints in all_endpoints:
    surface_points.extend(endpoints)

LoftSurface, loft_curves = create_surface_from_points(all_endpoints, 20)

# Step 4: Extend surface to XY plane
ExtendedSurface = extend_surface_to_xy_plane(LoftSurface)

# Step 5: Create Voronoi tessellation
VoronoiCells, voronoi_edges = create_voronoi_on_surface(
    ExtendedSurface if ExtendedSurface else LoftSurface, 
    surface_points
)

# Step 6: Offset Voronoi edges on surface
OffsetEdges = offset_edges_on_surface(
    voronoi_edges, 
    ExtendedSurface if ExtendedSurface else LoftSurface, 
    VoronoiOffset
)

# Convert edges to curves for output
OffsetEdges = [rg.LineCurve(edge) for edge in OffsetEdges]

# Step 7: Create surfaces between original and offset edges
OffsetSurfaces = create_surfaces_between_edges(voronoi_edges, 
    [rg.Line(e.PointAtStart, e.PointAtEnd) for e in OffsetEdges])

# Step 8: Extrude surfaces
ExtrudedSurfaces = extrude_surfaces(OffsetSurfaces, ExtrudeDistance)

# Step 9: Get 2/3 height points and connect to Voronoi vertices
flat_vertical_lines = [line for curve_lines in all_vertical_lines for line in curve_lines]
TwoThirdsPoints = get_two_thirds_points(flat_vertical_lines)

ConnectionLines = create_connection_lines(TwoThirdsPoints, VoronoiCells)
ConnectionLines = [rg.LineCurve(line) for line in ConnectionLines]

# Step 10: Create multipipe
Pipes = create_multipipe(
    [rg.Line(c.PointAtStart, c.PointAtEnd) for c in ConnectionLines], 
    PipeRadius
)
