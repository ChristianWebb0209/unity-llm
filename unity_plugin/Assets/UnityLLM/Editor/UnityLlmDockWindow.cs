using System;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;
using UnityLLM.Editor.Architecture.Controller;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Architecture.View;
using UnityLLM.Editor.Docking;

namespace UnityLLM.Editor
{
    public class UnityLlmDockWindow : EditorWindow
    {
        private const string UxmlDockPath = "Assets/UnityLLM/Editor/UI/Uxml/DockWindow.uxml";
        private const string UxmlChatPath = "Assets/UnityLLM/Editor/UI/Uxml/Tabs/ChatTab.uxml";
        private const string UxmlTimelinePath = "Assets/UnityLLM/Editor/UI/Uxml/Tabs/TimelineTab.uxml";
        private const string UxmlSettingsPath = "Assets/UnityLLM/Editor/UI/Uxml/Tabs/SettingsTab.uxml";
        private const string UssPath = "Assets/UnityLLM/Editor/UI/Styles/unityllm.uss";

        private AIController _controller;
        private ChatTabView _chatView;
        private TimelineTabView _timelineView;
        private SettingsTabView _settingsView;

        private VisualElement _chatContainer;
        private VisualElement _timelineContainer;
        private VisualElement _settingsContainer;

        [MenuItem("UnityLLM/Assistant (V1)")]
        public static void Open()
        {
            var window = GetWindow<UnityLlmDockWindow>();
            window.titleContent = new GUIContent("UnityLLM");
            window.minSize = new Vector2(360, 220);
            DockRightHelper.DockRight(window);
        }

        public void CreateGUI()
        {
            try
            {
                var dockTree = LoadUxml(UxmlDockPath);
                rootVisualElement.Clear();
                rootVisualElement.Add(dockTree);
            }
            catch (Exception ex)
            {
                rootVisualElement.Clear();
                var fallback = new VisualElement();
                fallback.Add(new Label("UnityLLM failed to initialize."));
                fallback.Add(new Label(ex.Message));
                rootVisualElement.Add(fallback);
                return;
            }

            var uss = AssetDatabase.LoadAssetAtPath<StyleSheet>(UssPath);
            if (uss != null)
                rootVisualElement.styleSheets.Add(uss);
            else
                rootVisualElement.Add(new Label($"Missing stylesheet: {UssPath}"));

            _chatContainer = rootVisualElement.Q<VisualElement>("ChatTabContent");
            _timelineContainer = rootVisualElement.Q<VisualElement>("TimelineTabContent");
            _settingsContainer = rootVisualElement.Q<VisualElement>("SettingsTabContent");

            var chatTree = LoadUxml(UxmlChatPath);
            _chatContainer.Add(chatTree);

            var timelineTree = LoadUxml(UxmlTimelinePath);
            _timelineContainer.Add(timelineTree);

            var settingsTree = LoadUxml(UxmlSettingsPath);
            _settingsContainer.Add(settingsTree);

            _chatView = new ChatTabView(_chatContainer);
            _timelineView = new TimelineTabView(_timelineContainer);
            _settingsView = new SettingsTabView(_settingsContainer);

            _controller = new AIController();
            _controller.Initialize(
                chatView: _chatView,
                timelineView: _timelineView,
                settingsView: _settingsView,
                onSend: prompt => _controller.HandleSendPrompt(prompt, _chatView, _timelineView, _settingsView),
                onRequestSettingsApply: settings => _settingsView.ApplySettings(settings)
            );

            var chatBtn = rootVisualElement.Q<Button>("ChatTabButton");
            var timelineBtn = rootVisualElement.Q<Button>("TimelineTabButton");
            var settingsBtn = rootVisualElement.Q<Button>("SettingsTabButton");

            chatBtn.clicked += () => ShowOnly(_chatContainer, chatBtn, _timelineContainer, timelineBtn, _settingsContainer, settingsBtn);
            timelineBtn.clicked += () => ShowOnly(_timelineContainer, chatBtn, _chatContainer, timelineBtn, _settingsContainer, settingsBtn);
            settingsBtn.clicked += () => ShowOnly(_settingsContainer, chatBtn, _chatContainer, timelineBtn, _timelineContainer, settingsBtn);

            // Default tab: Chat.
            ShowOnly(_chatContainer, chatBtn, _timelineContainer, timelineBtn, _settingsContainer, settingsBtn);
        }

        private void ShowOnly(
            VisualElement show,
            Button showBtn,
            VisualElement hide1,
            Button hide1Btn,
            VisualElement hide2,
            Button hide2Btn)
        {
            if (show != null) show.style.display = DisplayStyle.Flex;
            if (hide1 != null) hide1.style.display = DisplayStyle.None;
            if (hide2 != null) hide2.style.display = DisplayStyle.None;

            // We do not toggle button visuals for V1, but keeping hooks makes later styling easier.
        }

        private static VisualElement LoadUxml(string assetPath)
        {
            var tree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(assetPath);
            if (tree == null)
            {
                var fallback = new VisualElement();
                fallback.Add(new Label($"Missing UXML: {assetPath}"));
                return fallback;
            }

            return tree.CloneTree();
        }
    }
}

