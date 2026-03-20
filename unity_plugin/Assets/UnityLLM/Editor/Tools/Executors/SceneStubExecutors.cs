using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEditor.Events;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.Events;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Backend;

#nullable enable
namespace UnityLLM.Editor.Tools.Executors
{
    public sealed class SceneStubExecutors : IToolExecutor
    {
        private static readonly List<string> _recentCompilerMessages = new List<string>();
        private static readonly object CompilerMessagesLock = new object();

        static SceneStubExecutors()
        {
            CompilationPipeline.assemblyCompilationFinished += OnAssemblyCompilationFinished;
        }

        private static void OnAssemblyCompilationFinished(string assemblyPath, CompilerMessage[] messages)
        {
            if (messages == null || messages.Length == 0) return;
            lock (CompilerMessagesLock)
            {
                foreach (var m in messages)
                {
                    var kind = m.type.ToString();
                    _recentCompilerMessages.Add($"{kind}: {m.file}:{m.line}:{m.column}: {m.message}");
                }
                if (_recentCompilerMessages.Count > 5000)
                {
                    _recentCompilerMessages.RemoveRange(0, _recentCompilerMessages.Count - 5000);
                }
            }
        }

        public bool CanExecute(string toolName)
        {
            if (string.IsNullOrWhiteSpace(toolName))
                return false;

            return toolName switch
            {
                "open_scene" => true,
                "save_scene" => true,
                "get_scene_hierarchy" => true,
                "create_game_object" => true,
                "delete_game_object" => true,
                "add_component" => true,
                "remove_component" => true,
                "set_component_property" => true,
                "connect_ui_event" => true,
                "collect_compile_errors" => true,
                "lint_file" => true,
                "run_unity_editor_tests" => true,
                _ => false
            };
        }

        public PendingTimelineRecord Execute(ToolCall toolCall)
        {
            if (toolCall == null)
                throw new ArgumentNullException(nameof(toolCall));

            var args = toolCall.Arguments ?? new Dictionary<string, JsonElement>();
            ValidateRequired(toolCall.ToolName, args);

            try
            {
                return toolCall.ToolName switch
                {
                    "open_scene" => ExecuteOpenScene(toolCall),
                    "save_scene" => ExecuteSaveScene(toolCall),
                    "get_scene_hierarchy" => ExecuteGetSceneHierarchy(toolCall),
                    "create_game_object" => ExecuteCreateGameObject(toolCall),
                    "delete_game_object" => ExecuteDeleteGameObject(toolCall),
                    "add_component" => ExecuteAddComponent(toolCall),
                    "remove_component" => ExecuteRemoveComponent(toolCall),
                    "set_component_property" => ExecuteSetComponentProperty(toolCall),
                    "connect_ui_event" => ExecuteConnectUiEvent(toolCall),
                    "collect_compile_errors" => ExecuteCollectCompileErrors(toolCall),
                    "lint_file" => ExecuteLintFile(toolCall),
                    "run_unity_editor_tests" => ExecuteRunUnityEditorTests(toolCall),
                    _ => throw new InvalidOperationException($"Unsupported scene tool: {toolCall.ToolName}")
                };
            }
            catch (Exception ex)
            {
                return BuildFailure(toolCall, ex.Message);
            }
        }

        private static void ValidateRequired(string toolName, Dictionary<string, JsonElement> args)
        {
            switch (toolName)
            {
                case "open_scene":
                    RequireString(args, "scene_path");
                    break;

                case "create_game_object":
                    RequireString(args, "name");
                    break;

                case "connect_ui_event":
                    RequireString(args, "source_game_object_path");
                    RequireString(args, "component_type");
                    RequireString(args, "event_property_path");
                    RequireString(args, "target_game_object_path");
                    RequireString(args, "target_method_name");
                    break;

                case "add_component":
                    RequireString(args, "game_object_path");
                    RequireString(args, "component_type");
                    break;
                case "remove_component":
                    RequireString(args, "game_object_path");
                    break;
                case "set_component_property":
                    RequireString(args, "game_object_path");
                    RequireString(args, "component_type");
                    RequireString(args, "property_path");
                    if (!args.ContainsKey("value"))
                        throw new InvalidOperationException("Tool argument 'value' is required.");
                    break;
                case "delete_game_object":
                    RequireString(args, "game_object_path");
                    break;
                case "lint_file":
                    RequireString(args, "path");
                    break;
                case "collect_compile_errors":
                case "run_unity_editor_tests":
                case "save_scene":
                case "get_scene_hierarchy":
                    break;
                default:
                    break;
            }
        }

