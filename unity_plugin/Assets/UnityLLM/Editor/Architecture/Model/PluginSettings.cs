using System.Collections.Generic;

namespace UnityLLM.Editor.Architecture.Model
{
    public sealed class PluginSettings
    {
        public string BackendBaseUrl { get; set; } = "http://127.0.0.1:8001";
        public bool ToolsEnabled { get; set; } = true;

        // When using the fine-tuned composer endpoint, map to backend QueryRequest.composer_mode.
        // Allowed values per backend: "agent" | "ask".
        public string ComposerMode { get; set; } = "agent";

        /// <summary>
        /// UI font size for chat messages.
        /// </summary>
        public int TextSize { get; set; } = 14;

        /// <summary>
        /// Raw JSON editor backing store for model definitions.
        /// </summary>
        public string ModelDefinitionsJson { get; set; } =
            "[{\"id\":\"gpt-4.1-mini\",\"displayName\":\"gpt-4.1-mini\"}]";

        /// <summary>
        /// Parsed model definitions presented in the chat UI model selector.
        /// </summary>
        public List<ModelDefinition> ModelDefinitions { get; set; } =
            new List<ModelDefinition> { new ModelDefinition { Id = "gpt-4.1-mini", DisplayName = "gpt-4.1-mini" } };
    }
}

