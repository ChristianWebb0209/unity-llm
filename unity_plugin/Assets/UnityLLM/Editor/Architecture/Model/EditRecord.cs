using System;

namespace UnityLLM.Editor.Architecture.Model
{
    public enum EditActionType
    {
        CreateFile,
        WriteFile,
        AppendToFile,
        ApplyPatch,
        DeleteFile,
        CreateScript,
        Unknown
    }

    [Serializable]
    public sealed class EditRecord
    {
        public string EditId { get; set; } = Guid.NewGuid().ToString("N");
        public string ToolName { get; set; } = "";
        public string ToolArgumentsJson { get; set; } = "{}";

        // res:// path inside project (Unity-style: Assets/...)
        public string FilePathRes { get; set; } = "";

        public string OldContent { get; set; } = "";
        public string NewContent { get; set; } = "";

        public string Summary { get; set; } = "";
        public DateTime TimestampUtc { get; set; } = DateTime.UtcNow;
        public EditActionType ActionType { get; set; } = EditActionType.Unknown;
    }
}

