"""
Umbracle - Area Phase
Grasshopper Python Component for Procedural Geometry Generation

Generates three 70sqm circles arranged in an equilateral triangle with:
- Controllable point population (A, B, C sliders)
- Radial circles (2-5m random radius) from each point
- Boundary extraction and smoothing with B-spline fillets

Input (from Grasshopper sliders):
  - triangle_side: Side length of equilateral triangle (controls circle overlap)
  - point_count_A, point_count_B, point_count_C: Number of points in each circle
  - seed: Random seed for reproducibility (optional)

Output:
  - circles: The three main region circles
  - points_list: All generated points grouped by circle
  - radial_circles: All small circles drawn from points
  - boundary_curves: Extracted boundary segments within regions
  - smooth_curves: Final curves with B-spline fillets
"""

import Rhino.Geometry as rg
import scriptcontext as sc
import random
import math
from System.Collections.Generic import List

# Constants
CIRCLE_AREA = 70.0  # Square meters
CIRCLE_RADIUS = math.sqrt(CIRCLE_AREA / math.pi)  # ~4.72m
RADIAL_CIRCLE_MIN_RADIUS = 2.0  # meters
RADIAL_CIRCLE_MAX_RADIUS = 5.0  # meters
FILLET_DEGREE = 3  # B-spline degree for fillets
INTERSECTION_TOLERANCE = 1e-6


def equilateral_triangle_vertices(side_length, center=None):
    """
    Calculate the three vertices of an equilateral triangle.
    
    Args:
        side_length: Length of the triangle side (meters)
        center: Center point (Rhino.Geometry.Point3d), defaults to origin
        
    Returns:
        List of three Point3d vertices
    """
    if center is None:
        center = rg.Point3d(0, 0, 0)
    
    # Height of equilateral triangle
    height = side_length * math.sqrt(3) / 2
    
    # Vertices relative to center
    v0 = rg.Point3d(center.X, center.Y + 2 * height / 3, center.Z)
    v1 = rg.Point3d(center.X - side_length / 2, center.Y - height / 3, center.Z)
    v2 = rg.Point3d(center.X + side_length / 2, center.Y - height / 3, center.Z)
    
    return [v0, v1, v2]


def create_region_circles(vertices):
    """
    Create three circles of CIRCLE_AREA centered at triangle vertices.
    
    Args:
        vertices: List of three Point3d vertices
        
    Returns:
        List of three Circle objects
    """
    circles = []
    for vertex in vertices:
        circle = rg.Circle(rg.Plane(vertex, rg.Vector3d.ZAxis), CIRCLE_RADIUS)
        circles.append(circle)
    return circles


def populate_points_in_circle(circle, count, seed_offset=0):
    """
    Generate random points uniformly distributed within a circle.
    
    Args:
        circle: Rhino.Geometry.Circle object
        count: Number of points to generate
        seed_offset: Offset for random seed (ensures different sequences per circle)
        
    Returns:
        List of Point3d objects
    """
    points = []
    random.seed(hash((circle.Center.X, circle.Center.Y, seed_offset)) % 2**32)
    
    for _ in range(count):
        # Use polar coordinates with sqrt(random) for uniform distribution
        r = circle.Radius * math.sqrt(random.random())
        theta = random.random() * 2 * math.pi
        
        x = circle.Center.X + r * math.cos(theta)
        y = circle.Center.Y + r * math.sin(theta)
        point = rg.Point3d(x, y, circle.Center.Z)
        points.append(point)
    
    return points


def create_radial_circles_from_points(points, seed_base=0):
    """
    Create circles (2-5m radius) centered at each point.
    
    Args:
        points: List of Point3d objects
        seed_base: Base seed for random radius generation
        
    Returns:
        List of Circle objects
    """
    radial_circles = []
    random.seed(seed_base)
    
    for i, point in enumerate(points):
        # Random radius between 2-5m
        radius = RADIAL_CIRCLE_MIN_RADIUS + random.random() * (RADIAL_CIRCLE_MAX_RADIUS - RADIAL_CIRCLE_MIN_RADIUS)
        circle = rg.Circle(rg.Plane(point, rg.Vector3d.ZAxis), radius)
        radial_circles.append(circle)
    
    return radial_circles


