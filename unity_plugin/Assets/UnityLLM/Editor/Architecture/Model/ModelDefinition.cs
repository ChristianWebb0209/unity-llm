namespace UnityLLM.Editor.Architecture.Model
{
    /// <summary>
    /// A user-configurable model entry exposed in the chat UI.
    /// </summary>
    [System.Serializable]
    public sealed class ModelDefinition
    {
        public string Id { get; set; } = string.Empty;
        public string DisplayName { get; set; } = string.Empty;
    }
}

