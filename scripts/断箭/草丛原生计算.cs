// 在独立批次工程中恢复TerrainData并调用原生细节接口，不加载原游戏或其他项目。
using System;
using System.IO;
using UnityEngine;
using UnityEditor;

public static class AirbaseDetailBatch
{
    [Serializable] public class Prototype
    {
        public float minWidth, maxWidth, minHeight, maxHeight, noiseSpread, density, alignToGround, positionJitter;
        public int noiseSeed;
        public Vector3 boundsLow, boundsHigh;
    }
    [Serializable] public class Config
    {
        public string input, output;
        public int resolution, heightResolution, patchSamples;
        public Vector3 size;
        public Prototype[] prototypes;
    }
    [Serializable] public class Report
    {
        public string unityVersion, status, rotationUnit = "弧度";
        public long count, densitySum;
        public long[] layerCounts = new long[6];
        public float[] minScaleXZ = new float[6], maxScaleXZ = new float[6], minScaleY = new float[6], maxScaleY = new float[6];
        public int fixtureResolution = 128, fixturePatchSamples = 64;
        public string layout = "BAGN头部: magic,version; 六个uint32计数; 按原型顺序float32(posX,posY,posZ,scaleXZ,scaleY,rotationY)";
    }
    static DetailPrototype[] MakePrototypes(Config config)
    {
        var result = new DetailPrototype[config.prototypes.Length];
        for (int i = 0; i < result.Length; i++)
        {
            var p = config.prototypes[i];
            var mesh = new Mesh();
            var a = p.boundsLow; var b = p.boundsHigh;
            mesh.vertices = new[] {a, new Vector3(b.x,a.y,a.z), new Vector3(a.x,b.y,b.z), b};
            mesh.triangles = new[] {0,1,2, 1,3,2};
            mesh.uv = new[] {Vector2.zero,Vector2.right,Vector2.up,Vector2.one};
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            AssetDatabase.CreateAsset(mesh, "Assets/原型包围盒-" + i + ".asset");
            var go = new GameObject("仅供原生分布计算-" + i);
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            var renderer = go.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = AssetDatabase.GetBuiltinExtraResource<Material>("Default-Material.mat");
            var prefab = PrefabUtility.SaveAsPrefabAsset(go, "Assets/原型包围盒-" + i + ".prefab");
            UnityEngine.Object.DestroyImmediate(go);
            result[i] = new DetailPrototype {
                prototype = prefab, usePrototypeMesh = true, renderMode = DetailRenderMode.VertexLit,
                minWidth = p.minWidth, maxWidth = p.maxWidth, minHeight = p.minHeight, maxHeight = p.maxHeight,
                noiseSeed = p.noiseSeed, noiseSpread = p.noiseSpread, density = p.density,
                alignToGround = p.alignToGround, positionJitter = p.positionJitter,
                useInstancing = false, useDensityScaling = true,
                healthyColor = Color.white, dryColor = Color.white
            };
            string error;
            if (!result[i].Validate(out error)) throw new Exception("原型参数无效: " + error);
        }
        return result;
    }
    static void Fixture(DetailPrototype[] prototypes)
    {
        var td = new TerrainData {heightmapResolution = 33, size = new Vector3(128,64,128)};
        td.SetDetailResolution(128,64);
        td.SetDetailScatterMode(DetailScatterMode.InstanceCountMode);
        td.detailPrototypes = prototypes;
        for (int layer = 0; layer < 6; layer++)
        {
            var values = new int[128,128];
            // 不对称位置及计数可区分补丁次序、补丁内方向、镜像和图层顺序。
            for (int z = 0; z < 128; z += 19)
                for (int x = 0; x < 128; x += 23)
                    values[(z + layer*3)%128,(x + layer*5)%128] = 1 + (x+z+layer)%3;
            td.SetDetailLayer(0,0,layer,values);
        }
        AssetDatabase.CreateAsset(td,"Assets/坐标夹具.asset");
    }
    public static void Run()
    {
        try
        {
            string configPath = null;
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length-1; i++) if (args[i] == "-airbaseConfig") configPath = args[i+1];
            if (configPath == null) throw new Exception("缺少本轮配置");
            var config = JsonUtility.FromJson<Config>(File.ReadAllText(configPath));
            Directory.CreateDirectory(config.output);
            EditorSettings.serializationMode = SerializationMode.ForceBinary;
            var prototypes = MakePrototypes(config);
            Fixture(prototypes);
            var terrain = new TerrainData {heightmapResolution = config.heightResolution, size = config.size};
            var bytes = File.ReadAllBytes(Path.Combine(config.input,"高度.i16"));
            if (bytes.Length != config.heightResolution*config.heightResolution*2) throw new Exception("高度长度错误");
            var heights = new float[config.heightResolution,config.heightResolution];
            for (int z = 0; z < config.heightResolution; z++)
                for (int x = 0; x < config.heightResolution; x++)
                    heights[z,x] = BitConverter.ToInt16(bytes,2*(z*config.heightResolution+x))/32766f;
            terrain.SetHeights(0,0,heights);
            terrain.SetDetailResolution(config.resolution,config.patchSamples);
            terrain.SetDetailScatterMode(DetailScatterMode.InstanceCountMode);
            terrain.detailPrototypes = prototypes;
            var report = new Report {unityVersion = Application.unityVersion, status = "原生派生完成，待序列化和分页独立核验"};
            for (int layer = 0; layer < 6; layer++)
            {
                bytes = File.ReadAllBytes(Path.Combine(config.input,"密度-"+layer+".u8"));
                if (bytes.Length != config.resolution*config.resolution) throw new Exception("密度长度错误");
                var values = new int[config.resolution,config.resolution];
                for (int z = 0; z < config.resolution; z++)
                    for (int x = 0; x < config.resolution; x++) {values[z,x]=bytes[z*config.resolution+x]; report.densitySum+=values[z,x];}
                terrain.SetDetailLayer(0,0,layer,values);
                var back = terrain.GetDetailLayer(0,0,config.resolution,config.resolution,layer);
                for (int z = 0; z < config.resolution; z++)
                    for (int x = 0; x < config.resolution; x++) if (values[z,x] != back[z,x]) throw new Exception("密度API回读不一致");
                report.minScaleXZ[layer] = report.minScaleY[layer] = float.PositiveInfinity;
            }
            AssetDatabase.CreateAsset(terrain,"Assets/完整草丛地形.asset");
            AssetDatabase.SaveAssets();
            int patches = config.resolution/config.patchSamples;
            for (int z = 0; z < patches; z++) for (int x = 0; x < patches; x++)
            {
                var transforms = new DetailInstanceTransform[6][];
                for (int layer = 0; layer < 6; layer++)
                    transforms[layer] = terrain.ComputeDetailInstanceTransforms(x,z,layer,1f,out var bounds);
                using (var file = new BinaryWriter(File.Open(Path.Combine(config.output,$"{x:00}-{z:00}.bin"),FileMode.CreateNew)))
                {
                    file.Write(new byte[] {66,65,71,78}); file.Write(1);
                    foreach (var values in transforms) file.Write(values.Length);
                    for (int layer = 0; layer < 6; layer++)
                    {
                        report.layerCounts[layer] += transforms[layer].Length;
                        report.count += transforms[layer].Length;
                        foreach (var t in transforms[layer])
                        {
                            file.Write(t.posX); file.Write(t.posY); file.Write(t.posZ);
                            file.Write(t.scaleXZ); file.Write(t.scaleY); file.Write(t.rotationY);
                            report.minScaleXZ[layer] = Mathf.Min(report.minScaleXZ[layer],t.scaleXZ);
                            report.maxScaleXZ[layer] = Mathf.Max(report.maxScaleXZ[layer],t.scaleXZ);
                            report.minScaleY[layer] = Mathf.Min(report.minScaleY[layer],t.scaleY);
                            report.maxScaleY[layer] = Mathf.Max(report.maxScaleY[layer],t.scaleY);
                        }
                    }
                }
                if ((z*patches+x)%32 == 0) File.WriteAllText(Path.Combine(config.output,"原生进度.json"),$"{{\"patch\":{z*patches+x},\"total\":{patches*patches},\"count\":{report.count}}}");
            }
            if (report.count != report.densitySum) throw new Exception("InstanceCountMode原生输出数量未守恒");
            File.WriteAllText(Path.Combine(config.output,"原生回执.json"),JsonUtility.ToJson(report,true));
            EditorApplication.Exit(0);
        }
        catch (Exception error) { Debug.LogException(error); EditorApplication.Exit(1); }
    }
}
