using System;
using System.Text;
using UnityEngine;
using UnityEngine.UIElements;

namespace UnityLLM.Editor.Architecture.View
{
    public sealed class ChatTabView
    {
        private readonly VisualElement _root;
        private readonly ScrollView _messagesScroll;
        private readonly TextField _promptInput;
        private readonly Button _sendButton;
        private readonly Label _errorLabel;

        private readonly StringBuilder _assistantStreamingBuffer = new StringBuilder();

        public ChatTabView(VisualElement root)
        {
            _root = root;
            _messagesScroll = root.Q<ScrollView>("ChatMessages");
            _promptInput = root.Q<TextField>("PromptInput");
            _sendButton = root.Q<Button>("SendButton");
            _errorLabel = root.Q<Label>("ChatErrorLabel");
        }

        public void SetSendHandler(Action<string> onSend)
        {
            _sendButton.clicked += () =>
            {
                var prompt = (_promptInput.value ?? string.Empty).Trim();
                if (string.IsNullOrWhiteSpace(prompt))
                    return;

                onSend?.Invoke(prompt);
            };
        }

        public void AppendUserMessage(string message)
        {
            var msg = new Label(message);
            msg.style.unityTextAlign = TextAnchor.MiddleRight;
            _messagesScroll.Add(msg);
            _messagesScroll.scrollOffset = new Vector2(0, _messagesScroll.contentRect.height);
        }

        public void ResetAssistantStreaming()
        {
            _assistantStreamingBuffer.Clear();
        }

        public string GetAssistantStreamingText()
        {
            return _assistantStreamingBuffer.ToString();
        }

        public void AppendAssistantDelta(string delta)
        {
            if (string.IsNullOrEmpty(delta))
                return;

            _assistantStreamingBuffer.Append(delta);

            // V1: render as a single label that we replace each time.
            var labelName = "AssistantStreamingLabel";
            var existing = _root.Q<Label>(labelName);
            if (existing == null)
            {
                existing = new Label(_assistantStreamingBuffer.ToString());
                existing.name = labelName;
                existing.style.unityTextAlign = TextAnchor.MiddleLeft;
                _messagesScroll.Add(existing);
            }
            else
            {
                existing.text = _assistantStreamingBuffer.ToString();
            }

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
    }
}

