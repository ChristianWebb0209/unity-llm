using UnityEngine.UIElements;
using UnityLLM.Editor.Architecture.Model;

#nullable enable
namespace UnityLLM.Editor.Architecture.View
{
    public sealed class TimelineTabView
    {
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

                if (onRevertRequested != null && capture.Status == TimelineStatus.Applied && !string.IsNullOrWhiteSpace(capture.FilePathRes))
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

