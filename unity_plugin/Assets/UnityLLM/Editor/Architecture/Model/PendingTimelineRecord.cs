using System;

namespace UnityLLM.Editor.Architecture.Model
{
    public enum TimelineStatus
    {
        Pending,
        Applied,
        Failed
    }

    [Serializable]
    public sealed class PendingTimelineRecord
    {
        public string EditId { get; set; } = Guid.NewGuid().ToString("N");
        public string ToolName { get; set; } = "";
        public string ArgumentsJson { get; set; } = "{}";

        // res:// path inside project (Unity-style: Assets/...)
        public string FilePathRes { get; set; } = "";
        public string OldContent { get; set; } = "";
        public string NewContent { get; set; } = "";

        public TimelineStatus Status { get; set; } = TimelineStatus.Pending;
        public string Error { get; set; } = "";
        public DateTime TimestampUtc { get; set; } = DateTime.UtcNow;

        public string Summary { get; set; } = "";
    }
}

