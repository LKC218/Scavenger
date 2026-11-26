// 在隔离的Unity后台项目中读取原资源包，以原Avatar求解人形动画。
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

public static class 原生动作采样
{
    [Serializable] public class 预制体 { public string name; public string path; }
    [Serializable] public class 配置 { public string bundle; public string output; public string[] dependencies; public 预制体[] prefabs; }
    [Serializable] public class 帧 { public float time; public float[] trs; }
    [Serializable] public class 网格核验 { public int frame; public float[] vertices; }
    [Serializable] public class 动作 { public string name; public float duration; public float sampleRate; public bool humanMotion; public bool looping; public 帧[] frames; public 网格核验[] checks; }
    [Serializable] public class 角色 { public string name; public string prefab; public string avatar; public bool validAvatar; public bool humanAvatar; public string[] paths; public 动作[] clips; }

    public static void Run()
    {
        try
        {
            string project = Directory.GetParent(Application.dataPath).FullName;
            var config = JsonUtility.FromJson<配置>(File.ReadAllText(Path.Combine(project, "采样配置.json")));
            var dependencies = new List<AssetBundle>();
            foreach (string path in config.dependencies ?? Array.Empty<string>())
            {
                var dependency = AssetBundle.LoadFromFile(path);
                if (!dependency) throw new Exception("依赖资源包加载失败：" + Path.GetFileName(path));
                dependencies.Add(dependency);
            }
            var bundle = AssetBundle.LoadFromFile(config.bundle);
            if (!bundle) throw new Exception("原资源包加载失败");
            using var file = new FileStream(config.output, FileMode.CreateNew);
            using Stream payload = config.output.EndsWith(".gz", StringComparison.OrdinalIgnoreCase)
                ? new GZipStream(file, System.IO.Compression.CompressionLevel.Fastest, true) : file;
            using var writer = new StreamWriter(payload);
            writer.Write("{\"unity\":\"" + Application.unityVersion + "\",\"roles\":[");
            var targets = config.prefabs ?? new[] { "Walker", "Runner" }.Select(role => new 预制体 {
                name = role, path = "Assets/Resources_moved/ModelPrefabs/Zombie/" + role + "/ZOM_" + role + "1.prefab"
            }).ToArray();
            for (int roleIndex = 0; roleIndex < targets.Length; roleIndex++)
            {
                var target = targets[roleIndex];
                string role = target.name, source = target.path;
                var prefab = bundle.LoadAsset<GameObject>(source);
                if (!prefab) throw new Exception("预制体加载失败：" + role);
                var go = UnityEngine.Object.Instantiate(prefab);
                var animator = go.GetComponentInChildren<Animator>(true);
                if (!animator || !animator.avatar || !animator.avatar.isValid) throw new Exception("Avatar无效：" + role);
                if (!animator.runtimeAnimatorController) throw new Exception("缺少动画控制器：" + role);
                animator.fireEvents = false;
                var clips = animator.runtimeAnimatorController.animationClips.Distinct().OrderBy(c => c.name).ToArray();
                var transforms = go.GetComponentsInChildren<Transform>(true);
                var initialT = transforms.Select(t => t.localPosition).ToArray();
                var initialQ = transforms.Select(t => t.localRotation).ToArray();
                var initialS = transforms.Select(t => t.localScale).ToArray();
                var renderer = go.GetComponentsInChildren<SkinnedMeshRenderer>(true).First(r => r.name == "lod_0");
                var record = new 角色 { name = role, prefab = source, avatar = animator.avatar.name,
                    validAvatar = animator.avatar.isValid, humanAvatar = animator.avatar.isHuman,
                    paths = transforms.Select(t => AnimationUtility.CalculateTransformPath(t, go.transform)).ToArray() };
                var animations = new List<动作>();
                foreach (var clip in clips)
                {
                    for (int i = 0; i < transforms.Length; i++)
                    {
                        transforms[i].localPosition = initialT[i];
                        transforms[i].localRotation = initialQ[i];
                        transforms[i].localScale = initialS[i];
                    }
                    var graph = PlayableGraph.Create(role + "/" + clip.name);
                    graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                    var playable = AnimationClipPlayable.Create(graph, clip);
                    playable.SetApplyFootIK(false);
                    playable.SetApplyPlayableIK(false);
                    var output = AnimationPlayableOutput.Create(graph, "原生采样", animator);
                    output.SetSourcePlayable(playable);
                    animator.applyRootMotion = false;
                    animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
                    graph.Play();
                    int count = Mathf.CeilToInt(clip.length * clip.frameRate) + 1;
                    var frames = new 帧[count];
                    var checks = new List<网格核验>();
                    for (int frame = 0; frame < count; frame++)
                    {
                        float time = Mathf.Min(frame / clip.frameRate, clip.length);
                        playable.SetTime(time);
                        graph.Evaluate(0);
                        var values = new float[transforms.Length * 10];
                        for (int index = 0; index < transforms.Length; index++)
                        {
                            var tr = transforms[index]; int k = index * 10;
                            var t = tr.localPosition; var q = tr.localRotation; var s = tr.localScale;
                            values[k] = t.x; values[k+1] = t.y; values[k+2] = t.z;
                            values[k+3] = q.x; values[k+4] = q.y; values[k+5] = q.z; values[k+6] = q.w;
                            values[k+7] = s.x; values[k+8] = s.y; values[k+9] = s.z;
                        }
                        frames[frame] = new 帧 { time = time, trs = values };
                        if (frame == 0 || frame == count / 2 || frame == count - 1)
                        {
                            var baked = new Mesh(); renderer.BakeMesh(baked, false);
                            checks.Add(new 网格核验 { frame = frame, vertices = baked.vertices.SelectMany(v => new[] { v.x, v.y, v.z }).ToArray() });
                            UnityEngine.Object.DestroyImmediate(baked);
                        }
                    }
                    graph.Destroy();
                    animations.Add(new 动作 { name = clip.name, duration = clip.length, sampleRate = clip.frameRate,
                        humanMotion = clip.humanMotion, looping = clip.isLooping, frames = frames, checks = checks.ToArray() });
                    Debug.Log("原生采样完成：" + role + "/" + clip.name + "，帧数" + count);
                }
                record.clips = animations.ToArray();
                if (roleIndex != 0) writer.Write(',');
                writer.Write(JsonUtility.ToJson(record));
                UnityEngine.Object.DestroyImmediate(go);
            }
            writer.Write("]}");
            // EditorApplication.Exit 会直接结束进程，必须先写完压缩文件尾。
            writer.Dispose();
            bundle.Unload(true);
            foreach (var dependency in dependencies) dependency.Unload(true);
            EditorApplication.Exit(0);
        }
        catch (Exception error)
        {
            Debug.LogException(error);
            EditorApplication.Exit(1);
        }
    }
}
