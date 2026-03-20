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

        public (PluginSettings settings, ChatTranscript transcript) Load()
        {
            try
            {
                if (!File.Exists(_statePath))
                    return (new PluginSettings(), new ChatTranscript());
                var json = File.ReadAllText(_statePath);
                var dto = JsonSerializer.Deserialize<PluginStateDto>(json, _opts) ?? new PluginStateDto();
                return (dto.Settings ?? new PluginSettings(), dto.Transcript ?? new ChatTranscript());
            }
            catch
            {
                return (new PluginSettings(), new ChatTranscript());
            }
        }

        public void Save(PluginSettings settings, ChatTranscript transcript)
        {
            try
            {
                var dir = Path.GetDirectoryName(_statePath);
                if (!string.IsNullOrWhiteSpace(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);
                var dto = new PluginStateDto { Settings = settings, Transcript = transcript };
                File.WriteAllText(_statePath, JsonSerializer.Serialize(dto, _opts));
            }
            catch
            {
            }
        }

        private sealed class PluginStateDto
        {
            public PluginSettings? Settings { get; set; }
            public ChatTranscript? Transcript { get; set; }
        }
    }
}

