using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using UnityEditor;
using UnityLLM.Editor.Backend;
using UnityLLM.Editor.Architecture.Model;

namespace UnityLLM.Editor.Tools.Executors
{
    public sealed class ProjectQueryExecutors : IToolExecutor
    {
        // These are the "text-like" extensions we scan for grep/search to avoid reading binary assets.
        private static readonly HashSet<string> DefaultTextExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".cs",
            ".txt",
            ".md",
            ".json",
            ".xml",
            ".yml",
            ".yaml",
            ".shader",
            ".uss",
            ".uxml",
            ".hlsl",
            ".glsl",
            ".cginc",
            ".unity",
            ".prefab",
            ".asset"
        };

        public bool CanExecute(string toolName)
        {
            if (string.IsNullOrWhiteSpace(toolName)) return false;

            return toolName switch
            {
                "list_directory" => true,
                "list_files" => true,
                "search_files" => true,
                "grep_search" => true,
                "project_structure" => true,
                "find_references_to" => true,
                "fetch_url" => true,
                "run_terminal_command" => true,
                _ => false
            };
        }

        public PendingTimelineRecord Execute(ToolCall toolCall)
        {
            if (toolCall == null) throw new ArgumentNullException(nameof(toolCall));

            var args = toolCall.Arguments ?? new Dictionary<string, JsonElement>();
            if (string.IsNullOrWhiteSpace(toolCall.ToolName))
                throw new InvalidOperationException("ToolName is required.");

            return toolCall.ToolName switch
            {
                "list_directory" => ExecuteListDirectory(toolCall, args),
                "list_files" => ExecuteListFiles(toolCall, args),
                "search_files" => ExecuteSearchFiles(toolCall, args),
                "grep_search" => ExecuteGrepSearch(toolCall, args),
                "project_structure" => ExecuteProjectStructure(toolCall, args),
                "find_references_to" => ExecuteFindReferencesTo(toolCall, args),
                "fetch_url" => ExecuteFetchUrl(toolCall, args),
                "run_terminal_command" => ExecuteRunTerminalCommand(toolCall, args),
                _ => throw new InvalidOperationException($"Unsupported project/query tool: {toolCall.ToolName}")
            };
        }

        private static void ValidateResAssetsPath(string assetsPath, string paramName)
        {
            if (string.IsNullOrWhiteSpace(assetsPath))
                throw new InvalidOperationException($"{paramName} is required.");

            assetsPath = NormalizeAssetsPath(assetsPath);
            if (!assetsPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"{paramName} must be under Assets/. Received: '{assetsPath}'");
        }

        private static string NormalizeAssetsPath(string path)
        {
            var p = (path ?? "").Trim();
            p = p.Replace('\\', '/');

            if (string.IsNullOrWhiteSpace(p))
                return "Assets/";

            if (string.Equals(p, "Assets", StringComparison.OrdinalIgnoreCase))
                return "Assets/";

            if (!p.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(p, "Assets", StringComparison.OrdinalIgnoreCase))
            {
                // Allow passing just "Assets/..." or "Assets\\...".
                if (p.StartsWith("Assets", StringComparison.OrdinalIgnoreCase))
                    return "Assets/" + p.Substring("Assets".Length).TrimStart('/');
            }

            if (p.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                // Avoid accidental double slashes.
                p = p.TrimStart('/');
                return p;
            }

            return p;
        }

        private static string NormalizeAssetsPathDir(string path)
        {
            var p = NormalizeAssetsPath(path);
            if (!p.EndsWith("/", StringComparison.Ordinal))
                p += "/";
            return p;
        }

        private static string AbsoluteDirToAssetsPath(string absolutePath)
        {
            if (string.IsNullOrWhiteSpace(absolutePath)) return "";

            var projectRootAbs = ResPathUtility.GetProjectRoot().Replace('\\', '/').TrimEnd('/');
            var normalizedAbs = absolutePath.Replace('\\', '/');

            var rel = normalizedAbs;
            if (rel.StartsWith(projectRootAbs, StringComparison.OrdinalIgnoreCase))
                rel = rel.Substring(projectRootAbs.Length).TrimStart('/');

            rel = rel.Replace('\\', '/');
            if (!rel.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                // Some callers may pass directories using different roots; fall back to filename-only.
                // But keep output stable as best-effort Assets/... paths.
                var idx = normalizedAbs.IndexOf("/Assets/", StringComparison.OrdinalIgnoreCase);
                if (idx >= 0)
                    return normalizedAbs.Substring(idx + 1);
            }

            return rel;
        }

