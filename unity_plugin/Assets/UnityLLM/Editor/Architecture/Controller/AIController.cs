using System;
using System.Threading;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Architecture.View;
using UnityLLM.Editor.Backend;
using UnityLLM.Editor.Stores;
using UnityLLM.Editor.Tools;

#nullable enable
namespace UnityLLM.Editor.Architecture.Controller
{
    public sealed class AIController
    {
        private ChatTabView _chatView;
        private TimelineTabView _timelineView;
        private SettingsTabView _settingsView;

        private Action<string> _onSend;
        private Action<PluginSettings> _onRequestSettingsApply;

        private SynchronizationContext? _syncContext;
        private readonly PluginStateStore _stateStore = new PluginStateStore();
        private readonly ChatTranscript _transcript = new ChatTranscript();
        private PluginSettings _settings = new PluginSettings();

        public void Initialize(
            ChatTabView chatView,
            TimelineTabView timelineView,
            SettingsTabView settingsView,
            Action<string> onSend,
            Action<PluginSettings> onRequestSettingsApply)
        {
            _chatView = chatView;
            _timelineView = timelineView;
            _settingsView = settingsView;
            _onSend = onSend;
            _onRequestSettingsApply = onRequestSettingsApply;

            _settingsView?.BindApplyHandler(s =>
            {
                _settings = s ?? new PluginSettings();
                _stateStore.Save(_settings, _transcript);
                _onRequestSettingsApply?.Invoke(_settings);
            });
            _chatView?.SetSendHandler(_onSend);

            _syncContext = SynchronizationContext.Current;

            var loaded = _stateStore.Load();
            _settings = loaded.settings;
            if (loaded.transcript != null)
            {
                foreach (var m in loaded.transcript.Messages)
                {
                    if (m.Role == ChatRole.User) _chatView.AppendUserMessage(m.Content);
                    else _chatView.AppendAssistantDelta(m.Content);
                    _transcript.Messages.Add(m);
                }
            }
            _settingsView?.ApplySettings(_settings);
            _chatView?.ClearError();
        }

        public void HandleSendPrompt(
            string prompt,
            ChatTabView chatView,
            TimelineTabView timelineView,
            SettingsTabView settingsView)
        {
            chatView?.AppendUserMessage(prompt);
            chatView?.ResetAssistantStreaming();
            chatView?.ClearError();
            timelineView?.Clear();

            _ = HandleSendPromptAsync(prompt, chatView, timelineView, settingsView);
        }

