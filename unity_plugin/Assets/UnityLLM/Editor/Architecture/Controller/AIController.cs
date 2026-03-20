using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
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

        private SynchronizationContext? _syncContext;
        private readonly PluginStateStore _stateStore = new PluginStateStore();
        private readonly StringBuilder _assistantBuffer = new StringBuilder();

        private PluginSettings _settings = new PluginSettings();
        private readonly List<ChatSession> _sessions = new List<ChatSession>();
        private string _activeChatId = "";

        private bool _isBusy;
        private int _lastTextSizeApplied;

        public void Initialize(
            ChatTabView chatView,
            TimelineTabView timelineView,
            SettingsTabView settingsView)
        {
            _chatView = chatView;
            _timelineView = timelineView;
            _settingsView = settingsView;

            _syncContext = SynchronizationContext.Current;

            // Bind settings changes: save immediately on every field edit.
            _settingsView?.BindSettingsChangedHandler(s =>
            {
                _settings = s ?? new PluginSettings();
                PersistState();
                ApplySettingsToUi();
            });

            // Bind chat UI events.
            _chatView?.BindHandlers(
                onNewChat: CreateNewChat,
                onSwitchChat: SelectChat,
                onModelSelected: SetActiveChatModel,
                onSend: HandleSendPrompt);

            LoadFromStateStore();
            EnsureActiveChatExists();

            _settingsView?.ApplySettings(_settings);
            ApplySettingsToUi();

            _chatView?.SetChatSessions(_sessions, _activeChatId);
            var active = GetActiveSession();
            if (active != null)
                _chatView?.RenderTranscript(active.Transcript, animateMessages: false);

            _chatView?.ClearError();
            _timelineView?.Clear();
        }

        private void LoadFromStateStore()
        {
            _sessions.Clear();

            var loaded = _stateStore.Load();
            _settings = loaded.settings ?? new PluginSettings();
            _activeChatId = loaded.activeChatId ?? "";

            if (loaded.sessions != null)
                _sessions.AddRange(loaded.sessions);

            if (_sessions.Count > 0)
            {
                var defaultModelId = GetDefaultModelId();
                foreach (var s in _sessions)
                {
                    if (s == null || s.Transcript == null) continue;
                    if (string.IsNullOrWhiteSpace(s.SelectedModelId) && !string.IsNullOrWhiteSpace(defaultModelId))
                        s.SelectedModelId = defaultModelId;
                }
            }

            if (_sessions.Count == 0)
            {
                var first = CreateEmptyChatSession(defaultTitle: "Chat 1");
                _sessions.Add(first);
                _activeChatId = first.Transcript.ChatId;
            }
        }

        private void EnsureActiveChatExists()
        {
            if (_sessions.Count == 0) return;

            var activeExists = !string.IsNullOrWhiteSpace(_activeChatId) &&
                                _sessions.Any(s => s != null && s.Transcript != null && s.Transcript.ChatId == _activeChatId);

            if (!activeExists)
                _activeChatId = _sessions[0].Transcript.ChatId;
        }

        private ChatSession CreateEmptyChatSession(string defaultTitle)
        {
            var session = new ChatSession
            {
                Title = defaultTitle ?? "Chat",
                SelectedModelId = GetDefaultModelId(),
                SelectedModelKind = "auto",
                Transcript = new ChatTranscript()
            };

            return session;
        }

        private string GetDefaultModelId()
        {
            if (_settings?.ModelDefinitions != null && _settings.ModelDefinitions.Count > 0)
            {
                var first = _settings.ModelDefinitions[0];
                if (first != null && !string.IsNullOrWhiteSpace(first.Id))
                    return first.Id;
            }

            return string.Empty;
        }

        private ChatSession? GetActiveSession()
        {
            if (_sessions.Count == 0) return null;
            return _sessions.FirstOrDefault(s => s != null && s.Transcript != null && s.Transcript.ChatId == _activeChatId);
        }

        private void PersistState()
        {
            _stateStore.Save(_settings, _sessions, _activeChatId);
        }

        private void ApplySettingsToUi()
        {
            _chatView?.SetTextSize(_settings.TextSize);

            var active = GetActiveSession();
            var selectedModelId = active?.SelectedModelId ?? "";
            _chatView?.SetModelChoices(_settings.ModelDefinitions, selectedModelId);

            // Re-render to update font sizes for existing bubbles.
            if (active != null && _settings.TextSize != _lastTextSizeApplied)
            {
                _lastTextSizeApplied = _settings.TextSize;
                _chatView?.RenderTranscript(active.Transcript, animateMessages: false);
            }
        }

        private void CreateNewChat()
        {
            if (_isBusy) return;

            var session = CreateEmptyChatSession($"Chat {_sessions.Count + 1}");
            _sessions.Add(session);
            _activeChatId = session.Transcript.ChatId;

            PersistState();

            _chatView?.SetChatSessions(_sessions, _activeChatId);
            ApplySettingsToUi();
            _chatView?.RenderTranscript(session.Transcript, animateMessages: false);
            _timelineView?.Clear();
            _chatView?.ClearError();
        }

        private void SelectChat(string chatId)
        {
            if (_isBusy) return;
            if (string.IsNullOrWhiteSpace(chatId)) return;

            var target = _sessions.FirstOrDefault(s => s != null && s.Transcript != null && s.Transcript.ChatId == chatId);
            if (target == null) return;

            _activeChatId = chatId;
            PersistState();

            _chatView?.SetChatSessions(_sessions, _activeChatId);
            ApplySettingsToUi();
            _chatView?.RenderTranscript(target.Transcript, animateMessages: false);
            _timelineView?.Clear();
            _chatView?.ClearError();
        }

        private void SetActiveChatModel(string modelId)
        {
            if (_isBusy) return;
            var active = GetActiveSession();
            if (active == null) return;

            active.SelectedModelId = modelId ?? "";
            PersistState();
            ApplySettingsToUi();
        }

        private void HandleSendPrompt(string prompt)
        {
            if (_isBusy) return;
            if (string.IsNullOrWhiteSpace(prompt)) return;

            _ = HandleSendPromptAsync(prompt);
        }

        private async Task HandleSendPromptAsync(string prompt)
        {
            var session = GetActiveSession();
            if (session == null) return;

            _isBusy = true;
            _chatView?.SetBusy(true);
            _timelineView?.Clear();
            _chatView?.ClearError();

            _assistantBuffer.Clear();

            // Update both UI and persisted state immediately.
            session.Transcript.AddUserMessage(prompt);
            _chatView?.AppendUserMessage(prompt);

            // Placeholder assistant message for persistence mid-stream.
            var assistantIndex = session.Transcript.BeginAssistantMessage();

            PostToUi(() =>
            {
                _chatView?.BeginAssistantStreaming();
            });

            PersistState();

            var backend = new BackendClient();

            string composerMode = (_settings.ComposerMode ?? string.Empty).Trim();
            bool useComposer = !string.IsNullOrWhiteSpace(composerMode);
            bool toolsEnabled = _settings.ToolsEnabled;

            var modelOverride = string.IsNullOrWhiteSpace(session.SelectedModelId) ? null : session.SelectedModelId;

            var editHistoryStore = new EditHistoryStore();
            var toolExecutionController = new ToolExecutionController();
            var iterationController = new IterationController();
            var contextExtraInitial = ContextDecider.BuildContextExtra(prompt, editHistoryStore, session.Transcript);

            try
            {
                if (toolsEnabled)
                {
                    if (useComposer)
                    {
                        await backend.StreamComposerQueryWithToolsAsync(
                            _settings.BackendBaseUrl,
                            prompt,
                            composerMode,
                            contextExtraInitial,
                            onAnswerDelta: delta =>
                            {
                                if (string.IsNullOrEmpty(delta)) return;
                                _assistantBuffer.Append(delta);
                                session.Transcript.UpdateAssistantMessageContent(assistantIndex, _assistantBuffer.ToString());
                                PostToUi(() => _chatView?.AppendAssistantDelta(delta));
                            },
                            onToolCalls: calls =>
                            {
                                PostToUi(() =>
                                {
                                    _timelineView.Clear();
                                    _timelineView.AddTimelineItem($"Executing {calls.Count} tool call(s)...");

                                    try
                                    {
                                        var results = toolExecutionController.ExecuteToolCalls(calls, editHistoryStore);
                                        _timelineView.SetTimelineRecords(results, rec =>
                                        {
                                            var ok = editHistoryStore.RevertEdit(rec.EditId);
                                            if (!ok) _chatView.ShowError($"Revert failed for {rec.ToolName}");
                                        });

                                        var lintDiag = "";
                                        var compileDiag = "";
                                        foreach (var r in results)
                                        {
                                            if (string.Equals(r.ToolName, "lint_file", StringComparison.OrdinalIgnoreCase))
                                                lintDiag = r.NewContent ?? "";
                                            if (string.Equals(r.ToolName, "collect_compile_errors", StringComparison.OrdinalIgnoreCase))
                                                compileDiag = r.NewContent ?? "";
                                        }

                                        var diag = !string.IsNullOrWhiteSpace(lintDiag) ? lintDiag : compileDiag;
                                        if (!string.IsNullOrWhiteSpace(diag))
                                        {
                                            var contextExtraFollowUp = ContextDecider.BuildContextExtra(prompt, editHistoryStore, session.Transcript);
                                            _ = iterationController.MaybeRequestFixFollowUpAsync(
                                                backend,
                                                _settings,
                                                prompt,
                                                diag,
                                                followUpIndex: 0,
                                                baseContextExtra: contextExtraFollowUp,
                                                onNoOpAsync: null,
                                                onAnswerDelta: _ => { },
                                                onToolCalls: _ => { },
                                                onUsage: _u => { },
                                                modelOverride: modelOverride);
                                        }
                                    }
                                    catch (Exception toolExecError)
                                    {
                                        _chatView.ShowError($"Tool execution failed: {toolExecError.Message}");
                                        _timelineView.AddTimelineItem($"Tool execution error: {toolExecError.Message}");
                                    }
                                });
                            },
                            onUsage: usage => { },
                            cancellationToken: CancellationToken.None,
                            modelOverride: modelOverride);
                    }
                    else
                    {
                        await backend.StreamRagQueryWithToolsAsync(
                            _settings.BackendBaseUrl,
                            prompt,
                            contextExtraInitial,
                            onAnswerDelta: delta =>
                            {
                                if (string.IsNullOrEmpty(delta)) return;
                                _assistantBuffer.Append(delta);
                                session.Transcript.UpdateAssistantMessageContent(assistantIndex, _assistantBuffer.ToString());
                                PostToUi(() => _chatView?.AppendAssistantDelta(delta));
                            },
                            onToolCalls: calls =>
                            {
                                PostToUi(() =>
                                {
                                    _timelineView.Clear();
                                    _timelineView.AddTimelineItem($"Executing {calls.Count} tool call(s)...");

                                    try
                                    {
                                        var results = toolExecutionController.ExecuteToolCalls(calls, editHistoryStore);
                                        _timelineView.SetTimelineRecords(results, rec =>
                                        {
                                            var ok = editHistoryStore.RevertEdit(rec.EditId);
                                            if (!ok) _chatView.ShowError($"Revert failed for {rec.ToolName}");
                                        });

                                        var lintDiag = "";
                                        var compileDiag = "";
                                        foreach (var r in results)
                                        {
                                            if (string.Equals(r.ToolName, "lint_file", StringComparison.OrdinalIgnoreCase))
                                                lintDiag = r.NewContent ?? "";
                                            if (string.Equals(r.ToolName, "collect_compile_errors", StringComparison.OrdinalIgnoreCase))
                                                compileDiag = r.NewContent ?? "";
                                        }

                                        var diag = !string.IsNullOrWhiteSpace(lintDiag) ? lintDiag : compileDiag;
                                        if (!string.IsNullOrWhiteSpace(diag))
                                        {
                                            var contextExtraFollowUp = ContextDecider.BuildContextExtra(prompt, editHistoryStore, session.Transcript);
                                            _ = iterationController.MaybeRequestFixFollowUpAsync(
                                                backend,
                                                _settings,
                                                prompt,
                                                diag,
                                                followUpIndex: 0,
                                                baseContextExtra: contextExtraFollowUp,
                                                onNoOpAsync: null,
                                                onAnswerDelta: _ => { },
                                                onToolCalls: _ => { },
                                                onUsage: _u => { },
                                                modelOverride: modelOverride);
                                        }
                                    }
                                    catch (Exception toolExecError)
                                    {
                                        _chatView.ShowError($"Tool execution failed: {toolExecError.Message}");
                                        _timelineView.AddTimelineItem($"Tool execution error: {toolExecError.Message}");
                                    }
                                });
                            },
                            onUsage: usage => { },
                            cancellationToken: CancellationToken.None,
                            modelOverride: modelOverride);
                    }
                }
                else
                {
                    if (useComposer)
                    {
                        await backend.StreamComposerQueryAnswerOnlyAsync(
                            _settings.BackendBaseUrl,
                            prompt,
                            composerMode,
                            onAnswerDelta: delta =>
                            {
                                if (string.IsNullOrEmpty(delta)) return;
                                _assistantBuffer.Append(delta);
                                session.Transcript.UpdateAssistantMessageContent(assistantIndex, _assistantBuffer.ToString());
                                PostToUi(() => _chatView?.AppendAssistantDelta(delta));
                            },
                            cancellationToken: CancellationToken.None,
                            modelOverride: modelOverride);
                    }
                    else
                    {
                        await backend.StreamRagQueryAnswerOnlyAsync(
                            _settings.BackendBaseUrl,
                            prompt,
                            onAnswerDelta: delta =>
                            {
                                if (string.IsNullOrEmpty(delta)) return;
                                _assistantBuffer.Append(delta);
                                session.Transcript.UpdateAssistantMessageContent(assistantIndex, _assistantBuffer.ToString());
                                PostToUi(() => _chatView?.AppendAssistantDelta(delta));
                            },
                            cancellationToken: CancellationToken.None,
                            modelOverride: modelOverride);
                    }
                }

                PostToUi(() => _chatView?.ResetAssistantStreaming());
                PersistState();
            }
            catch (Exception e)
            {
                PostToUi(() => _chatView?.ShowError($"Request failed: {e.Message}"));
            }
            finally
            {
                _isBusy = false;
                PostToUi(() => _chatView?.SetBusy(false));
            }
        }

        private void PostToUi(Action action)
        {
            if (action == null) return;
            if (_syncContext != null)
                _syncContext.Post(_ => action(), null);
            else
                action();
        }
    }
}

