using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.UIElements;
using UnityLLM.Editor.Architecture.Model;

namespace UnityLLM.Editor.Architecture.View
{
    public sealed class ChatTabView
    {
        private readonly VisualElement _root;
        private readonly ScrollView _chatTabsScrollView;
        private readonly VisualElement _chatTabsContent;
        private readonly Button _newChatButton;
        private readonly DropdownField _modelDropdown;

        private readonly ScrollView _messagesScroll;
        private readonly TextField _promptInput;
        private readonly Button _sendButton;
        private readonly Label _errorLabel;

        private readonly StringBuilder _assistantStreamingBuffer = new StringBuilder();
        private VisualElement? _assistantStreamingContainer;
        private Label? _assistantStreamingLabel;

        private readonly List<Button> _chatTabButtons = new List<Button>();
        private readonly Dictionary<string, string> _modelChoiceToId = new Dictionary<string, string>();

        private Action? _onNewChat;
        private Action<string>? _onSwitchChat;
        private Action<string>? _onModelSelected;
        private Action<string>? _onSend;

        private bool _isBusy;
        private bool _isProgrammaticModelUpdate;
        private int _textSize = 14;

        public ChatTabView(VisualElement root)
        {
            _root = root;
            _chatTabsScrollView = root.Q<ScrollView>("ChatTabsScrollView");
            _newChatButton = root.Q<Button>("NewChatButton");
            _modelDropdown = root.Q<DropdownField>("ModelDropdown");
            _messagesScroll = root.Q<ScrollView>("ChatMessages");
            _promptInput = root.Q<TextField>("PromptInput");
            _sendButton = root.Q<Button>("SendButton");
            _errorLabel = root.Q<Label>("ChatErrorLabel");

            _chatTabsContent = new VisualElement();
            _chatTabsContent.style.flexDirection = FlexDirection.Row;
            _chatTabsContent.style.gap = 6;
            _chatTabsContent.AddToClassList("unityllm-chat-tabs-content");

            if (_chatTabsScrollView != null)
                _chatTabsScrollView.Add(_chatTabsContent);

            // Keep dropdown changes wired even before the first model choices arrive.
            _modelDropdown.RegisterValueChangedCallback(evt =>
            {
                if (_isBusy) return;
                if (_isProgrammaticModelUpdate) return;
                if (evt?.newValue == null) return;

                var display = evt.newValue.ToString() ?? "";
                if (_modelChoiceToId.TryGetValue(display, out var modelId))
                    _onModelSelected?.Invoke(modelId);
            });
        }

        public void BindHandlers(
            Action? onNewChat,
            Action<string>? onSwitchChat,
            Action<string>? onModelSelected,
            Action<string>? onSend)
        {
            _onNewChat = onNewChat;
            _onSwitchChat = onSwitchChat;
            _onModelSelected = onModelSelected;
            _onSend = onSend;

            _newChatButton.clicked += () =>
            {
                if (_isBusy) return;
                _onNewChat?.Invoke();
            };

            _sendButton.clicked += () =>
            {
                var prompt = (_promptInput.value ?? string.Empty).Trim();
                if (string.IsNullOrWhiteSpace(prompt))
                    return;

                _promptInput.value = string.Empty; // UX: clear immediately after send.
                _onSend?.Invoke(prompt);
            };
        }

        public void SetBusy(bool busy)
        {
            _isBusy = busy;
            _sendButton?.SetEnabled(!busy);
            _newChatButton?.SetEnabled(!busy);
            _modelDropdown?.SetEnabled(!busy);
            _promptInput?.SetEnabled(!busy);

            foreach (var btn in _chatTabButtons)
                btn.SetEnabled(!busy);
        }

        public void SetTextSize(int size)
        {
            _textSize = size > 0 ? size : 14;
        }

        public void SetModelChoices(IReadOnlyList<ModelDefinition> models, string selectedModelId)
        {
            _modelChoiceToId.Clear();

            var choices = new List<string>();
            if (models != null)
            {
                foreach (var m in models)
                {
                    if (m == null) continue;
                    if (string.IsNullOrWhiteSpace(m.Id)) continue;

                    var display = string.IsNullOrWhiteSpace(m.DisplayName)
                        ? m.Id
                        : $"{m.DisplayName} ({m.Id})";

                    choices.Add(display);
                    _modelChoiceToId[display] = m.Id;
                }
            }

            _modelDropdown.choices = choices;

            var selectedDisplay = choices.FirstOrDefault(c => _modelChoiceToId.TryGetValue(c, out var id) && id == selectedModelId);
            if (string.IsNullOrWhiteSpace(selectedDisplay) && choices.Count > 0)
                selectedDisplay = choices[0];

            _isProgrammaticModelUpdate = true;
            try
            {
                _modelDropdown.value = selectedDisplay;
            }
            finally
            {
                _isProgrammaticModelUpdate = false;
            }
        }

