using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Backend;

namespace UnityLLM.Editor.Tools.Executors
{
    public sealed class FileToolsExecutors : IToolExecutor
    {
        public bool CanExecute(string toolName)
        {
            return toolName switch
            {
                "read_file" => true,
                "create_file" => true,
                "write_file" => true,
                "append_to_file" => true,
                "apply_patch" => true,
                // Some tool names appear in the schema; implement them for robustness.
                "delete_file" => true,
                "create_script" => true,
                _ => false
            };
        }

        public PendingTimelineRecord Execute(ToolCall toolCall)
        {
            if (toolCall == null)
                throw new ArgumentNullException(nameof(toolCall));
            if (string.IsNullOrWhiteSpace(toolCall.ToolName))
                throw new ArgumentException("ToolName is required");

            return toolCall.ToolName switch
            {
                "read_file" => ExecuteReadFile(toolCall),
                "create_file" => ExecuteCreateFile(toolCall),
                "write_file" => ExecuteWriteFile(toolCall),
                "append_to_file" => ExecuteAppendToFile(toolCall),
                "apply_patch" => ExecuteApplyPatch(toolCall),
                "delete_file" => ExecuteDeleteFile(toolCall),
                "create_script" => ExecuteCreateScript(toolCall),
                _ => throw new InvalidOperationException($"No executor for tool: {toolCall.ToolName}")
            };
        }

        private static string GetStringArg(Dictionary<string, JsonElement> args, string key, bool required)
        {
            if (!args.TryGetValue(key, out var el))
            {
                if (required)
                    throw new InvalidOperationException($"Tool argument '{key}' is required.");
                return "";
            }
            if (el.ValueKind == JsonValueKind.String)
                return el.GetString() ?? "";
            // Allow non-string values (e.g. numbers) to be stringified for V1 robustness.
            return el.ToString();
        }

        private static bool GetBoolArg(Dictionary<string, JsonElement> args, string key, bool defaultValue)
        {
            if (!args.TryGetValue(key, out var el))
                return defaultValue;

            if (el.ValueKind == JsonValueKind.True) return true;
            if (el.ValueKind == JsonValueKind.False) return false;
            return el.GetBoolean();
        }

        private static bool FileExists(string absolutePath)
        {
            return !string.IsNullOrWhiteSpace(absolutePath) && File.Exists(absolutePath);
        }

        private static string ReadTextIfExists(string absolutePath)
        {
            if (!FileExists(absolutePath))
                return "";
            return File.ReadAllText(absolutePath, Encoding.UTF8);
        }

        private static void WriteText(string absolutePath, string content)
        {
            var dir = Path.GetDirectoryName(absolutePath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                Directory.CreateDirectory(dir);

            File.WriteAllText(absolutePath, content ?? "", Encoding.UTF8);
        }

        private PendingTimelineRecord ExecuteReadFile(ToolCall toolCall)
        {
            var resPath = GetStringArg(toolCall.Arguments, "path", required: true);
            var absPath = ResPathUtility.ToAbsolutePath(resPath);
            var existing = ReadTextIfExists(absPath);

            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = toolCall.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                FilePathRes = resPath,
                OldContent = existing,
                NewContent = existing,
                Status = TimelineStatus.Applied,
                Summary = "Read file"
            };
        }

        private PendingTimelineRecord ExecuteCreateFile(ToolCall toolCall)
        {
            var resPath = GetStringArg(toolCall.Arguments, "path", required: true);
            var absPath = ResPathUtility.ToAbsolutePath(resPath);

            bool overwrite = GetBoolArg(toolCall.Arguments, "overwrite", defaultValue: false);
            var content = GetStringArg(toolCall.Arguments, "content", required: false);

            var exists = FileExists(absPath);
            if (exists && !overwrite)
            {
                return new PendingTimelineRecord
                {
                    EditId = Guid.NewGuid().ToString("N"),
                    ToolName = toolCall.ToolName,
                    ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                    FilePathRes = resPath,
                    OldContent = ReadTextIfExists(absPath),
                    NewContent = ReadTextIfExists(absPath),
                    Status = TimelineStatus.Failed,
                    Error = "create_file refused: target exists and overwrite=false",
                    Summary = "Create file (skipped)"
                };
            }

            var oldContent = exists ? ReadTextIfExists(absPath) : "";
            WriteText(absPath, content ?? "");
            var newContent = ReadTextIfExists(absPath);

            TryRefreshAssetDatabase(resPath);

            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = toolCall.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                FilePathRes = resPath,
                OldContent = oldContent,
                NewContent = newContent,
                Status = TimelineStatus.Applied,
                Summary = exists ? "Create file (overwrote)" : "Create file",
            };
        }

