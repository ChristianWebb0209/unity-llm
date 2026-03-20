using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using UnityEditor;
using UnityEngine;

namespace UnityLLM.Editor.Tools
{
    public static class UnityLlmScriptLintRunner
    {
        public static void LintScript()
        {
            try
            {
                var args = Environment.GetCommandLineArgs();
                var lintScriptInput = GetArgValue(args, "-lintScriptPath", "-lintScriptAssetPath");
                var timeoutSeconds = ParseIntOrDefault(GetArgValue(args, "-lintTimeoutSeconds"), defaultValue: 600);

                if (string.IsNullOrWhiteSpace(lintScriptInput))
                {
                    Debug.LogError("[UnityLlmScriptLintRunner] Missing -lintScriptPath argument.");
                    EditorApplication.Exit(1);
                    return;
                }

                var assetPath = NormalizeToAssetPath(lintScriptInput);
                if (string.IsNullOrWhiteSpace(assetPath) || !assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                {
                    Debug.LogError($"[UnityLlmScriptLintRunner] -lintScriptPath did not resolve to an Assets/... path. Input='{lintScriptInput}', resolved='{assetPath}'.");
                    EditorApplication.Exit(1);
                    return;
                }

                // Force re-import so Unity triggers (or re-triggers) script compilation for the target file.
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
                AssetDatabase.Refresh();

                // Wait for compilation to settle.
                WaitForCompilationToFinish(timeoutSeconds);
            }
            catch (Exception e)
            {
                Debug.LogError($"[UnityLlmScriptLintRunner] LintScript failed: {e}");
                EditorApplication.Exit(1);
                return;
            }

            // -batchmode -quit handles process exit, but calling Exit explicitly makes it deterministic.
            EditorApplication.Exit(0);
        }

        private static void WaitForCompilationToFinish(int timeoutSeconds)
        {
            var timeout = Math.Max(5, timeoutSeconds);
            var sw = Stopwatch.StartNew();

            // In some Unity versions compilation can start slightly after ImportAsset/Refresh.
            bool sawCompiling = false;
            while (sw.Elapsed.TotalSeconds < timeout)
            {
                if (EditorApplication.isCompiling)
                {
                    sawCompiling = true;
                }

                if (sawCompiling && !EditorApplication.isCompiling)
                    break;

                Thread.Sleep(200);
            }
        }

        private static int ParseIntOrDefault(string raw, int defaultValue)
        {
            if (string.IsNullOrWhiteSpace(raw))
                return defaultValue;

            if (int.TryParse(raw, out var v))
                return v;

            return defaultValue;
        }

        private static string GetArgValue(string[] args, params string[] names)
        {
            if (args == null || args.Length == 0 || names == null || names.Length == 0)
                return null;

            for (int i = 0; i < args.Length - 1; i++)
            {
                foreach (var name in names)
                {
                    if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase))
                        return args[i + 1];
                }
            }

            return null;
        }

        private static string NormalizeToAssetPath(string input)
        {
            if (string.IsNullOrWhiteSpace(input))
                return "";

            var normalized = input.Replace('\\', '/');
            if (normalized.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                return normalized;

            if (Path.IsPathRooted(input))
            {
                var dataPath = Application.dataPath.Replace('\\', '/'); // .../<Project>/Assets
                if (normalized.StartsWith(dataPath, StringComparison.OrdinalIgnoreCase))
                {
                    var rel = normalized.Substring(dataPath.Length).TrimStart('/');
                    return "Assets/" + rel;
                }
            }

            // Fallback: extract substring beginning with "/Assets/" if present.
            var idx = normalized.IndexOf("/Assets/", StringComparison.OrdinalIgnoreCase);
            if (idx >= 0)
            {
                return normalized.Substring(idx + 1); // remove leading '/'
            }

            return normalized;
        }
    }
}

