"""按同材质、同绕序及全部交付顶点属性归并重合面，保留逐源面映射。"""

import hashlib

import numpy as np


def unique_surfaces(arrays):
    """不改坐标、法线或UV；反向面、不同材质和不同属性的面分别保留。"""
    triangles = arrays["triangles"]
    materials = arrays["material_indices"]
    channels = [key for key in ("vertices", "normals", "uv0", "uv1") if arrays.get(key) is not None and arrays[key].size]
    vertex_bytes = np.concatenate([np.ascontiguousarray(arrays[key]).view(np.uint8).reshape(len(arrays[key]), -1)
                                   for key in channels], axis=1)
    seen, keep, mapping = {}, [], np.empty(len(triangles), dtype=np.int32)
    for index, (face, slot) in enumerate(zip(triangles, materials)):
        corners = vertex_bytes[face]
        key = int(slot).to_bytes(4, "little") + min(np.roll(corners, shift, axis=0).tobytes() for shift in range(3))
        if key not in seen:
            seen[key] = len(keep)
            keep.append(index)
        mapping[index] = seen[key]
    keep = np.asarray(keep, dtype=np.int32)
    # 每个被归并源面均指向具有完全相同角点属性和绕序的保留面。
    digest = hashlib.sha256(b"".join(sorted(seen))).hexdigest()
    return keep, mapping, {"channels": channels, "source_triangles": len(triangles),
                           "render_triangles": len(keep), "coincident_faces_merged": len(triangles) - len(keep),
                           "oriented_surface_sha256": digest,
                           "policy": "同材质且全部角点属性逐字节一致，仅允许循环起点变化；反向面和属性差异均保留"}