        private static void RequireString(Dictionary<string, JsonElement> args, string key)
        {
            if (!args.TryGetValue(key, out var el))
                throw new InvalidOperationException($"Tool argument '{key}' is required.");
            if (el.ValueKind != JsonValueKind.String)
                throw new InvalidOperationException($"Tool argument '{key}' must be a string.");
            var s = el.GetString();
            if (string.IsNullOrWhiteSpace(s))
                throw new InvalidOperationException($"Tool argument '{key}' must be a non-empty string.");
        }

        private static string GetString(Dictionary<string, JsonElement> args, string key, string defaultValue = "")
        {
            if (!args.TryGetValue(key, out var el))
                return defaultValue;
            if (el.ValueKind == JsonValueKind.String)
                return el.GetString() ?? defaultValue;
            return el.ToString();
        }

        private static bool GetBool(Dictionary<string, JsonElement> args, string key, bool defaultValue)
        {
            if (!args.TryGetValue(key, out var el))
                return defaultValue;
            if (el.ValueKind == JsonValueKind.True) return true;
            if (el.ValueKind == JsonValueKind.False) return false;
            return defaultValue;
        }

        private static int GetInt(Dictionary<string, JsonElement> args, string key, int defaultValue)
        {
            if (!args.TryGetValue(key, out var el))
                return defaultValue;
            if (el.ValueKind == JsonValueKind.Number && el.TryGetInt32(out var v))
                return v;
            return defaultValue;
        }

        private static float[]? GetFloatArray3(Dictionary<string, JsonElement> args, string key)
        {
            if (!args.TryGetValue(key, out var el) || el.ValueKind != JsonValueKind.Array)
                return null;
            var list = new List<float>();
            foreach (var item in el.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.Number && item.TryGetSingle(out var f))
                    list.Add(f);
            }
            if (list.Count != 3) return null;
            return list.ToArray();
        }

        private static Scene ResolveScene(Dictionary<string, JsonElement> args)
        {
            var scenePath = GetString(args, "scene_path", "");
            if (!string.IsNullOrWhiteSpace(scenePath))
            {
                scenePath = scenePath.Replace('\\', '/');
                if (!scenePath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("scene_path must be under Assets/.");
                return EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
            }

            var active = SceneManager.GetActiveScene();
            if (!active.IsValid())
                throw new InvalidOperationException("No active scene.");
            return active;
        }

        private static GameObject? FindByHierarchyPath(Scene scene, string hierarchyPath)
        {
            if (!scene.IsValid()) return null;
            if (string.IsNullOrWhiteSpace(hierarchyPath) || hierarchyPath == "/")
                return null;

            var parts = hierarchyPath.Trim('/').Split('/');
            GameObject? current = null;
            foreach (var root in scene.GetRootGameObjects())
            {
                if (string.Equals(root.name, parts[0], StringComparison.Ordinal))
                {
                    current = root;
                    break;
                }
            }
            if (current == null) return null;
            for (int i = 1; i < parts.Length; i++)
            {
                var child = current.transform.Find(parts[i]);
                if (child == null) return null;
                current = child.gameObject;
            }
            return current;
        }

        private static string BuildHierarchyPath(Transform t)
        {
            var stack = new Stack<string>();
            var cur = t;
            while (cur != null)
            {
                stack.Push(cur.name);
                cur = cur.parent;
            }
            return "/" + string.Join("/", stack);
        }

        private static PendingTimelineRecord BuildSuccess(ToolCall tc, string summary, string filePath = "", string oldContent = "", string newContent = "")
        {
            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = tc.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(tc.Arguments),
                FilePathRes = filePath,
                OldContent = oldContent,
                NewContent = newContent,
                Status = TimelineStatus.Applied,
                Summary = summary
            };
        }

