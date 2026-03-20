using System.Collections.Generic;
using System.Text.Json;

namespace UnityLLM.Editor.Backend
{
    public sealed class ToolCall
    {
        public string ToolName { get; set; } = "";
        public Dictionary<string, JsonElement> Arguments { get; set; } = new Dictionary<string, JsonElement>();

        // The backend often returns null output for composer streaming.
        public JsonElement? Output { get; set; }
    }
}

