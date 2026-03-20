using System;

namespace UnityLLM.Editor.Architecture.Model
{
    /// <summary>
    /// One persisted chat session (one open chat tab).
    /// </summary>
    [Serializable]
    public sealed class ChatSession
    {
        public string Title { get; set; } = "New chat";

        /// <summary>
        /// OpenAI/Backend model identifier to override the backend for this chat.
        /// </summary>
        public string SelectedModelId { get; set; } = string.Empty;

        /// <summary>
        /// Optional future hook: distinguish composer vs rag models in UI.
        /// </summary>
        public string SelectedModelKind { get; set; } = "auto";

        public ChatTranscript Transcript { get; set; } = new ChatTranscript();
    }
}

