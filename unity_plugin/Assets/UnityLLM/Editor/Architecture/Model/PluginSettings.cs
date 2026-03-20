namespace UnityLLM.Editor.Architecture.Model
{
    public sealed class PluginSettings
    {
        public string BackendBaseUrl { get; set; } = "http://127.0.0.1:8001";
        public bool ToolsEnabled { get; set; } = true;

        // When using the fine-tuned composer endpoint, map to backend QueryRequest.composer_mode.
        // Allowed values per backend: "agent" | "ask".
        public string ComposerMode { get; set; } = "agent";
    }
}