        private PendingTimelineRecord ExecuteWriteFile(ToolCall toolCall)
        {
            var resPath = GetStringArg(toolCall.Arguments, "path", required: true);
            var absPath = ResPathUtility.ToAbsolutePath(resPath);

            var content = GetStringArg(toolCall.Arguments, "content", required: true);
            var oldContent = ReadTextIfExists(absPath);
            WriteText(absPath, content ?? "");
            var newContent = ReadTextIfExists(absPath);

            TryRefreshAssetDatabase(resPath);

            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = toolCall.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                FilePathRes = resPath,
                OldContent = oldContent,
                NewContent = newContent,
                Status = TimelineStatus.Applied,
                Summary = "Write file",
                Error = ""
            };
        }

        private PendingTimelineRecord ExecuteAppendToFile(ToolCall toolCall)
        {
            var resPath = GetStringArg(toolCall.Arguments, "path", required: true);
            var absPath = ResPathUtility.ToAbsolutePath(resPath);

            var append = GetStringArg(toolCall.Arguments, "content", required: true);
            var oldContent = ReadTextIfExists(absPath);
            var newContent = (oldContent ?? "") + (append ?? "");

            WriteText(absPath, newContent);
            TryRefreshAssetDatabase(resPath);

            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = toolCall.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                FilePathRes = resPath,
                OldContent = oldContent,
                NewContent = newContent,
                Status = TimelineStatus.Applied,
                Summary = "Append to file"
            };
        }

        private PendingTimelineRecord ExecuteApplyPatch(ToolCall toolCall)
        {
            var resPath = GetStringArg(toolCall.Arguments, "path", required: true);
            var absPath = ResPathUtility.ToAbsolutePath(resPath);

            var oldContent = ReadTextIfExists(absPath);

            // Support either:
            //  - apply_patch(path, old_string, new_string)
            //  - apply_patch(path, diff)
            // diff is "unified-diff" style.
            var diff = GetStringArg(toolCall.Arguments, "diff", required: false);
            var oldString = GetStringArg(toolCall.Arguments, "old_string", required: false);
            var newString = GetStringArg(toolCall.Arguments, "new_string", required: false);

            string updatedContent;
            try
            {
                if (!string.IsNullOrWhiteSpace(diff))
                {
                    updatedContent = ApplyUnifiedDiff(oldContent, diff);
                }
                else
                {
                    updatedContent = ApplyOldStringReplacement(oldContent, oldString, newString);
                }
            }
            catch (Exception e)
            {
                return new PendingTimelineRecord
                {
                    EditId = Guid.NewGuid().ToString("N"),
                    ToolName = toolCall.ToolName,
                    ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                    FilePathRes = resPath,
                    OldContent = oldContent,
                    NewContent = oldContent,
                    Status = TimelineStatus.Failed,
                    Error = e.Message,
                    Summary = "apply_patch failed"
                };
            }

            WriteText(absPath, updatedContent);
            TryRefreshAssetDatabase(resPath);

            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = toolCall.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                FilePathRes = resPath,
                OldContent = oldContent,
                NewContent = updatedContent,
                Status = TimelineStatus.Applied,
                Summary = "apply_patch"
            };
        }

        private PendingTimelineRecord ExecuteDeleteFile(ToolCall toolCall)
        {
            var resPath = GetStringArg(toolCall.Arguments, "path", required: true);
            var absPath = ResPathUtility.ToAbsolutePath(resPath);

            var oldContent = ReadTextIfExists(absPath);

            if (FileExists(absPath))
                File.Delete(absPath);

            TryRefreshAssetDatabase(resPath);

            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = toolCall.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                FilePathRes = resPath,
                OldContent = oldContent,
                NewContent = "",
                Status = TimelineStatus.Applied,
                Summary = "delete_file"
            };
        }

        private PendingTimelineRecord ExecuteCreateScript(ToolCall toolCall)
        {
            var resPath = GetStringArg(toolCall.Arguments, "path", required: true);
            var absPath = ResPathUtility.ToAbsolutePath(resPath);

            var language = GetStringArg(toolCall.Arguments, "language", required: false);
            var extendsClass = GetStringArg(toolCall.Arguments, "extends_class", required: false);
            var initialContent = GetStringArg(toolCall.Arguments, "initial_content", required: false);

            if (!string.IsNullOrWhiteSpace(language) && !string.Equals(language, "csharp", StringComparison.OrdinalIgnoreCase))
            {
                return new PendingTimelineRecord
                {
                    EditId = Guid.NewGuid().ToString("N"),
                    ToolName = toolCall.ToolName,
                    ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                    FilePathRes = resPath,
                    OldContent = ReadTextIfExists(absPath),
                    NewContent = ReadTextIfExists(absPath),
                    Status = TimelineStatus.Failed,
                    Error = $"create_script V1 only supports language='csharp'. Received: '{language}'",
                    Summary = "create_script failed"
                };
            }

            var className = Path.GetFileNameWithoutExtension(absPath);
            var oldContent = ReadTextIfExists(absPath);
            if (FileExists(absPath))
            {
                return new PendingTimelineRecord
                {
                    EditId = Guid.NewGuid().ToString("N"),
                    ToolName = toolCall.ToolName,
                    ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                    FilePathRes = resPath,
                    OldContent = oldContent,
                    NewContent = oldContent,
                    Status = TimelineStatus.Failed,
                    Error = "create_script refused: file already exists",
                    Summary = "create_script skipped"
                };
            }

            // Minimal Unity C# script skeleton.
            var baseClass = string.IsNullOrWhiteSpace(extendsClass) ? "MonoBehaviour" : extendsClass;
            var content =
                $"using UnityEngine;\n\npublic class {className} : {baseClass}\n{{\n    // V1 stub generated by UnityLLM.\n{initialContent}\n}}\n";

            WriteText(absPath, content);
            TryRefreshAssetDatabase(resPath);

            return new PendingTimelineRecord
            {
                EditId = Guid.NewGuid().ToString("N"),
                ToolName = toolCall.ToolName,
                ArgumentsJson = JsonSerializer.Serialize(toolCall.Arguments),
                FilePathRes = resPath,
                OldContent = oldContent,
                NewContent = content,
                Status = TimelineStatus.Applied,
                Summary = "create_script"
            };
        }

        private static string ApplyOldStringReplacement(string oldContent, string oldString, string newString)
        {
            if (string.IsNullOrWhiteSpace(oldString))
                throw new InvalidOperationException("apply_patch requires old_string when diff is not provided.");

            if (newString == null)
                newString = "";

            if (oldContent == null)
                oldContent = "";

            if (oldContent.Contains(newString) && !oldContent.Contains(oldString))
            {
                // Idempotency: new_string already present and old_string isn't.
                return oldContent;
            }

            int idx = oldContent.IndexOf(oldString, StringComparison.Ordinal);
            if (idx < 0)
                throw new InvalidOperationException("apply_patch failed: old_string not found.");

            // Replace first occurrence.
            return oldContent.Substring(0, idx) + (newString ?? "") + oldContent.Substring(idx + oldString.Length);
        }

        private static string ApplyUnifiedDiff(string oldContent, string diff)
        {
            if (diff == null) diff = "";
            oldContent ??= "";

            // Normalize newlines for consistent patch application.
            oldContent = oldContent.Replace("\r\n", "\n").Replace('\r', '\n');
            diff = diff.Replace("\r\n", "\n").Replace('\r', '\n');

            var oldLines = oldContent.Split('\n');
            var diffLines = diff.Split('\n');

            // Skip diff headers (---/+++ lines) and apply hunks.
            var outputLines = new List<string>();

            int oldCursor = 0;
            int i = 0;
            while (i < diffLines.Length)
            {
                var line = diffLines[i];
                if (string.IsNullOrWhiteSpace(line))
                {
                    i++;
                    continue;
                }

                if (!line.StartsWith("@@"))
                {
                    i++;
                    continue;
                }

                // Hunk header: @@ -oldStart,oldCount +newStart,newCount @@
                // We only use oldStart/oldCount to advance oldCursor and to validate content.
                var headerMatch = Regex.Match(line, @"@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@");
                if (!headerMatch.Success)
                    throw new InvalidOperationException("apply_patch failed: malformed unified diff hunk header.");

                int oldStart1Based = int.Parse(headerMatch.Groups[1].Value);
                // int oldCount = headerMatch.Groups[2].Success ? int.Parse(headerMatch.Groups[2].Value) : 1;

                int hunkOldStartIndex0 = Math.Max(0, oldStart1Based - 1);

                // Copy unchanged lines before this hunk.
                if (hunkOldStartIndex0 > oldCursor)
                {
                    for (int k = oldCursor; k < Math.Min(hunkOldStartIndex0, oldLines.Length); k++)
                        outputLines.Add(oldLines[k]);
                    oldCursor = hunkOldStartIndex0;
                }

                i++; // move to first hunk line

                // Process hunk lines until next hunk header or end.
                while (i < diffLines.Length && !diffLines[i].StartsWith("@@"))
                {
                    var hl = diffLines[i];
                    if (hl.Length == 0)
                    {
                        // An empty line in unified diff is ambiguous; treat as context line.
                        if (oldCursor < oldLines.Length)
                        {
                            outputLines.Add(oldLines[oldCursor]);
                            oldCursor++;
                        }
                        i++;
                        continue;
                    }

                    char prefix = hl[0];
                    var contentPart = hl.Length > 1 ? hl.Substring(1) : "";

                    if (prefix == ' ')
                    {
                        if (oldCursor >= oldLines.Length)
                            throw new InvalidOperationException("apply_patch failed: context overflow.");

                        if (!string.Equals(oldLines[oldCursor], contentPart, StringComparison.Ordinal))
                            throw new InvalidOperationException("apply_patch failed: context mismatch.");

                        outputLines.Add(oldLines[oldCursor]);
                        oldCursor++;
                    }
                    else if (prefix == '-')
                    {
                        if (oldCursor >= oldLines.Length)
                            throw new InvalidOperationException("apply_patch failed: removal overflow.");

                        if (!string.Equals(oldLines[oldCursor], contentPart, StringComparison.Ordinal))
                            throw new InvalidOperationException("apply_patch failed: removal mismatch.");

                        oldCursor++; // skip removed line
                    }
                    else if (prefix == '+')
                    {
                        // Add new line.
                        outputLines.Add(contentPart);
                    }
                    else if (prefix == '\\')
                    {
                        // Example: "\ No newline at end of file" - ignore.
                    }
                    else
                    {
                        // Unknown diff line marker: treat as failure.
                        throw new InvalidOperationException("apply_patch failed: unknown diff line prefix.");
                    }

                    i++;
                }
            }

            // Copy remaining lines.
            for (int k = oldCursor; k < oldLines.Length; k++)
                outputLines.Add(oldLines[k]);

            // Join with \n (normalized).
            var result = string.Join("\n", outputLines);
            return result;
        }

        private static void TryRefreshAssetDatabase(string resPath)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(resPath))
                    return;

                // resPath is expected to be "Assets/..." (tool contract uses res:// which we convert,
                // but resPath we store is the original res:// arg; normalize that for AssetDatabase).
                if (resPath.StartsWith("res://", StringComparison.OrdinalIgnoreCase))
                    resPath = resPath.Substring("res://".Length);
                resPath = resPath.TrimStart('/', '\\').Replace('\\', '/');
                if (!resPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                    resPath = "Assets/" + resPath;

                AssetDatabase.ImportAsset(resPath, ImportAssetOptions.ForceUpdate);
                AssetDatabase.Refresh();
            }
            catch
            {
                // V1: best effort. If refresh fails, file ops still happened.
            }
        }
    }
}

