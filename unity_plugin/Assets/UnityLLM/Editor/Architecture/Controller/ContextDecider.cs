using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Stores;

#nullable enable

namespace UnityLLM.Editor.Architecture.Controller
{
    /// <summary>
    /// Builds the per-request `context.extra` payload for `rag_service`.
    /// "User-time" context: scene state (hierarchy snapshot) and active editor hints.
    /// "Agent-time" context: recent tool-executed edits (cross-turn working set).
    /// </summary>
    public static class ContextDecider
    {
        // Rough cap to keep payloads reasonable; backend still does budgeted trimming.
        private const int MaxSceneTreeNodes = 800;
        private const int MaxComponentsPerNode = 6;

        private const int MaxActiveFileTextChars = 12_000;
        private const int MaxRecentEdits = 10;
        private const int MaxRecentEditEntryChars = 900;

        private const int MaxConversationTurns = 20;

        // Find an Assets/... or res://Assets/... mention in the prompt.
        // We only treat .cs/.gd as "active file" candidates for now.
        private static readonly Regex _assetsPathRegex = new Regex(
            @"(?:(res:\/\/)?)(Assets\/[A-Za-z0-9_\-\/\.]+?\.(?:cs|gd))",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

        public static Dictionary<string, object?> BuildContextExtra(
            string prompt,
            EditHistoryStore editHistoryStore,
            ChatTranscript transcript
        )
        {
            var extra = new Dictionary<string, object?>();

            // Backend reads this to serve read_file/list_files/etc (server-side).
            try
            {
                extra["project_root_abs"] = ResPathUtility.GetProjectRoot();
            }
            catch
            {
                // If we can't compute it, backend will still work with partial context (no server-side exploration).
            }

            // --- User-time: scene hierarchy snapshot ---
            try
            {
                var scene = SceneManager.GetActiveScene();
                if (scene.IsValid())
                {
                    if (!string.IsNullOrWhiteSpace(scene.path))
                        extra["active_scene_path"] = scene.path;

                    extra["scene_tree"] = BuildSceneTree(scene, MaxSceneTreeNodes, MaxComponentsPerNode, includeInactive: true);
                }
            }
            catch
            {
                // Best-effort only.
            }

            // --- User-time: active file preview ---
            string? activeFileText = null;
            string? activeFilePathRes = null;

            var promptAssetsPath = TryExtractAssetsScriptPath(prompt);
            if (!string.IsNullOrWhiteSpace(promptAssetsPath) && editHistoryStore != null)
            {
                try
                {
                    activeFilePathRes = NormalizeToAssetsPath(promptAssetsPath);
                    var absPath = ResPathUtility.ToAbsolutePath(promptAssetsPath);
                    if (!string.IsNullOrWhiteSpace(absPath) && File.Exists(absPath))
                    {
                        var text = File.ReadAllText(absPath, Encoding.UTF8);
                        activeFileText = Truncate(text, MaxActiveFileTextChars);
                    }
                }
                catch
                {
                    // ignore, fallback to recent edits
                }
            }

            // Fallback: "latest agent edits" becomes the active file preview.
            if (string.IsNullOrWhiteSpace(activeFileText) && editHistoryStore?.History != null && editHistoryStore.History.Count > 0)
            {
                var latest = editHistoryStore.History[0];
                if (!string.IsNullOrWhiteSpace(latest.FilePathRes))
                {
                    activeFilePathRes = latest.FilePathRes;
                    var candidate = !string.IsNullOrWhiteSpace(latest.NewContent) ? latest.NewContent : latest.OldContent;
                    if (!string.IsNullOrWhiteSpace(candidate))
                        activeFileText = Truncate(candidate, MaxActiveFileTextChars);
                }
            }

            if (!string.IsNullOrWhiteSpace(activeFileText))
                extra["active_file_text"] = activeFileText;

            // --- Agent-time: recent edit working set ---
            try
            {
                if (editHistoryStore?.History != null && editHistoryStore.History.Count > 0)
                {
                    var recent = BuildRecentEdits(editHistoryStore.History, MaxRecentEdits, MaxRecentEditEntryChars);
                    if (recent.Count > 0)
                        extra["recent_edits"] = recent;
                }
            }
            catch
            {
                // best effort only
            }

            // --- Multi-turn continuity ---
            try
            {
                extra["chat_id"] = transcript?.ChatId;

                if (transcript?.Messages != null && transcript.Messages.Count > 0)
                {
                    var hist = new List<Dictionary<string, object?>>();
                    var startIdx = Math.Max(0, transcript.Messages.Count - MaxConversationTurns);
                    foreach (var m in transcript.Messages.Skip(startIdx))
                    {
                        if (m == null) continue;
                        var content = m.Content ?? "";
                        if (string.IsNullOrWhiteSpace(content)) continue;

                        var role = m.Role == ChatRole.User ? "user" : "assistant";
                        hist.Add(new Dictionary<string, object?>
                        {
                            ["role"] = role,
                            ["content"] = content
                        });
                    }

                    if (hist.Count > 0)
                        extra["conversation_history"] = hist;
                }
            }
            catch
            {
                // ignore
            }

            return extra;
        }

        private static string? TryExtractAssetsScriptPath(string prompt)
        {
            if (string.IsNullOrWhiteSpace(prompt))
                return null;

            var m = _assetsPathRegex.Match(prompt);
            if (!m.Success)
                return null;

            // m.Groups[1] may be "res://" (nullable), m.Groups[2] is the Assets/... part.
            var resPrefix = m.Groups[1].Value;
            var assetsPath = m.Groups[2].Value;
            if (!string.IsNullOrWhiteSpace(resPrefix))
                return "res://" + assetsPath;
            return assetsPath;
        }

        private static string NormalizeToAssetsPath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
                return path ?? "";

            // Remove leading res://
            var p = path.Trim();
            if (p.StartsWith("res://", StringComparison.OrdinalIgnoreCase))
                p = p.Substring("res://".Length);

            p = p.Replace('\\', '/');
            if (!p.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                p = "Assets/" + p.TrimStart('/');

            return p;
        }

        private static List<string> BuildRecentEdits(
            IReadOnlyList<EditRecord> history,
            int maxEdits,
            int maxEntryChars)
        {
            var outList = new List<string>();

            for (int i = 0; i < history.Count && outList.Count < maxEdits; i++)
            {
                var rec = history[i];
                if (rec == null) continue;
                if (string.IsNullOrWhiteSpace(rec.FilePathRes)) continue;

                var oldSnippet = Truncate(rec.OldContent ?? "", maxEntryChars);
                var newSnippet = Truncate(rec.NewContent ?? "", maxEntryChars);

                if (string.IsNullOrWhiteSpace(oldSnippet) && string.IsNullOrWhiteSpace(newSnippet))
                    continue;

                var tool = string.IsNullOrWhiteSpace(rec.ToolName) ? "edit" : rec.ToolName;

                // Keep per-entry compact: the backend will still budget/evict at the block level.
                var entry =
                    $"--- Recent edit ({tool}) ---\n" +
                    $"Path: {rec.FilePathRes}\n" +
                    $"Old:\n{oldSnippet}\n" +
                    $"New:\n{newSnippet}";

                outList.Add(entry);
            }

            return outList;
        }

        private static string BuildSceneTree(
            Scene scene,
            int maxNodes,
            int maxComponentsPerNode,
            bool includeInactive)
        {
            var rows = new List<string>();
            if (!scene.IsValid())
                return "";

            var q = new Queue<Transform>();
            foreach (var root in scene.GetRootGameObjects())
            {
                if (root == null) continue;
                q.Enqueue(root.transform);
            }

            while (q.Count > 0 && rows.Count < maxNodes)
            {
                var t = q.Dequeue();
                if (t == null) continue;

                if (!includeInactive && !t.gameObject.activeInHierarchy)
                    continue;

                var compTypes = new List<string>();
                try
                {
                    var comps = t.GetComponents<Component>();
                    foreach (var c in comps)
                    {
                        if (c == null) continue;
                        compTypes.Add(c.GetType().Name);
                        if (compTypes.Count >= maxComponentsPerNode)
                            break;
                    }
                }
                catch
                {
                    // Best-effort.
                }

                rows.Add(
                    $"{BuildHierarchyPath(t)} | activeSelf:{t.gameObject.activeSelf} | comps:{string.Join(",", compTypes)}"
                );

                for (int i = 0; i < t.childCount; i++)
                    q.Enqueue(t.GetChild(i));
            }

            return string.Join("\n", rows);
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

        private static string Truncate(string? text, int maxChars)
        {
            if (string.IsNullOrWhiteSpace(text))
                return "";

            var s = text;
            if (s.Length <= maxChars)
                return s;

            // Keep tail out to preserve "head" context for symbol-based reasoning.
            return s.Substring(0, Math.Max(0, maxChars - 200)) +
                   "\n\n[...truncated for context decider payload...]\n";
        }
    }
}