        private static PendingTimelineRecord BuildFailure(ToolCall tc, string error)
        {
            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = tc.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(tc.Arguments),
                FilePathRes = "",
                OldContent = "",
                NewContent = "",
                Status = TimelineStatus.Failed,
                Error = error,
                Summary = "Failed"
            };
        }

        private PendingTimelineRecord ExecuteOpenScene(ToolCall toolCall)
        {
            var scenePath = GetString(toolCall.Arguments, "scene_path");
            var mode = GetString(toolCall.Arguments, "open_mode", "Single");
            var openMode = string.Equals(mode, "Additive", StringComparison.OrdinalIgnoreCase)
                ? OpenSceneMode.Additive
                : OpenSceneMode.Single;
            var scene = EditorSceneManager.OpenScene(scenePath, openMode);
            return BuildSuccess(toolCall, $"Opened scene: {scene.path}");
        }

        private PendingTimelineRecord ExecuteSaveScene(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var dst = GetString(toolCall.Arguments, "scene_path", "");
            var saveCopy = GetBool(toolCall.Arguments, "save_as_copy", false);
            bool ok = EditorSceneManager.SaveScene(scene, dst, saveCopy);
            if (!ok) throw new InvalidOperationException("SaveScene failed.");
            return BuildSuccess(toolCall, $"Saved scene: {(string.IsNullOrEmpty(dst) ? scene.path : dst)}");
        }

        private PendingTimelineRecord ExecuteGetSceneHierarchy(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var includeInactive = GetBool(toolCall.Arguments, "include_inactive", true);
            var maxNodes = Math.Max(1, GetInt(toolCall.Arguments, "max_nodes", 3000));

            var rows = new List<string>();
            var q = new Queue<Transform>();
            foreach (var root in scene.GetRootGameObjects())
                q.Enqueue(root.transform);
            while (q.Count > 0 && rows.Count < maxNodes)
            {
                var t = q.Dequeue();
                if (!includeInactive && !t.gameObject.activeInHierarchy)
                    continue;
                var components = t.GetComponents<Component>().Where(c => c != null).Select(c => c!.GetType().Name);
                rows.Add($"{BuildHierarchyPath(t)} | active:{t.gameObject.activeSelf} | comps:{string.Join(",", components)}");
                for (int i = 0; i < t.childCount; i++)
                    q.Enqueue(t.GetChild(i));
            }
            return BuildSuccess(toolCall, $"Hierarchy nodes: {rows.Count}", newContent: string.Join("\n", rows));
        }

        private PendingTimelineRecord ExecuteCreateGameObject(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var name = GetString(toolCall.Arguments, "name");
            var parentPath = GetString(toolCall.Arguments, "parent_path", "/");

            var go = new GameObject(name);
            Undo.RegisterCreatedObjectUndo(go, "Create GameObject");

            if (parentPath != "/")
            {
                var parentGo = FindByHierarchyPath(scene, parentPath);
                if (parentGo == null) throw new InvalidOperationException($"Parent path not found: {parentPath}");
                go.transform.SetParent(parentGo.transform, false);
            }
            else
            {
                SceneManager.MoveGameObjectToScene(go, scene);
            }

            var pos = GetFloatArray3(toolCall.Arguments, "local_position");
            var rot = GetFloatArray3(toolCall.Arguments, "local_rotation_euler");
            var scale = GetFloatArray3(toolCall.Arguments, "local_scale");
            if (pos != null) go.transform.localPosition = new Vector3(pos[0], pos[1], pos[2]);
            if (rot != null) go.transform.localEulerAngles = new Vector3(rot[0], rot[1], rot[2]);
            if (scale != null) go.transform.localScale = new Vector3(scale[0], scale[1], scale[2]);

            EditorSceneManager.MarkSceneDirty(scene);
            return BuildSuccess(toolCall, $"Created GameObject: {BuildHierarchyPath(go.transform)}");
        }

        private PendingTimelineRecord ExecuteDeleteGameObject(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var path = GetString(toolCall.Arguments, "game_object_path");
            var go = FindByHierarchyPath(scene, path);
            if (go == null) throw new InvalidOperationException($"GameObject not found: {path}");
            Undo.DestroyObjectImmediate(go);
            EditorSceneManager.MarkSceneDirty(scene);
            return BuildSuccess(toolCall, $"Deleted GameObject: {path}");
        }

        private static Type ResolveComponentType(string typeName)
        {
            var t = Type.GetType(typeName);
            if (t != null) return t;
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                t = asm.GetType(typeName) ?? asm.GetTypes().FirstOrDefault(x => x.Name == typeName);
                if (t != null) return t;
            }
            throw new InvalidOperationException($"Component type not found: {typeName}");
        }

        private PendingTimelineRecord ExecuteAddComponent(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var go = FindByHierarchyPath(scene, GetString(toolCall.Arguments, "game_object_path"));
            if (go == null) throw new InvalidOperationException("Target GameObject not found.");
            var compType = ResolveComponentType(GetString(toolCall.Arguments, "component_type"));
            Undo.AddComponent(go, compType);
            EditorSceneManager.MarkSceneDirty(scene);
            return BuildSuccess(toolCall, $"Added component {compType.Name} to {BuildHierarchyPath(go.transform)}");
        }

        private PendingTimelineRecord ExecuteRemoveComponent(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var go = FindByHierarchyPath(scene, GetString(toolCall.Arguments, "game_object_path"));
            if (go == null) throw new InvalidOperationException("Target GameObject not found.");

            Component? target = null;
            var componentTypeName = GetString(toolCall.Arguments, "component_type", "");
            if (!string.IsNullOrWhiteSpace(componentTypeName))
            {
                var type = ResolveComponentType(componentTypeName);
                target = go.GetComponent(type);
            }
            else if (toolCall.Arguments.ContainsKey("component_index"))
            {
                int idx = GetInt(toolCall.Arguments, "component_index", -1);
                var comps = go.GetComponents<Component>();
                if (idx >= 0 && idx < comps.Length) target = comps[idx];
            }

            if (target == null) throw new InvalidOperationException("Component to remove not found.");
            Undo.DestroyObjectImmediate(target);
            EditorSceneManager.MarkSceneDirty(scene);
            return BuildSuccess(toolCall, $"Removed component from {BuildHierarchyPath(go.transform)}");
        }

        private PendingTimelineRecord ExecuteSetComponentProperty(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var go = FindByHierarchyPath(scene, GetString(toolCall.Arguments, "game_object_path"));
            if (go == null) throw new InvalidOperationException("Target GameObject not found.");
            var compType = ResolveComponentType(GetString(toolCall.Arguments, "component_type"));
            var comp = go.GetComponent(compType);
            if (comp == null) throw new InvalidOperationException("Target component not found.");

            var propPath = GetString(toolCall.Arguments, "property_path");
            var so = new SerializedObject(comp);
            var sp = so.FindProperty(propPath);
            if (sp == null) throw new InvalidOperationException($"Serialized property not found: {propPath}");

            if (!toolCall.Arguments.TryGetValue("value", out var v))
                throw new InvalidOperationException("value is required.");

            Undo.RecordObject(comp, $"Set {propPath}");
            SetSerializedPropertyValue(sp, v);
            so.ApplyModifiedPropertiesWithoutUndo();
            EditorUtility.SetDirty(comp);
            EditorSceneManager.MarkSceneDirty(scene);
            return BuildSuccess(toolCall, $"Set {compType.Name}.{propPath} on {BuildHierarchyPath(go.transform)}");
        }

        private static void SetSerializedPropertyValue(SerializedProperty sp, JsonElement v)
        {
            switch (sp.propertyType)
            {
                case SerializedPropertyType.Boolean:
                    sp.boolValue = v.ValueKind == JsonValueKind.True || (v.ValueKind == JsonValueKind.Number && v.GetInt32() != 0);
                    break;
                case SerializedPropertyType.Integer:
                    sp.intValue = v.ValueKind == JsonValueKind.Number ? v.GetInt32() : int.Parse(v.ToString());
                    break;
                case SerializedPropertyType.Float:
                    sp.floatValue = v.ValueKind == JsonValueKind.Number ? v.GetSingle() : float.Parse(v.ToString());
                    break;
                case SerializedPropertyType.String:
                    sp.stringValue = v.ToString();
                    break;
                case SerializedPropertyType.Enum:
                    if (v.ValueKind == JsonValueKind.Number) sp.enumValueIndex = v.GetInt32();
                    else sp.enumValueIndex = Array.IndexOf(sp.enumDisplayNames, v.ToString());
                    break;
                case SerializedPropertyType.Vector2:
                case SerializedPropertyType.Vector3:
                case SerializedPropertyType.Color:
                case SerializedPropertyType.ObjectReference:
                default:
                    throw new InvalidOperationException($"Unsupported property type for V1: {sp.propertyType}");
            }
        }

        private PendingTimelineRecord ExecuteConnectUiEvent(ToolCall toolCall)
        {
            var scene = ResolveScene(toolCall.Arguments);
            var sourcePath = GetString(toolCall.Arguments, "source_game_object_path");
            var sourceGo = FindByHierarchyPath(scene, sourcePath);
            var targetPath = GetString(toolCall.Arguments, "target_game_object_path");
            var targetGo = FindByHierarchyPath(scene, targetPath);
            if (sourceGo == null || targetGo == null) throw new InvalidOperationException("Source or target GameObject not found.");

            var sourceCompType = ResolveComponentType(GetString(toolCall.Arguments, "component_type"));
            var sourceComp = sourceGo.GetComponent(sourceCompType);
            if (sourceComp == null) throw new InvalidOperationException("Source component not found.");
            var eventPath = GetString(toolCall.Arguments, "event_property_path");
            var methodName = GetString(toolCall.Arguments, "target_method_name");
            var targetCompTypeName = GetString(toolCall.Arguments, "target_component_type", "");
            Component? targetComponent = null;
            if (!string.IsNullOrWhiteSpace(targetCompTypeName))
            {
                var t = ResolveComponentType(targetCompTypeName);
                targetComponent = targetGo.GetComponent(t);
            }
            if (targetComponent == null)
                targetComponent = targetGo.GetComponent<MonoBehaviour>();
            if (targetComponent == null)
                throw new InvalidOperationException("Target component not found for event listener.");

            // V1 implementation: parameterless UnityEvent (e.g., Button.onClick / m_OnClick).
            var eventInfo = sourceCompType.GetProperty("onClick");
            if (!(eventInfo?.GetValue(sourceComp) is UnityEvent unityEvent))
            {
                if (!string.Equals(eventPath, "m_OnClick", StringComparison.Ordinal))
                    throw new InvalidOperationException("Only UnityEvent onClick (m_OnClick) is supported in V1.");
                throw new InvalidOperationException("Source event is not a supported UnityEvent in V1.");
            }

            var mi = targetComponent.GetType().GetMethod(methodName, Type.EmptyTypes);
            if (mi == null)
                throw new InvalidOperationException($"Target method not found or has parameters: {methodName}");

            Undo.RecordObject(sourceComp, "Connect UI Event");
            UnityAction call = (UnityAction)Delegate.CreateDelegate(typeof(UnityAction), targetComponent, mi);
            UnityEventTools.AddPersistentListener(unityEvent, call);
            EditorUtility.SetDirty(sourceComp);
            EditorSceneManager.MarkSceneDirty(scene);
            return BuildSuccess(toolCall, $"Connected {sourcePath}.{eventPath} -> {targetComponent.GetType().Name}.{methodName}");
        }

        private PendingTimelineRecord ExecuteCollectCompileErrors(ToolCall toolCall)
        {
            bool includeWarnings = GetBool(toolCall.Arguments, "include_warnings", true);
            int maxItems = Math.Max(1, GetInt(toolCall.Arguments, "max_items", 200));
            List<string> snapshot;
            lock (CompilerMessagesLock)
            {
                snapshot = _recentCompilerMessages.ToList();
            }

            var filtered = snapshot
                .Where(m => includeWarnings || !m.StartsWith("Warning", StringComparison.OrdinalIgnoreCase))
                .ToList();
            if (filtered.Count > maxItems)
            {
                filtered = filtered.Skip(filtered.Count - maxItems).ToList();
            }
            var payload = string.Join("\n", filtered);
            if (string.IsNullOrWhiteSpace(payload))
                payload = "No recent compiler diagnostics captured.";
            return BuildSuccess(toolCall, "Collected compile diagnostics", newContent: payload);
        }

        private PendingTimelineRecord ExecuteLintFile(ToolCall toolCall)
        {
            bool includeWarnings = GetBool(toolCall.Arguments, "include_warnings", true);
            int maxItems = Math.Max(1, GetInt(toolCall.Arguments, "max_items", 200));
            int timeoutSeconds = Math.Max(5, GetInt(toolCall.Arguments, "lint_timeout_seconds", 120));

            var lintInput = GetString(toolCall.Arguments, "path");
            if (string.IsNullOrWhiteSpace(lintInput))
                throw new InvalidOperationException("lint_file requires argument 'path'.");

            // Normalize to Assets/... inside the project.
            var assetPath = lintInput.Replace('\\', '/').Trim();
            if (assetPath.StartsWith("res://", StringComparison.OrdinalIgnoreCase))
                assetPath = assetPath.Substring("res://".Length);
            assetPath = assetPath.TrimStart('/');

            if (!assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                var idx = assetPath.IndexOf("/Assets/", StringComparison.OrdinalIgnoreCase);
                if (idx >= 0)
                    assetPath = assetPath.Substring(idx + 1); // keep leading Assets/
            }

            if (!assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"lint_file path must resolve to Assets/... . Received: '{lintInput}'");

            var assetPathNorm = assetPath;
            var fileNameOnly = assetPathNorm.Split('/').LastOrDefault() ?? assetPathNorm;
            var absPath = ResPathUtility.ToAbsolutePath(assetPathNorm).Replace('\\', '/');

            int startCount;
            lock (CompilerMessagesLock)
                startCount = _recentCompilerMessages.Count;

            // Force re-import so Unity triggers compilation for this file.
            AssetDatabase.ImportAsset(assetPathNorm, ImportAssetOptions.ForceUpdate);
            AssetDatabase.Refresh();

            // Wait until compilation settles.
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var sawCompiling = false;
            while (sw.Elapsed.TotalSeconds < timeoutSeconds)
            {
                if (EditorApplication.isCompiling)
                    sawCompiling = true;

                if (sawCompiling && !EditorApplication.isCompiling)
                    break;

                System.Threading.Thread.Sleep(200);
            }

            List<string> newMessages;
            lock (CompilerMessagesLock)
                newMessages = _recentCompilerMessages.Skip(startCount).ToList();

            var filtered = newMessages
                .Where(m =>
                    (includeWarnings || !m.StartsWith("Warning", StringComparison.OrdinalIgnoreCase)) &&
                    // Mention-based filtering for stability across Unity versions.
                    (m.IndexOf(assetPathNorm, StringComparison.OrdinalIgnoreCase) >= 0 ||
                     m.IndexOf(absPath, StringComparison.OrdinalIgnoreCase) >= 0 ||
                     m.IndexOf(fileNameOnly, StringComparison.OrdinalIgnoreCase) >= 0))
                .ToList();

            if (filtered.Count > maxItems)
                filtered = filtered.Skip(filtered.Count - maxItems).ToList();

            var payload = string.Join("\n", filtered);
            if (string.IsNullOrWhiteSpace(payload))
                payload = $"No compiler diagnostics captured for {assetPathNorm}.";

            return BuildSuccess(toolCall, $"Linted {assetPathNorm}", newContent: payload);
        }

        private PendingTimelineRecord ExecuteRunUnityEditorTests(ToolCall toolCall)
        {
            // Reflection-based invocation so plugin compiles without hard package reference.
            var apiType = Type.GetType("UnityEditor.TestTools.TestRunner.Api.TestRunnerApi, UnityEditor.TestRunner");
            var filterType = Type.GetType("UnityEditor.TestTools.TestRunner.Api.Filter, UnityEditor.TestRunner");
            var execType = Type.GetType("UnityEditor.TestTools.TestRunner.Api.ExecutionSettings, UnityEditor.TestRunner");
            if (apiType == null || filterType == null || execType == null)
                return BuildFailure(toolCall, "Unity Test Framework package not found.");

            var api = ScriptableObject.CreateInstance(apiType);
            var filter = Activator.CreateInstance(filterType);
            var settings = Activator.CreateInstance(execType, filter);

            var mode = GetString(toolCall.Arguments, "test_mode", "EditMode");
            var runSync = GetBool(toolCall.Arguments, "run_synchronously", false);

            var testModeType = Type.GetType("UnityEditor.TestTools.TestRunner.TestMode, UnityEditor.TestRunner");
            if (testModeType != null)
            {
                var enumVal = Enum.Parse(testModeType, mode, true);
                filterType.GetField("testMode")?.SetValue(filter, enumVal);
            }
            execType.GetField("runSynchronously")?.SetValue(settings, runSync);

            var executeMethod = apiType.GetMethod("Execute", new[] { execType });
            if (executeMethod == null)
                return BuildFailure(toolCall, "Could not find TestRunnerApi.Execute.");
            executeMethod.Invoke(api, new[] { settings });
            return BuildSuccess(toolCall, $"Triggered Unity editor tests ({mode}).");
        }
    }
}

