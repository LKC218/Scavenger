"""从离线恢复程序集生成类型树，使用UnityPy标准对象头校正对齐。"""

import json
from pathlib import Path
import subprocess
import sys

from 地图执行记录 import ROOT, atomic_json, fingerprint


TOOL = ROOT / "local-data/tools/TypeTreeGeneratorAPI/0.0.10/app"


def restore_assemblies(game, output, log, journal):
    if output.exists():
        receipt = output.parent / "程序集恢复凭证.json"
        if not receipt.exists():
            raise ValueError("已有程序集目录缺少冻结凭证，拒绝盲目续用")
        identity = json.loads(receipt.read_text("utf-8"))
        for name, expected in identity["inputs"].items():
            if fingerprint(game / name)["sha256"] != expected["sha256"]:
                raise ValueError("恢复程序集输入变化")
        for name, expected in identity["outputs"].items():
            if fingerprint(output / name)["sha256"] != expected["sha256"]:
                raise ValueError("恢复程序集输出变化")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    executable = ROOT / "local-data/tools/Cpp2IL/2022.1.0-pre-release.21/net472/Cpp2IL.exe"
    arguments = [str(executable), "--force-binary-path", str(game / "GameAssembly.dll"),
                 "--force-metadata-path", str(game / "BrokenArrow_Data/il2cpp_data/Metadata/global-metadata.dat"),
                 "--force-unity-version", "6000.3.24f1", "--use-processor", "attributeanalyzer",
                 "--output-as", "dll_empty", "--output-to", str(output)]
    journal.update(operation="离线恢复组件序列化属性")
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(arguments, cwd=log.parent, stdout=stream, stderr=subprocess.STDOUT, check=True)
    atomic_json(output.parent / "程序集恢复凭证.json", {
        "inputs": {name: fingerprint(game / name) for name in ["GameAssembly.dll", "BrokenArrow_Data/il2cpp_data/Metadata/global-metadata.dat"]},
        "executable": fingerprint(executable), "arguments": arguments[1:],
        "outputs": {path.name: fingerprint(path) for path in sorted(output.glob("*.dll"))}})


def corrected_generator(assemblies):
    sys.path.insert(0, str(TOOL))
    from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
    from UnityPy.helpers.Tpk import get_typetree_node
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    from UnityPy.enums import ClassIDType

    class CorrectedGenerator(TypeTreeGenerator):
        def get_nodes_up(self, assembly, fullname):
            generated = super().get_nodes_up(assembly, fullname)
            standard = get_typetree_node(ClassIDType.MonoBehaviour, (6000, 3, 24, 1))
            header_names = {child.m_Name for child in standard.m_Children}
            # 0.0.10生成头缺少m_Enabled对齐标记；使用已核对的原生头，保留生成的有效载荷。
            children = list(standard.m_Children) + [child for child in generated.m_Children if child.m_Name not in header_names]
            return TypeTreeNode(0, generated.m_Type, "Base", 0, 0, children, m_MetaFlag=standard.m_MetaFlag)

    generator = CorrectedGenerator("6000.3.24f1")
    generator.load_local_dll_folder(str(assemblies))
    return generator


def read_component(obj):
    header = obj.parse_monobehaviour_head()
    tree = obj.read_typetree()
    expected = {"m_FileID": header.m_Script.file_id, "m_PathID": header.m_Script.path_id}
    if tree["m_Script"] != expected or tree["m_GameObject"]["m_PathID"] != header.m_GameObject.path_id:
        raise ValueError(f"组件头与原生头不符：{obj.path_id}")
    return tree
