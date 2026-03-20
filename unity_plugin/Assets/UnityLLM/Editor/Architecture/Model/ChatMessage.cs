using System;

namespace UnityLLM.Editor.Architecture.Model
{
    public enum ChatRole
    {
        User,
        Assistant
    }

    [Serializable]
    public sealed class ChatMessage
    {
        public ChatRole Role { get; set; }
        public string Content { get; set; } = "";
        public DateTime TimestampUtc { get; set; }
    }
}

