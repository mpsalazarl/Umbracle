import Rhino.Geometry as rg

# 1. Handle Branching Data
try:
    cell_curve = C[0]
    original_center = P[0]
except:
    cell_curve = C
    original_center = P

all_lines = []
kinetic_meshes = []
static_meshes = []

if isinstance(original_center, rg.Point3d) and cell_curve:
    
    # Create the dynamic center point
    center_pt = rg.Point3d(
        original_center.X + dX,
        original_center.Y + dY,
        original_center.Z + dZ
    )
    
    segments = cell_curve.DuplicateSegments()
    
    if segments:
        original_verts = [s.PointAtStart for s in segments]
        final_verts = []
        
        # Determine if we use Kinetic logic (6 sides) or Static logic (rest)
        is_hexagon = len(segments) == 6
        
        for i in range(len(original_verts)):
            p = original_verts[i]
            
            if is_hexagon:
                # Kinetic Logic: Move points 2, 4, 6
                if (i + 1) % 2 == 0:
                    vector = center_pt - p
                    final_verts.append(p + (vector * Factor))
                else:
                    final_verts.append(p)
            else:
                # Static Logic: All points stay at original boundary
                final_verts.append(p)
        
        # 2. Build the Geometry
        loop_verts = final_verts + [final_verts[0]]
        
        for j in range(len(loop_verts) - 1):
            p1 = loop_verts[j]
            p2 = loop_verts[j+1]
            
            # Lines
            all_lines.append(rg.Line(center_pt, p1).ToNurbsCurve())
            
            # Create Mesh Triangle
            m = rg.Mesh()
            m.Vertices.Add(center_pt)
            m.Vertices.Add(p1)
            m.Vertices.Add(p2)
            m.Faces.AddFace(0, 1, 2)
            
            if is_hexagon:
                kinetic_meshes.append(m)
            else:
                static_meshes.append(m)

# Final Outputs
a = all_lines
b = kinetic_meshes
c = center_pt
d = static_meshes