        private async System.Threading.Tasks.Task HandleSendPromptAsync(
            string prompt,
            ChatTabView chatView,
            TimelineTabView timelineView,
            SettingsTabView settingsView)
        {
            var settings = settingsView?.GetCurrentSettings() ?? _settings;
            _settings = settings;
            var backend = new BackendClient();

            string composerMode = settings.ComposerMode ?? string.Empty;
            composerMode = composerMode.Trim();

            bool useComposer = !string.IsNullOrWhiteSpace(composerMode);
            bool toolsEnabled = settings.ToolsEnabled;

            void PostToUi(Action action)
            {
                if (_syncContext != null)
                    _syncContext.Post(_ => action(), null);
                else
                    action();
            }

            try
            {
                _transcript.AddUserMessage(prompt);
                PostToUi(() => chatView.AppendAssistantDelta("")); // ensure we have a streaming label

                var editHistoryStore = new EditHistoryStore();
                var toolExecutionController = new ToolExecutionController();
                var iterationController = new IterationController();

                if (toolsEnabled)
                {
                    if (useComposer)
                    {
                        await backend.StreamComposerQueryWithToolsAsync(
                            settings.BackendBaseUrl,
                            prompt,
                            composerMode,
                            onAnswerDelta: delta => PostToUi(() => chatView.AppendAssistantDelta(delta)),
                            onToolCalls: calls =>
                            {
                                PostToUi(() =>
                                {
                                    timelineView.Clear();
                                    timelineView.AddTimelineItem($"Executing {calls.Count} tool call(s)...");

                                    try
                                    {
                                        var results = toolExecutionController.ExecuteToolCalls(calls, editHistoryStore);
                                        timelineView.SetTimelineRecords(results, rec =>
                                        {
                                            var ok = editHistoryStore.RevertEdit(rec.EditId);
                                            if (!ok) chatView.ShowError($"Revert failed for {rec.ToolName}");
                                        });

                                        var compileDiag = "";
                                        foreach (var r in results)
                                        {
                                            if (string.Equals(r.ToolName, "collect_compile_errors", StringComparison.OrdinalIgnoreCase))
                                            {
                                                compileDiag = r.NewContent ?? "";
                                                break;
                                            }
                                        }
                                        if (!string.IsNullOrWhiteSpace(compileDiag))
                                        {
                                            _ = iterationController.MaybeRequestFixFollowUpAsync(
                                                backend,
                                                settings,
                                                prompt,
                                                compileDiag,
                                                followUpIndex: 0,
                                                onNoOpAsync: null,
                                                onAnswerDelta: _ => { },
                                                onToolCalls: _ => { },
                                                onUsage: _u => { });
                                        }
                                    }
                                    catch (Exception toolExecError)
                                    {
                                        chatView.ShowError($"Tool execution failed: {toolExecError.Message}");
                                        timelineView.AddTimelineItem($"Tool execution error: {toolExecError.Message}");
                                    }
                                });
                            },
                            onUsage: usage => { },
                            cancellationToken: CancellationToken.None
                        );
                    }
                    else
                    {
                        await backend.StreamRagQueryWithToolsAsync(
                            settings.BackendBaseUrl,
                            prompt,
                            onAnswerDelta: delta => PostToUi(() => chatView.AppendAssistantDelta(delta)),
                            onToolCalls: calls =>
                            {
                                PostToUi(() =>
                                {
                                    timelineView.Clear();
                                    timelineView.AddTimelineItem($"Executing {calls.Count} tool call(s)...");

                                    try
                                    {
                                        var results = toolExecutionController.ExecuteToolCalls(calls, editHistoryStore);
                                        timelineView.SetTimelineRecords(results, rec =>
                                        {
                                            var ok = editHistoryStore.RevertEdit(rec.EditId);
                                            if (!ok) chatView.ShowError($"Revert failed for {rec.ToolName}");
                                        });

                                        var compileDiag = "";
                                        foreach (var r in results)
                                        {
                                            if (string.Equals(r.ToolName, "collect_compile_errors", StringComparison.OrdinalIgnoreCase))
                                            {
                                                compileDiag = r.NewContent ?? "";
                                                break;
                                            }
                                        }
                                        if (!string.IsNullOrWhiteSpace(compileDiag))
                                        {
                                            _ = iterationController.MaybeRequestFixFollowUpAsync(
                                                backend,
                                                settings,
                                                prompt,
                                                compileDiag,
                                                followUpIndex: 0,
                                                onNoOpAsync: null,
                                                onAnswerDelta: _ => { },
                                                onToolCalls: _ => { },
                                                onUsage: _u => { });
                                        }
                                    }
                                    catch (Exception toolExecError)
                                    {
                                        chatView.ShowError($"Tool execution failed: {toolExecError.Message}");
                                        timelineView.AddTimelineItem($"Tool execution error: {toolExecError.Message}");
                                    }
                                });
                            },
                            onUsage: usage => { },
                            cancellationToken: CancellationToken.None
                        );
                    }
                }
                else
                {
                    if (useComposer)
                    {
                        await backend.StreamComposerQueryAnswerOnlyAsync(
                            settings.BackendBaseUrl,
                            prompt,
                            composerMode,
                            onAnswerDelta: delta => PostToUi(() => chatView.AppendAssistantDelta(delta)),
                            cancellationToken: CancellationToken.None
                        );
                    }
                    else
                    {
                        await backend.StreamRagQueryAnswerOnlyAsync(
                            settings.BackendBaseUrl,
                            prompt,
                            onAnswerDelta: delta => PostToUi(() => chatView.AppendAssistantDelta(delta)),
                            cancellationToken: CancellationToken.None
                        );
                    }
                }
                _transcript.AddAssistantMessage(chatView.GetAssistantStreamingText());
                _stateStore.Save(_settings, _transcript);
            }
            catch (Exception e)
            {
                PostToUi(() => chatView.ShowError($"Request failed: {e.Message}"));
            }
        }

        // Kept for parity with later controller refactors.
        public void HandleSendPrompt(string prompt)
        {
            HandleSendPrompt(prompt, _chatView, _timelineView, _settingsView);
        }
    }
}

