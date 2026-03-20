using System;
using UnityEngine.UIElements;
using UnityLLM.Editor.Architecture.Model;

namespace UnityLLM.Editor.Architecture.View
{
    public sealed class SettingsTabView
    {
        private readonly VisualElement _root;

        private readonly TextField _backendUrlField;
        private readonly Toggle _toolsEnabledToggle;
        private readonly TextField _composerModeField;
        private readonly Button _applySettingsButton;

        public SettingsTabView(VisualElement root)
        {
            _root = root;
            _backendUrlField = root.Q<TextField>("BackendUrlField");
            _toolsEnabledToggle = root.Q<Toggle>("ToolsEnabledToggle");
            _composerModeField = root.Q<TextField>("ComposerModeField");
            _applySettingsButton = root.Q<Button>("ApplySettingsButton");
        }

        public void BindApplyHandler(Action<PluginSettings> onApply)
        {
            if (_applySettingsButton == null) return;
            _applySettingsButton.clicked += () =>
            {
                var settings = GetCurrentSettings();
                onApply?.Invoke(settings);
            };
        }

        public PluginSettings GetCurrentSettings()
        {
            var s = new PluginSettings();
            if (_backendUrlField != null)
                s.BackendBaseUrl = (_backendUrlField.value ?? string.Empty).Trim();
            if (_toolsEnabledToggle != null)
                s.ToolsEnabled = _toolsEnabledToggle.value;
            if (_composerModeField != null)
                s.ComposerMode = (_composerModeField.value ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(s.BackendBaseUrl))
                s.BackendBaseUrl = "http://127.0.0.1:8001";

            return s;
        }

        public void ApplySettings(PluginSettings settings)
        {
            if (settings == null) return;
            if (_backendUrlField != null) _backendUrlField.value = settings.BackendBaseUrl;
            if (_toolsEnabledToggle != null) _toolsEnabledToggle.value = settings.ToolsEnabled;
            if (_composerModeField != null) _composerModeField.value = settings.ComposerMode;
        }
    }
}