def extract_boundary_segments(radial_circles, region_circles, region_index):
    """
    Extract arc segments of radial circles that fall within their circular region.
    
    Args:
        radial_circles: List of Circle objects (generated from points)
        region_circles: List of Circle objects (the three main regions)
        region_index: Index (0, 1, or 2) indicating which region these radial circles belong to
        
    Returns:
        List of Curve objects representing boundary segments
    """
    boundary_curves = []
    region_circle = region_circles[region_index]
    
    for radial_circle in radial_circles:
        try:
            # Find intersection points between radial circle and region circle boundary
            intersections = rg.Intersect.Intersection.CircleCircle(
                region_circle,
                radial_circle
            )
            
            if intersections is None or len(intersections) == 0:
                # No intersection - radial circle might be entirely inside or outside
                # Check if radial circle center is inside region
                dist_to_region_center = radial_circle.Center.DistanceTo(region_circle.Center)
                if dist_to_region_center < region_circle.Radius:
                    # Center is inside - include partial arc
                    try:
                        arc_curve = rg.ArcCurve(rg.Arc(radial_circle, math.pi))
                        boundary_curves.append(arc_curve)
                    except:
                        pass
                continue
            
            if len(intersections) >= 2:
                # Two intersection points - extract arc segment between them
                int_pt1 = intersections[0]
                int_pt2 = intersections[1]
                
                try:
                    # Create arc from radial circle between intersection points
                    arc = rg.Arc(radial_circle, int_pt1, int_pt2)
                    if arc.IsValid and arc.Radius > 0:
                        # Convert to curve
                        arc_curve = rg.ArcCurve(arc)
                        boundary_curves.append(arc_curve)
                except:
                    # If arc creation fails, try the other direction
                    try:
                        arc = rg.Arc(radial_circle, int_pt2, int_pt1)
                        if arc.IsValid and arc.Radius > 0:
                            arc_curve = rg.ArcCurve(arc)
                            boundary_curves.append(arc_curve)
                    except:
                        pass
            
            elif len(intersections) == 1:
                # Tangent case - include small arc around tangent point
                try:
                    # Create small arc around the tangent point
                    tangent_pt = intersections[0]
                    # Get angle to tangent point
                    vec_to_tangent = tangent_pt - radial_circle.Center
                    angle = math.atan2(vec_to_tangent.Y, vec_to_tangent.X)
                    
                    # Create arc spanning around the tangent
                    arc = rg.Arc(
                        radial_circle,
                        angle - 0.3,
                        angle + 0.3
                    )
                    if arc.IsValid:
                        arc_curve = rg.ArcCurve(arc)
                        boundary_curves.append(arc_curve)
                except:
                    pass
                    
        except Exception as ex:
            # Skip this circle if intersection fails
            pass
    
    return boundary_curves


def get_arc_endpoints(arc):
    """Get start and end points of an arc."""
    return (arc.StartPoint, arc.EndPoint)


def apply_bspline_fillet(pt1, pt2, adjacent_arc1=None, adjacent_arc2=None):
    """
    Create a B-spline fillet curve to smoothly connect two arc segments.
    
    Args:
        pt1, pt2: Connection points (Point3d)
        adjacent_arc1, adjacent_arc2: Optional adjacent arcs for tangent continuity
        
    Returns:
        BSplineCurve object representing the fillet
    """
    # Create control points for the fillet
    control_pts = List[rg.Point3d]()
    control_pts.Add(pt1)
    
    # Add midpoint(s) for smooth transition
    mid = rg.Point3d(
        (pt1.X + pt2.X) / 2,
        (pt1.Y + pt2.Y) / 2,
        (pt1.Z + pt2.Z) / 2
    )
    control_pts.Add(mid)
    control_pts.Add(pt2)
    
    # Create B-spline curve through control points
    try:
        curve = rg.BSplineCurve.CreateInterpolatedCurve(
            control_pts,
            3,  # degree
            rg.CurveKnotStyle.Clamped
        )
        return curve
    except:
        # Fallback: create simple line segment
        return rg.LineCurve(pt1, pt2)


def assemble_final_curves(boundary_curves_input):
    """
    Assemble boundary curves and create fillets to form continuous smooth curves.
    
    Args:
        boundary_curves_input: List of Curve objects representing boundary segments
        
    Returns:
        Tuple of (assembled_curves, fillet_curves)
    """
    assembled_curves = list(boundary_curves_input)  # Return boundary curves as-is
    fillet_curves = []
    
    if len(assembled_curves) < 2:
        return (assembled_curves, fillet_curves)
    
    # Create fillets between consecutive curves
    for i in range(len(assembled_curves)):
        current_curve = assembled_curves[i]
        next_curve = assembled_curves[(i + 1) % len(assembled_curves)]
        
        try:
            # Get endpoints
            current_end = current_curve.PointAtEnd
            next_start = next_curve.PointAtStart
            
            # Calculate distance between curves
            dist = current_end.DistanceTo(next_start)
            
            # If curves don't connect, create fillet
            if dist > INTERSECTION_TOLERANCE:
                fillet = apply_bspline_fillet(current_end, next_start, current_curve, next_curve)
                if fillet is not None:
                    fillet_curves.append(fillet)
        except:
            pass
    
    return (assembled_curves, fillet_curves)


