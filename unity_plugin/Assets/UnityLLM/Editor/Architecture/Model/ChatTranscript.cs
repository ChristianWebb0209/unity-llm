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

