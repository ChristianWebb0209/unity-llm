using System;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using UnityEngine.UIElements;
using UnityEngine;
using UnityLLM.Editor.Architecture.Model;

#nullable enable
namespace UnityLLM.Editor.Architecture.View
{
    public sealed class TimelineTabView
    {
        private const int ExternalDiffPort = 8734;
        private static readonly HttpClient Http = new HttpClient();

        private static string BuildExternalDiffTitle(PendingTimelineRecord record)
        {
            if (record == null) return "UnityLLM Diff";
            if (!string.IsNullOrWhiteSpace(record.FilePathRes)) return record.FilePathRes;
            if (!string.IsNullOrWhiteSpace(record.Summary)) return record.Summary;
            return record.ToolName ?? "UnityLLM Diff";
        }

        private static async Task TryOpenExternalDiffAsync(PendingTimelineRecord record)
        {
            if (record == null) return;
            if (record.Status != TimelineStatus.Applied) return;

            var url = $"http://127.0.0.1:{ExternalDiffPort}/showDiff";
            var payload = new
            {
                requestId = record.EditId,
                title = BuildExternalDiffTitle(record),
                filePath = record.FilePathRes,
                oldContent = record.OldContent ?? "",
                newContent = record.NewContent ?? "",
            };

            try
            {
                var json = JsonSerializer.Serialize(payload);
                using var content = new StringContent(json, Encoding.UTF8, "application/json");
                using var resp = await Http.PostAsync(url, content);
                if (!resp.IsSuccessStatusCode)
                    Debug.LogWarning($"[UnityLLM] External diff request failed: {(int)resp.StatusCode} {resp.ReasonPhrase}");
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[UnityLLM] External diff request error: {e.Message}");
            }
        }

        private static bool IsRevertableTool(string? toolName)
        {
            if (string.IsNullOrWhiteSpace(toolName)) return false;
            return toolName switch
            {
                "create_file" => true,
                "write_file" => true,
                "append_to_file" => true,
                "apply_patch" => true,
                "delete_file" => true,
                "create_script" => true,
                _ => false
            };
        }

        private readonly VisualElement _root;
        private readonly ScrollView _timelineScroll;
        private readonly TextElement _diffOldText;
        private readonly TextElement _diffNewText;

        private readonly System.Collections.Generic.List<PendingTimelineRecord> _records =
            new System.Collections.Generic.List<PendingTimelineRecord>();

        public TimelineTabView(VisualElement root)
        {
            _root = root;
            _timelineScroll = root.Q<ScrollView>("TimelineScrollView");
            _diffOldText = root.Q<TextElement>("DiffOldText");
            _diffNewText = root.Q<TextElement>("DiffNewText");

            ShowDiff("Old: (select an edit)", "New: (select an edit)");
        }

        public void Clear()
        {
            _records.Clear();
            if (_timelineScroll != null)
                _timelineScroll.Clear();
            ShowDiff("Old: (select an edit)", "New: (select an edit)");
        }

        public void AddTimelineItem(string text)
        {
            if (_timelineScroll == null) return;
            _timelineScroll.Add(new Label(text));
        }

        public void ShowDiff(string oldText, string newText)
        {
            if (_diffOldText != null) _diffOldText.text = oldText ?? "";
            if (_diffNewText != null) _diffNewText.text = newText ?? "";
        }

        public void SetTimelineRecords(
            System.Collections.Generic.IReadOnlyList<PendingTimelineRecord> records,
            System.Action<PendingTimelineRecord>? onRevertRequested = null)
        {
            Clear();

            if (records == null || records.Count == 0)
            {
                AddTimelineItem("No tool calls.");
                return;
            }

            _records.AddRange(records);

            foreach (var r in records)
            {
                var capture = r;
                var label = $"{StatusToLabel(r.Status)} {r.ToolName}";
                if (!string.IsNullOrWhiteSpace(r.Summary))
                    label += $" — {r.Summary}";

                var row = new VisualElement();
                row.style.flexDirection = FlexDirection.Row;

                var btn = new Button(() =>
                {
                    if (capture == null) return;
                    if (!string.IsNullOrEmpty(capture.FilePathRes) && capture.Status == TimelineStatus.Applied)
                    {
                        ShowDiff("Old: " + (capture.OldContent ?? ""), "New: " + (capture.NewContent ?? ""));
                    }
                    else
                    {
                        ShowDiff("Old: (no diff)", "New: (no diff)");
                    }
                });

                btn.text = label;
                row.Add(btn);

                if (!string.IsNullOrWhiteSpace(capture.FilePathRes) && capture.Status == TimelineStatus.Applied)
                {
                    var externalDiffBtn = new Button(() => { _ = TryOpenExternalDiffAsync(capture); }) { text = "Open Diff (VS Code)" };
                    row.Add(externalDiffBtn);
                }

                if (onRevertRequested != null &&
                    capture.Status == TimelineStatus.Applied &&
                    IsRevertableTool(capture.ToolName) &&
                    !string.IsNullOrWhiteSpace(capture.FilePathRes))
                {
                    var revertBtn = new Button(() => onRevertRequested(capture)) { text = "Revert" };
                    row.Add(revertBtn);
                }
                _timelineScroll.Add(row);
            }

            // Default selection: first applied record with a file path.
            for (int i = 0; i < _records.Count; i++)
            {
                var r = _records[i];
                if (r != null && r.Status == TimelineStatus.Applied && !string.IsNullOrWhiteSpace(r.FilePathRes))
                {
                    ShowDiff("Old: " + (r.OldContent ?? ""), "New: " + (r.NewContent ?? ""));
                    break;
                }
            }
        }

        private static string StatusToLabel(TimelineStatus status)
        {
            return status switch
            {
                TimelineStatus.Applied => "[Applied]",
                TimelineStatus.Failed => "[Failed]",
                _ => "[Pending]"
            };
        }
    }
}