        public void SetChatSessions(IReadOnlyList<ChatSession> sessions, string activeChatId)
        {
            _chatTabsContent.Clear();
            _chatTabButtons.Clear();

            if (sessions == null) return;

            foreach (var s in sessions)
            {
                if (s == null || s.Transcript == null) continue;
                var chatId = s.Transcript.ChatId;
                var title = string.IsNullOrWhiteSpace(s.Title) ? chatId : s.Title;

                var tabBtn = new Button(() => _onSwitchChat?.Invoke(chatId));
                tabBtn.text = title;
                tabBtn.AddToClassList("unityllm-chat-tab");
                if (string.Equals(chatId, activeChatId, StringComparison.Ordinal))
                    tabBtn.AddToClassList("unityllm-chat-tab-active");

                _chatTabsContent.Add(tabBtn);
                _chatTabButtons.Add(tabBtn);
            }

            SetBusy(_isBusy);
        }

        public void RenderTranscript(ChatTranscript transcript, bool animateMessages)
        {
            ClearMessages();
            if (transcript?.Messages == null) return;

            foreach (var m in transcript.Messages)
            {
                if (m == null) continue;
                if (m.Role == ChatRole.User)
                    AddMessageBubble(m.Content, isUser: true, animate: animateMessages);
                else
                    AddMessageBubble(m.Content, isUser: false, animate: animateMessages);
            }
        }

        public void ClearMessages()
        {
            _messagesScroll.Clear();
            _assistantStreamingBuffer.Clear();
            _assistantStreamingContainer = null;
            _assistantStreamingLabel = null;
        }

        public void AppendUserMessage(string message)
        {
            AddMessageBubble(message, isUser: true, animate: true);
        }

        public void BeginAssistantStreaming()
        {
            _assistantStreamingBuffer.Clear();
            _assistantStreamingContainer = CreateMessageBubble(isUser: false, animate: true);
            _assistantStreamingLabel = _assistantStreamingContainer.Q<Label>();
            _messagesScroll.Add(_assistantStreamingContainer);
            _messagesScroll.scrollOffset = new Vector2(0, _messagesScroll.contentRect.height);
        }

        public void ResetAssistantStreaming()
        {
            _assistantStreamingBuffer.Clear();
            _assistantStreamingLabel = null;
            _assistantStreamingContainer = null;
        }

        public void AppendAssistantDelta(string delta)
        {
            if (string.IsNullOrEmpty(delta))
            {
                // If streaming starts, callers may send an empty delta to create the placeholder.
                if (_assistantStreamingLabel == null)
                    BeginAssistantStreaming();
                return;
            }

            if (_assistantStreamingLabel == null)
                BeginAssistantStreaming();

            _assistantStreamingBuffer.Append(delta);
            if (_assistantStreamingLabel != null)
                _assistantStreamingLabel.text = _assistantStreamingBuffer.ToString();

            _messagesScroll.scrollOffset = new Vector2(0, _messagesScroll.contentRect.height);
        }

        public void ShowError(string message)
        {
            if (_errorLabel == null) return;
            _errorLabel.text = message;
            _errorLabel.style.display = DisplayStyle.Flex;
        }

        public void ClearError()
        {
            if (_errorLabel == null) return;
            _errorLabel.text = "";
            _errorLabel.style.display = DisplayStyle.None;
        }

        public void ClearPromptInput()
        {
            if (_promptInput == null) return;
            _promptInput.value = string.Empty;
        }

        private void AddMessageBubble(string content, bool isUser, bool animate)
        {
            var bubble = CreateMessageBubble(isUser: isUser, animate: animate);
            var label = bubble.Q<Label>();
            if (label != null)
                label.text = content ?? "";

            _messagesScroll.Add(bubble);
            _messagesScroll.scrollOffset = new Vector2(0, _messagesScroll.contentRect.height);
        }

        private VisualElement CreateMessageBubble(bool isUser, bool animate)
        {
            var bubble = new VisualElement();
            bubble.AddToClassList("unityllm-chat-message");
            bubble.AddToClassList(isUser ? "unityllm-chat-message-user" : "unityllm-chat-message-assistant");

            // For smooth send animations: initial invisible -> visible in the next frame.
            if (animate)
            {
                bubble.AddToClassList("unityllm-chat-message-enter");
                bubble.style.opacity = 0f;
                bubble.style.translate = new Translate(0, -6, 0);
            }

            var label = new Label();
            label.style.unityTextAlign = isUser ? TextAnchor.MiddleRight : TextAnchor.MiddleLeft;
            label.style.fontSize = _textSize;
            bubble.Add(label);

            if (animate)
            {
                _root.schedule.Execute(() =>
                {
                    bubble.style.opacity = 1f;
                    bubble.style.translate = new Translate(0, 0, 0);
                    bubble.RemoveFromClassList("unityllm-chat-message-enter");
                }).StartingIn(1);
            }

            return bubble;
        }
    }
}

