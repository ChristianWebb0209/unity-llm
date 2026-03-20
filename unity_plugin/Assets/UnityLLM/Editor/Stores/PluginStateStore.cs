using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using UnityLLM.Editor.Architecture.Model;

#nullable enable
namespace UnityLLM.Editor.Stores
{
    public sealed class PluginStateStore
    {
        private const string StateStoreRelativePath = "Library/UnityLLM/plugin_state.json";
        private readonly string _statePath;
        private readonly JsonSerializerOptions _opts = new JsonSerializerOptions { WriteIndented = true, PropertyNameCaseInsensitive = true };

        public PluginStateStore()
        {
            _statePath = Path.Combine(ResPathUtility.GetProjectRoot(), StateStoreRelativePath.Replace('/', Path.DirectorySeparatorChar));
        }

        public (PluginSettings settings, List<ChatSession> sessions, string activeChatId) Load()
        {
            try
            {
                if (!File.Exists(_statePath))
                    return (new PluginSettings(), new List<ChatSession>(), "");
                var json = File.ReadAllText(_statePath);
                var dto = JsonSerializer.Deserialize<PluginStateDto>(json, _opts) ?? new PluginStateDto();

                var settings = dto.Settings ?? new PluginSettings();

                if (dto.Sessions != null && dto.Sessions.Count > 0)
                {
                    var active = !string.IsNullOrWhiteSpace(dto.ActiveChatId) ? dto.ActiveChatId : dto.Sessions[0].Transcript?.ChatId ?? "";
                    return (settings, dto.Sessions, active);
                }

                // Legacy migration: V1 stored a single transcript (global single-chat).
                if (dto.Transcript != null)
                {
                    var legacyTranscript = dto.Transcript;
                    var legacySession = new ChatSession
                    {
                        Title = "Chat",
                        SelectedModelId = "",
                        Transcript = legacyTranscript
                    };

                    return (settings, new List<ChatSession> { legacySession }, legacyTranscript.ChatId);
                }

                return (settings, new List<ChatSession>(), "");
            }
            catch
            {
                return (new PluginSettings(), new List<ChatSession>(), "");
            }
        }

        public void Save(PluginSettings settings, List<ChatSession> sessions, string activeChatId)
        {
            try
            {
                var dir = Path.GetDirectoryName(_statePath);
                if (!string.IsNullOrWhiteSpace(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);
                var dto = new PluginStateDto { Settings = settings, Sessions = sessions, ActiveChatId = activeChatId };
                File.WriteAllText(_statePath, JsonSerializer.Serialize(dto, _opts));
            }
            catch
            {
            }
        }

        private sealed class PluginStateDto
        {
            public PluginSettings? Settings { get; set; }

            // New format (multi-chat).
            public List<ChatSession>? Sessions { get; set; }
            public string? ActiveChatId { get; set; }

            // Legacy format (single transcript).
            public ChatTranscript? Transcript { get; set; }
        }
    }
}

