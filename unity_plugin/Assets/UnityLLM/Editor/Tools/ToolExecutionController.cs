using System;
using System.Collections.Generic;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Backend;
using UnityLLM.Editor.Stores;
using UnityLLM.Editor.Tools;
using UnityLLM.Editor.Tools.Executors;

namespace UnityLLM.Editor.Tools
{
    public sealed class ToolExecutionController
    {
        private static readonly HashSet<string> FileMutationTools = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "create_file",
            "write_file",
            "append_to_file",
            "apply_patch",
            "delete_file",
            "create_script"
        };

        private readonly List<IToolExecutor> _executors = new List<IToolExecutor>();

        public ToolExecutionController()
        {
            // V1: file tools are fully implemented.
            _executors.Add(new FileToolsExecutors());
            _executors.Add(new ProjectQueryExecutors());
            // V1: PRD-required Unity tools are stubbed (argument validated + timeline failure recorded).
            _executors.Add(new SceneStubExecutors());
        }

        public IReadOnlyList<PendingTimelineRecord> ExecuteToolCalls(
            IReadOnlyList<ToolCall> toolCalls,
            EditHistoryStore editHistoryStore)
        {
            var results = new List<PendingTimelineRecord>();
            if (toolCalls == null || toolCalls.Count == 0)
                return results;

            foreach (var tc in toolCalls)
            {
                if (tc == null)
                    continue;

                var executor = FindExecutor(tc.ToolName);
                var record = executor.Execute(tc);
                results.Add(record);

                // Persist applied file edits for later “revert selected”.
                var toolNameLc = record?.ToolName ?? "";
                if (record != null &&
                    record.Status == TimelineStatus.Applied &&
                    editHistoryStore != null &&
                    FileMutationTools.Contains(toolNameLc))
                {
                    var editRecord = new EditRecord
                    {
                        EditId = record.EditId,
                        ToolName = record.ToolName,
                        FilePathRes = record.FilePathRes,
                        OldContent = record.OldContent,
                        NewContent = record.NewContent,
                        Summary = record.Summary,
                        ActionType = record.ToolName switch
                        {
                            "create_file" => EditActionType.CreateFile,
                            "write_file" => EditActionType.WriteFile,
                            "append_to_file" => EditActionType.AppendToFile,
                            "apply_patch" => EditActionType.ApplyPatch,
                            "delete_file" => EditActionType.DeleteFile,
                            "create_script" => EditActionType.CreateScript,
                            _ => EditActionType.Unknown
                        }
                    };
                    editHistoryStore.RecordApplied(editRecord);
                }
            }

            return results;
        }

        private IToolExecutor FindExecutor(string toolName)
        {
            if (string.IsNullOrWhiteSpace(toolName))
                throw new InvalidOperationException("Tool name is empty.");

            foreach (var exec in _executors)
            {
                if (exec.CanExecute(toolName))
                    return exec;
            }

            // Fail hard on unknown tool calls.
            throw new InvalidOperationException($"Unknown tool call: {toolName}");
        }
    }
}

