using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Stores;

namespace UnityLLM.Editor.Debug
{
    public static class UnityLlmSmokeTests
    {
        [MenuItem("UnityLLM/Debug/Smoke Test: Plugin State Multi-Chat")]
        public static void SmokeTestPluginStateMultiChat()
        {
            var store = new PluginStateStore();

            var settings = new PluginSettings
            {
                BackendBaseUrl = "http://127.0.0.1:8001",
                ToolsEnabled = true,
                ComposerMode = "agent",
                TextSize = 16,
                ModelDefinitions = new List<ModelDefinition>
                {
                    new ModelDefinition { Id = "gpt-4.1-mini", DisplayName = "Default" },
                    new ModelDefinition { Id = "unity-composer", DisplayName = "Composer" }
                }
            };

            var s1 = new ChatSession
            {
                Title = "Chat A",
                SelectedModelId = "gpt-4.1-mini",
                SelectedModelKind = "auto",
                Transcript = new ChatTranscript()
            };
            s1.Transcript.AddUserMessage("Hi from smoke test.");
            s1.Transcript.AddAssistantMessage("Assistant response from smoke test.");

            var s2 = new ChatSession
            {
                Title = "Chat B",
                SelectedModelId = "unity-composer",
                SelectedModelKind = "auto",
                Transcript = new ChatTranscript()
            };
            s2.Transcript.AddUserMessage("Second chat, different model selection.");
            s2.Transcript.AddAssistantMessage("Second chat assistant response.");

            var sessions = new List<ChatSession> { s1, s2 };
            var active = s2.Transcript.ChatId;

            store.Save(settings, sessions, active);

            var loaded = store.Load();

            var loadedSessionsCount = loaded.sessions != null ? loaded.sessions.Count : 0;
            Debug.Log(
                $"[UnityLLM SmokeTest] Loaded sessions={loadedSessionsCount} activeChatId={loaded.activeChatId} settings.TextSize={loaded.settings?.TextSize}");

            if (loadedSessionsCount < 2)
                Debug.LogError("[UnityLLM SmokeTest] Expected at least 2 chat sessions in loaded state.");
            if (string.IsNullOrWhiteSpace(loaded.activeChatId))
                Debug.LogError("[UnityLLM SmokeTest] Expected activeChatId to be non-empty.");
        }
    }
}