def main(triangle_side, point_count_A, point_count_B, point_count_C, seed=None):
    """
    Main execution function for Grasshopper component.
    
    Args:
        triangle_side: Side length of equilateral triangle (meters)
        point_count_A, point_count_B, point_count_C: Point counts per circle
        seed: Random seed (optional)
        
    Returns:
        Dictionary with output geometry
    """
    # Input validation with defaults
    if triangle_side is None:
        triangle_side = 50.0
    if point_count_A is None:
        point_count_A = 5
    if point_count_B is None:
        point_count_B = 5
    if point_count_C is None:
        point_count_C = 5
    
    # Ensure positive values
    triangle_side = max(1.0, float(triangle_side))
    point_count_A = max(1, int(point_count_A))
    point_count_B = max(1, int(point_count_B))
    point_count_C = max(1, int(point_count_C))
    
    if seed is not None:
        random.seed(seed)
    
    # Step 1: Create equilateral triangle vertices
    vertices = equilateral_triangle_vertices(triangle_side)
    
    # Step 2: Create three region circles
    region_circles = create_region_circles(vertices)
    
    # Step 3: Populate points in each circle
    all_points = []
    points_A = populate_points_in_circle(region_circles[0], point_count_A, seed_offset=0)
    points_B = populate_points_in_circle(region_circles[1], point_count_B, seed_offset=1)
    points_C = populate_points_in_circle(region_circles[2], point_count_C, seed_offset=2)
    
    all_points = [points_A, points_B, points_C]
    
    # Step 4: Create radial circles from all points
    radial_circles_all = []
    for i, points in enumerate(all_points):
        radial_circles = create_radial_circles_from_points(points, seed_base=i * 1000)
        radial_circles_all.append(radial_circles)
    
    # Step 5: Extract boundary segments for each region
    all_boundary_curves = []
    for i, radial_circles in enumerate(radial_circles_all):
        boundary_curves = extract_boundary_segments(radial_circles, region_circles, i)
        all_boundary_curves.extend(boundary_curves)
    
    # Step 6: Assemble and apply fillets
    assembled_curves, fillet_curves = assemble_final_curves(all_boundary_curves)
    
    # Convert circles to curves for output
    region_curves = [rg.ArcCurve(rg.Arc(c, math.pi * 2)) for c in region_circles]
    
    # Flatten radial circles for output
    flat_radial_circles = []
    for rc_list in radial_circles_all:
        flat_radial_circles.extend(rc_list)
    
    radial_curves = [rg.ArcCurve(rg.Arc(c, math.pi * 2)) for c in flat_radial_circles]
    
    # Combine boundary and fillet curves
    all_smooth_curves = list(assembled_curves) + list(fillet_curves)
    
    # Return output dictionary
    output = {
        'region_circles': region_curves,
        'points_A': points_A,
        'points_B': points_B,
        'points_C': points_C,
        'radial_circles': radial_curves,
        'boundary_arcs': assembled_curves,
        'fillet_curves': fillet_curves,
        'all_smooth_curves': all_smooth_curves
    }
    
    return output


# Grasshopper integration
# This section runs when the component executes in Grasshopper
if __name__ == "__main__":
    try:
        # Validate and provide defaults for Grasshopper inputs
        ts = triangle_side if 'triangle_side' in dir() else None
        pcA = point_count_A if 'point_count_A' in dir() else None
        pcB = point_count_B if 'point_count_B' in dir() else None
        pcC = point_count_C if 'point_count_C' in dir() else None
        s = seed if 'seed' in dir() else None
        
        # Call main function with Grasshopper inputs
        result = main(ts, pcA, pcB, pcC, seed=s)
        
        # Assign to Grasshopper output variables
        circles = result['region_circles']
        points_A = result['points_A']
        points_B = result['points_B']
        points_C = result['points_C']
        all_points = points_A + points_B + points_C  # Combined point list
        radial_circles = result['radial_circles']
        boundary_curves = result['boundary_arcs']
        smooth_curves = result['all_smooth_curves']
        
    except Exception as e:
        print("Error: {}".format(str(e)))
        import traceback
        traceback.print_exc()