        private static string GetStringArg(Dictionary<string, JsonElement> args, string key, string defaultValue = "", bool required = false)
        {
            if (!args.TryGetValue(key, out var el))
            {
                if (required) throw new InvalidOperationException($"Tool argument '{key}' is required.");
                return defaultValue;
            }

            if (el.ValueKind == JsonValueKind.String)
                return el.GetString() ?? defaultValue;

            if (el.ValueKind == JsonValueKind.Null)
                return defaultValue;

            return el.ToString();
        }

        private static bool GetBoolArg(Dictionary<string, JsonElement> args, string key, bool defaultValue)
        {
            if (!args.TryGetValue(key, out var el))
                return defaultValue;

            if (el.ValueKind == JsonValueKind.True) return true;
            if (el.ValueKind == JsonValueKind.False) return false;
            if (el.ValueKind == JsonValueKind.Number)
                return el.GetInt32() != 0;

            return defaultValue;
        }

        private static int GetIntArg(Dictionary<string, JsonElement> args, string key, int defaultValue, int minValue = int.MinValue, int maxValue = int.MaxValue)
        {
            if (!args.TryGetValue(key, out var el))
                return defaultValue;

            if (el.ValueKind == JsonValueKind.Number && el.TryGetInt32(out var v))
                return Math.Clamp(v, minValue, maxValue);

            return defaultValue;
        }

