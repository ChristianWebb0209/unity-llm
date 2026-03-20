using System;
using System.Collections.Generic;

namespace UnityLLM.Editor.Architecture.Model
{
    [Serializable]
    public sealed class ChatTranscript
    {
        public string ChatId { get; set; } = Guid.NewGuid().ToString("N");
        public List<ChatMessage> Messages { get; set; } = new List<ChatMessage>();

        public void AddUserMessage(string content)
        {
            Messages.Add(new ChatMessage
            {
                Role = ChatRole.User,
                Content = content ?? "",
                TimestampUtc = DateTime.UtcNow
            });
        }

        /// <summary>
        /// Adds an assistant message placeholder and returns its index so the caller can update content while streaming.
        /// </summary>
        public int BeginAssistantMessage()
        {
            var idx = Messages.Count;
            Messages.Add(new ChatMessage
            {
                Role = ChatRole.Assistant,
                Content = "",
                TimestampUtc = DateTime.UtcNow
            });
            return idx;
        }

        public void UpdateAssistantMessageContent(int messageIndex, string content)
        {
            if (messageIndex < 0 || messageIndex >= Messages.Count) return;
            if (Messages[messageIndex] == null) return;
            if (Messages[messageIndex].Role != ChatRole.Assistant) return;
            Messages[messageIndex].Content = content ?? "";
        }

        public void AddAssistantMessage(string content)
        {
            Messages.Add(new ChatMessage
            {
                Role = ChatRole.Assistant,
                Content = content ?? "",
                TimestampUtc = DateTime.UtcNow
            });
        }
    }
}

