"""记录原型真实Shader身份和材质字段，支持固定正常静态状态的透明语义选择。"""

import argparse
import json
from pathlib import Path

from 导出全图原型 import FullMapExtractor
from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


def audit(directory, revision=None):
    journal = Journal(directory)
    journal.update(phase="A1双层资产样本", operation="核对材质Shader身份及正常静态状态")
    game = Path(json.loads((ROOT / "local-data/断箭/本地配置.json").read_text("utf-8"))["game_directory"])
    worker = FullMapExtractor(directory, game)
    result, shaders = {}, {}
    if revision and any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in revision):
        raise ValueError("无效材质修订名")
    output = directory / "来源" / (f"材质语义-{revision}.json" if revision else "材质语义.json")
    if output.exists():
        raise FileExistsError("材质语义快照已存在，拒绝覆盖")
    try:
        worker.load("globalgamemanagers.assets")
        worker.load("resources.assets")
        worker.open_bundle(next((game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("mapresources_assets_all_*.bundle")))
        prototypes = json.loads((directory / "来源/全量原型来源.json").read_text("utf-8"))
        for row in prototypes:
            path = Path(row["exported"]["scene"])
            data = json.loads(path.read_text("utf-8"))
            for key, material in data["materials"].items():
                if key in result:
                    result[key]["asset_ids"].append(row["asset_id"])
                    continue
                journal.update(material=material["name"], source_key=key)
                node = material["source_node"]
                af = worker.env.cabs.get(node.lower()) or worker.env.cabs.get(node)
                if af is None:
                    if any(n["name"] == node for n in worker.bundle.nodes):
                        worker.load(node)
                    else:
                        worker.load_external(node)
                    af = worker.env.cabs.get(node.lower()) or worker.env.cabs.get(node)
                shader = worker.deref(material["raw"]["m_Shader"], af)
                shader_key = f"{shader.assets_file.name}:{shader.path_id}"
                if shader_key not in shaders:
                    tree = shader.read_typetree()
                    shaders[shader_key] = {"name": tree["m_ParsedForm"]["m_Name"], "node": shader.assets_file.name,
                                           "id": shader.path_id, "raw_sha256": __import__("hashlib").sha256(shader.get_raw_data()).hexdigest()}
                fields = dict(material["raw"]["m_SavedProperties"]["m_Floats"])
                building_v2 = shaders[shader_key]["name"] == "Shader Graphs/Building_shader_V2"
                blood_tree = shaders[shader_key]["name"] == "Shader Graphs/BloodTree"
                cutoff_property = "_AlphaClip" if building_v2 or blood_tree else "_AlphaCutoff"
                cutoff = fields.get(cutoff_property, material.get("alpha_cutoff", .5))
                result[key] = {"name": material["name"], "source_node": node, "source_id": material["source_id"],
                               "shader": shaders[shader_key], "asset_ids": [row["asset_id"]],
                               "source_declared_alpha": material["alpha_mode"], "static_day_alpha": material["alpha_mode"],
                               "static_alpha_cutoff": cutoff, "alpha_cutoff_property": cutoff_property,
                               "normal_static_damage_fields": {name: fields.get(name) for name in ["_Low", "_High", "_Deep"]},
                               "policy": "保留源Alpha裁切；Building_shader_V2使用原_AlphaClip阈值。建筑三角黑块由重合面自遮挡复现，不再用OPAQUE猜测修复" if building_v2 else
                                         "BloodTree使用源_AlphaClip；源反射偏移48字节对应正常状态阈值，BurnClip位于196字节，仅毁伤时混入" if blood_tree else
                                         "按源透明裁切或混合规则，仍需实际画面核对"}
        atomic_json(output, {"schema": 1, "materials": result, "shaders": shaders,
                             "scope": "不声明复刻原Shader；仅为计划内正常白天静态状态的材质转换", "source_manifest": fingerprint(directory / "输入清单.json")})
        journal.close(status="材质语义审计完成，继续修复样本", materials=len(result), static_day_overrides=sum(r["static_day_alpha"] != r["source_declared_alpha"] for r in result.values()))
        print(json.dumps({"materials": len(result), "static_day_overrides": [r["name"] for r in result.values() if r["static_day_alpha"] != r["source_declared_alpha"]]}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status="材质语义审计失败", error=repr(error))
        raise
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--revision")
    args = parser.parse_args()
    directory = (ROOT / args.directory).resolve()
    directory.relative_to((ROOT / "local-data/断箭/批次").resolve())
    audit(directory, args.revision)