        private static List<string> GetStringArrayArg(Dictionary<string, JsonElement> args, string key)
        {
            if (!args.TryGetValue(key, out var el))
                return new List<string>();

            if (el.ValueKind != JsonValueKind.Array)
                return new List<string>();

            var list = new List<string>();
            foreach (var item in el.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.String)
                {
                    var s = item.GetString();
                    if (!string.IsNullOrWhiteSpace(s))
                        list.Add(s);
                }
                else if (item.ValueKind != JsonValueKind.Null)
                {
                    list.Add(item.ToString());
                }
            }
            return list;
        }

        private static PendingTimelineRecord BuildSuccess(ToolCall tc, string summary, string newContent = "")
        {
            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = tc.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(tc.Arguments),
                FilePathRes = "",
                OldContent = "",
                NewContent = newContent ?? "",
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
                Error = error ?? "Failed",
                Summary = "Failed"
            };
        }

        private static bool ShouldScanExtension(string filePath, List<string> extensions)
        {
            if (extensions == null || extensions.Count == 0)
                return DefaultTextExtensions.Contains(Path.GetExtension(filePath) ?? "");

            var ext = Path.GetExtension(filePath) ?? "";
            return extensions.Any(e =>
                string.Equals(NormalizeExt(e), ext, StringComparison.OrdinalIgnoreCase));
        }

        private static string NormalizeExt(string ext)
        {
            var e = (ext ?? "").Trim();
            if (string.IsNullOrWhiteSpace(e)) return "";
            if (!e.StartsWith(".")) e = "." + e;
            return e;
        }

        private PendingTimelineRecord ExecuteListDirectory(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            var path = GetStringArg(args, "path", "Assets/");
            var recursive = GetBoolArg(args, "recursive", false);
            var maxEntries = GetIntArg(args, "max_entries", 300, 1, 5000);
            var maxDepth = GetIntArg(args, "max_depth", 8, 0, 30);

            path = NormalizeAssetsPathDir(path);
            ValidateResAssetsPath(path, "path");

            var absRoot = ResPathUtility.ToAbsolutePath(path);
            if (!Directory.Exists(absRoot))
                return BuildFailure(tc, $"Directory does not exist: {path}");

            var results = new List<Dictionary<string, object>>();
            var stack = new Stack<(string abs, string rel, int depth)>();
            stack.Push((absRoot, path, 0));

            while (stack.Count > 0)
            {
                var (abs, relAssets, depth) = stack.Pop();
                if (results.Count >= maxEntries) break;
                if (!recursive && depth > 0) continue;
                if (maxDepth >= 0 && depth > maxDepth) continue;

                string[] entries;
                try
                {
                    entries = Directory.GetFileSystemEntries(abs);
                }
                catch (Exception e)
                {
                    return BuildFailure(tc, $"list_directory failed: {e.Message}");
                }

                foreach (var entry in entries)
                {
                    if (results.Count >= maxEntries) break;

                    var isDir = Directory.Exists(entry);
                    var assetPath = AbsoluteDirToAssetsPath(entry);
                    results.Add(new Dictionary<string, object>
                    {
                        ["path"] = assetPath,
                        ["is_dir"] = isDir
                    });

                    if (recursive && isDir)
                        stack.Push((entry, assetPath, depth + 1));
                }
            }

            var payload = JsonSerializer.Serialize(new { path, recursive, max_depth = maxDepth, entries = results });
            return BuildSuccess(tc, $"Listed {results.Count} entries", payload);
        }

        private PendingTimelineRecord ExecuteListFiles(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            var path = GetStringArg(args, "path", "Assets/");
            var recursive = GetBoolArg(args, "recursive", true);
            var extensions = GetStringArrayArg(args, "extensions");
            var maxEntries = GetIntArg(args, "max_entries", 1000, 1, 5000);

            path = NormalizeAssetsPathDir(path);
            ValidateResAssetsPath(path, "path");

            var absRoot = ResPathUtility.ToAbsolutePath(path);
            if (!Directory.Exists(absRoot))
                return BuildFailure(tc, $"Directory does not exist: {path}");

            var results = new List<string>();
            var queue = new Queue<(string abs, int depth)>();
            queue.Enqueue((absRoot, 0));

            while (queue.Count > 0 && results.Count < maxEntries)
            {
                var (abs, depth) = queue.Dequeue();

                string[] entries;
                try
                {
                    entries = Directory.GetFileSystemEntries(abs);
                }
                catch
                {
                    continue;
                }

                foreach (var entry in entries)
                {
                    if (results.Count >= maxEntries) break;

                    if (File.Exists(entry))
                    {
                        if (!ShouldScanExtension(entry, extensions))
                            continue;

                        var assetPath = AbsoluteDirToAssetsPath(entry);
                        results.Add(assetPath);
                    }
                    else if (Directory.Exists(entry) && recursive)
                    {
                        // In list_files, we don't have a max_depth argument; just rely on max_entries.
                        queue.Enqueue((entry, depth + 1));
                    }
                }
            }

            var payload = JsonSerializer.Serialize(new { path, recursive, extensions, paths = results });
            return BuildSuccess(tc, $"Listed {results.Count} file(s)", payload);
        }

        private PendingTimelineRecord ExecuteSearchFiles(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            var query = GetStringArg(args, "query", "", required: true).Trim();
            var rootPath = GetStringArg(args, "root_path", "Assets/").Trim();
            var extensions = GetStringArrayArg(args, "extensions");
            var maxMatches = GetIntArg(args, "max_matches", 100, 1, 2000);

            if (string.IsNullOrWhiteSpace(query))
                throw new InvalidOperationException("query is required.");

            rootPath = NormalizeAssetsPathDir(rootPath);
            ValidateResAssetsPath(rootPath, "root_path");

            var absRoot = ResPathUtility.ToAbsolutePath(rootPath);
            if (!Directory.Exists(absRoot))
                return BuildFailure(tc, $"Directory does not exist: {rootPath}");

            var matches = new List<string>();

            var files = Directory.EnumerateFiles(absRoot, "*", SearchOption.AllDirectories);
            foreach (var file in files)
            {
                if (matches.Count >= maxMatches) break;
                if (!ShouldScanExtension(file, extensions))
                    continue;

                string text;
                try
                {
                    // Read as UTF8 best-effort; Unity scripts are typically UTF-8.
                    text = File.ReadAllText(file, Encoding.UTF8);
                }
                catch
                {
                    continue;
                }

                if (text.Contains(query, StringComparison.Ordinal))
                {
                    matches.Add(AbsoluteDirToAssetsPath(file));
                }
            }

            var payload = JsonSerializer.Serialize(new { query, root_path = rootPath, matches = matches });
            return BuildSuccess(tc, $"Found {matches.Count} file(s) containing query", payload);
        }

        private PendingTimelineRecord ExecuteGrepSearch(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            // In rag_service: pattern or query are accepted by backend.
            var pattern = GetStringArg(args, "pattern", "").Trim();
            if (string.IsNullOrWhiteSpace(pattern))
                pattern = GetStringArg(args, "query", "").Trim();

            var rootPath = GetStringArg(args, "root_path", "Assets/").Trim();
            var extensions = GetStringArrayArg(args, "extensions");
            var maxMatches = GetIntArg(args, "max_matches", 200, 1, 5000);
            var useRegex = GetBoolArg(args, "use_regex", true);

            if (string.IsNullOrWhiteSpace(pattern))
                throw new InvalidOperationException("pattern or query is required.");

            rootPath = NormalizeAssetsPathDir(rootPath);
            ValidateResAssetsPath(rootPath, "root_path");

            var absRoot = ResPathUtility.ToAbsolutePath(rootPath);
            if (!Directory.Exists(absRoot))
                return BuildFailure(tc, $"Directory does not exist: {rootPath}");

            Regex? regex = null;
            if (useRegex)
            {
                try
                {
                    regex = new Regex(pattern, RegexOptions.Compiled);
                }
                catch (Exception e)
                {
                    return BuildFailure(tc, $"Invalid regex: {e.Message}");
                }
            }

            var matches = new List<Dictionary<string, object>>();
            var files = Directory.EnumerateFiles(absRoot, "*", SearchOption.AllDirectories);
            foreach (var file in files)
            {
                if (matches.Count >= maxMatches) break;
                if (!ShouldScanExtension(file, extensions))
                    continue;

                string[] lines;
                try
                {
                    lines = File.ReadAllLines(file, Encoding.UTF8);
                }
                catch
                {
                    continue;
                }

                for (int i = 0; i < lines.Length; i++)
                {
                    if (matches.Count >= maxMatches) break;
                    var line = lines[i];
                    bool hit = false;
                    if (regex != null)
                        hit = regex.IsMatch(line);
                    else
                        hit = line.Contains(pattern, StringComparison.Ordinal);

                    if (!hit) continue;

                    matches.Add(new Dictionary<string, object>
                    {
                        ["path"] = AbsoluteDirToAssetsPath(file),
                        ["line"] = i + 1,
                        ["text"] = line
                    });
                }
            }

            var payload = JsonSerializer.Serialize(new { pattern, root_path = rootPath, matches = matches });
            return BuildSuccess(tc, $"Found {matches.Count} match(es)", payload);
        }

        private PendingTimelineRecord ExecuteProjectStructure(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            var prefix = GetStringArg(args, "prefix", "Assets/");
            var maxPaths = GetIntArg(args, "max_paths", 300, 1, 1000);
            var maxDepth = GetIntArg(args, "max_depth", 10, 1, 20);

            prefix = NormalizeAssetsPathDir(prefix);
            ValidateResAssetsPath(prefix, "prefix");

            var absRoot = ResPathUtility.ToAbsolutePath(prefix);
            if (!Directory.Exists(absRoot))
                return BuildFailure(tc, $"Directory does not exist: {prefix}");

            var results = new List<string>();

            // BFS so maxDepth is applied cleanly.
            var queue = new Queue<(string abs, int depth)>();
            queue.Enqueue((absRoot, 0));

            while (queue.Count > 0 && results.Count < maxPaths)
            {
                var (abs, depth) = queue.Dequeue();
                if (depth > maxDepth) continue;

                string[] entries;
                try
                {
                    entries = Directory.GetFileSystemEntries(abs);
                }
                catch
                {
                    continue;
                }

                foreach (var entry in entries)
                {
                    if (results.Count >= maxPaths) break;

                    if (Directory.Exists(entry))
                    {
                        queue.Enqueue((entry, depth + 1));
                    }
                    else if (File.Exists(entry))
                    {
                        results.Add(AbsoluteDirToAssetsPath(entry));
                    }
                }
            }

            var payload = JsonSerializer.Serialize(new { prefix, max_paths = results.Count, max_depth = maxDepth, paths = results });
            return BuildSuccess(tc, $"Listed {results.Count} path(s)", payload);
        }

        private PendingTimelineRecord ExecuteFindReferencesTo(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            // rag_service uses res_path as the argument name.
            var resPath = GetStringArg(args, "res_path", "", required: true).Trim();
            if (string.IsNullOrWhiteSpace(resPath))
                throw new InvalidOperationException("res_path is required.");

            // Normalize to Assets/... for consistent matching.
            resPath = resPath.Replace('\\', '/');
            if (!resPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                if (resPath.StartsWith("res://", StringComparison.OrdinalIgnoreCase))
                    resPath = resPath.Substring("res://".Length);
                if (!resPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                    resPath = "Assets/" + resPath.TrimStart('/');
            }

            var absRoot = ResPathUtility.ToAbsolutePath("Assets/");
            if (!Directory.Exists(absRoot))
                return BuildFailure(tc, $"Project Assets directory not found.");

            var matches = new List<string>();
            var maxMatches = 20; // Keep bounded; backend/index tool is preferred when available.

            var files = Directory.EnumerateFiles(absRoot, "*", SearchOption.AllDirectories);
            foreach (var file in files)
            {
                if (matches.Count >= maxMatches) break;
                if (!DefaultTextExtensions.Contains(Path.GetExtension(file) ?? ""))
                    continue;

                string text;
                try
                {
                    text = File.ReadAllText(file, Encoding.UTF8);
                }
                catch
                {
                    continue;
                }

                if (text.Contains(resPath, StringComparison.Ordinal))
                    matches.Add(AbsoluteDirToAssetsPath(file));
            }

            var payload = JsonSerializer.Serialize(new { res_path = resPath, references = matches });
            return BuildSuccess(tc, $"Found {matches.Count} reference file(s)", payload);
        }

        private PendingTimelineRecord ExecuteFetchUrl(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            // NOTE: rag_service local mode currently disables fetch_url; this exists so the plugin can execute it
            // if the backend is configured to allow it in the future.
            var url = GetStringArg(args, "url", "", required: true).Trim();

            try
            {
                using var http = new HttpClient();
                http.Timeout = TimeSpan.FromSeconds(20);

                // Blocking call; tools are executed synchronously on the editor main thread.
                var text = http.GetStringAsync(url).GetAwaiter().GetResult();
                if (text == null) text = "";

                var payload = JsonSerializer.Serialize(new { url, length = text.Length, text = text });
                return BuildSuccess(tc, $"Fetched URL (chars={text.Length})", payload);
            }
            catch (Exception e)
            {
                return BuildFailure(tc, $"fetch_url failed: {e.Message}");
            }
        }

        private PendingTimelineRecord ExecuteRunTerminalCommand(ToolCall tc, Dictionary<string, JsonElement> args)
        {
            var command = GetStringArg(args, "command", "", required: true).Trim();
            var timeoutSeconds = GetIntArg(args, "timeout_seconds", 60, 1, 900);
            var maxOutputChars = GetIntArg(args, "max_output_chars", 200000, 1000, 2000000);

            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = "/c " + command,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true
                };

                using var process = new Process { StartInfo = psi, EnableRaisingEvents = true };

                var stdout = new StringBuilder();
                var stderr = new StringBuilder();

                void CapAppend(StringBuilder sb, string? line)
                {
                    if (string.IsNullOrEmpty(line)) return;
                    if (sb.Length >= maxOutputChars) return;
                    var remaining = maxOutputChars - sb.Length;
                    if (line.Length > remaining)
                        sb.Append(line.Substring(0, remaining));
                    else
                        sb.AppendLine(line);
                }

                process.OutputDataReceived += (_, e) => CapAppend(stdout, e.Data);
                process.ErrorDataReceived += (_, e) => CapAppend(stderr, e.Data);

                if (!process.Start())
                    return BuildFailure(tc, "Failed to start terminal command.");

                process.BeginOutputReadLine();
                process.BeginErrorReadLine();

                var exited = process.WaitForExit(TimeSpan.FromSeconds(timeoutSeconds));
                if (!exited)
                {
                    try { process.Kill(); } catch { /* ignore */ }
                    return BuildFailure(tc, $"run_terminal_command timed out after {timeoutSeconds}s.");
                }

                process.WaitForExit();

                var exitCode = process.ExitCode;
                var outText = stdout.ToString();
                var errText = stderr.ToString();

                var payload = JsonSerializer.Serialize(new
                {
                    command,
                    exit_code = exitCode,
                    stdout = outText,
                    stderr = errText,
                });

                return new PendingTimelineRecord
                {
                    EditId = Guid.NewGuid().ToString("N"),
                    ToolName = tc.ToolName,
                    ArgumentsJson = JsonSerializer.Serialize(tc.Arguments),
                    FilePathRes = "",
                    OldContent = "",
                    NewContent = payload,
                    Status = TimelineStatus.Applied,
                    Summary = $"Command finished (exit_code={exitCode})"
                };
            }
            catch (Exception e)
            {
                return BuildFailure(tc, $"run_terminal_command failed: {e.Message}");
            }
        }
    }
}

