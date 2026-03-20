using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
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
        private readonly IntegerField _textSizeField;
        private readonly TextField _modelDefinitionsJsonField;
        private readonly Label _modelDefinitionsErrorLabel;
        private readonly Button _applySettingsButton;

        private bool _suppressChangeEvents;
        private List<ModelDefinition> _lastValidModelDefinitions = new List<ModelDefinition>();

        public SettingsTabView(VisualElement root)
        {
            _root = root;
            _backendUrlField = root.Q<TextField>("BackendUrlField");
            _toolsEnabledToggle = root.Q<Toggle>("ToolsEnabledToggle");
            _composerModeField = root.Q<TextField>("ComposerModeField");
            _textSizeField = root.Q<IntegerField>("TextSizeField");
            _modelDefinitionsJsonField = root.Q<TextField>("ModelDefinitionsJsonField");
            _modelDefinitionsErrorLabel = root.Q<Label>("ModelDefinitionsErrorLabel");
            _applySettingsButton = root.Q<Button>("ApplySettingsButton");
        }

        public void BindSettingsChangedHandler(Action<PluginSettings> onChanged)
        {
            if (onChanged == null) return;

            void notify()
            {
                if (_suppressChangeEvents) return;
                onChanged.Invoke(GetCurrentSettings());
            }

            _backendUrlField?.RegisterValueChangedCallback(_ => notify());
            _toolsEnabledToggle?.RegisterValueChangedCallback(_ => notify());
            _composerModeField?.RegisterValueChangedCallback(_ => notify());
            _textSizeField?.RegisterValueChangedCallback(_ => notify());
            _modelDefinitionsJsonField?.RegisterValueChangedCallback(_ => notify());

            if (_applySettingsButton != null)
                _applySettingsButton.clicked += () => notify();
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
            if (_textSizeField != null)
                s.TextSize = _textSizeField.value;
            if (_modelDefinitionsJsonField != null)
                s.ModelDefinitionsJson = (_modelDefinitionsJsonField.value ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(s.BackendBaseUrl))
                s.BackendBaseUrl = "http://127.0.0.1:8001";

            // Best-effort parsing for dropdown; keep last valid results on parse errors.
            s.ModelDefinitions = new List<ModelDefinition>();
            var raw = s.ModelDefinitionsJson ?? "";
            if (!string.IsNullOrWhiteSpace(raw))
            {
                try
                {
                    var opts = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
                    var parsed = JsonSerializer.Deserialize<List<ModelDefinition>>(raw, opts);
                    if (parsed != null)
                    {
                        parsed = parsed
                            .Where(m => m != null && !string.IsNullOrWhiteSpace(m.Id))
                            .ToList();

                        s.ModelDefinitions = parsed;
                        _lastValidModelDefinitions = new List<ModelDefinition>(parsed);

                        if (_modelDefinitionsErrorLabel != null)
                            _modelDefinitionsErrorLabel.style.display = DisplayStyle.None;
                    }
                }
                catch (Exception ex)
                {
                    if (_modelDefinitionsErrorLabel != null)
                    {
                        _modelDefinitionsErrorLabel.text = "Model JSON parse error: " + ex.Message;
                        _modelDefinitionsErrorLabel.style.display = DisplayStyle.Flex;
                    }

                    s.ModelDefinitions = _lastValidModelDefinitions != null
                        ? new List<ModelDefinition>(_lastValidModelDefinitions)
                        : new List<ModelDefinition>();
                }
            }

            if (s.TextSize <= 0) s.TextSize = 14;

            return s;
        }

        public void ApplySettings(PluginSettings settings)
        {
            if (settings == null) return;
            _suppressChangeEvents = true;
            try
            {
                if (_backendUrlField != null) _backendUrlField.value = settings.BackendBaseUrl;
                if (_toolsEnabledToggle != null) _toolsEnabledToggle.value = settings.ToolsEnabled;
                if (_composerModeField != null) _composerModeField.value = settings.ComposerMode;
                if (_textSizeField != null) _textSizeField.value = settings.TextSize;
                if (_modelDefinitionsJsonField != null) _modelDefinitionsJsonField.value = settings.ModelDefinitionsJson;

                _lastValidModelDefinitions = settings.ModelDefinitions != null
                    ? new List<ModelDefinition>(settings.ModelDefinitions)
                    : new List<ModelDefinition>();

                if (_modelDefinitionsErrorLabel != null)
                    _modelDefinitionsErrorLabel.style.display = DisplayStyle.None;
            }
            finally
            {
                _suppressChangeEvents = false;
            }
        }
    }
}

