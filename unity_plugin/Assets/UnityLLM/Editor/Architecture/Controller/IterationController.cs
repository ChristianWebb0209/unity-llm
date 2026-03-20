using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using UnityLLM.Editor.Architecture.Model;
using UnityLLM.Editor.Backend;

namespace UnityLLM.Editor.Architecture.Controller
{
    /// <summary>
    /// Bounded follow-up loop: apply tool calls -> (optionally) collect compile/lint errors ->
    /// if errors exist and we haven't exceeded the cap, ask the backend for another tool call turn
    /// including the error text as request context extras.
    /// </summary>
    public sealed class IterationController
    {
        private readonly int _maxFollowUpTurns;

        public IterationController(int maxFollowUpTurns = 2)
        {
            _maxFollowUpTurns = Math.Max(0, maxFollowUpTurns);
        }

        public async Task MaybeRequestFixFollowUpAsync(
            BackendClient backend,
            PluginSettings settings,
            string originalPrompt,
            string compileDiagnosticsText,
            int followUpIndex,
            Func<Task> onNoOpAsync,
            Action<string> onAnswerDelta,
            Action<IReadOnlyList<ToolCall>> onToolCalls,
            Action<JsonElement> onUsage)
        {
            if (backend == null) throw new ArgumentNullException(nameof(backend));
            if (settings == null) throw new ArgumentNullException(nameof(settings));
            if (string.IsNullOrWhiteSpace(originalPrompt)) throw new ArgumentException("originalPrompt is required.");

            if (followUpIndex >= _maxFollowUpTurns)
            {
                if (onNoOpAsync != null) await onNoOpAsync();
                return;
            }

            var lintOutput = (compileDiagnosticsText ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(lintOutput))
            {
                if (onNoOpAsync != null) await onNoOpAsync();
                return;
            }

            var contextExtra = new Dictionary<string, object?>
            {
                // Backend expects either "errors_text" or "lint_output".
                ["lint_output"] = lintOutput,
                ["errors_text"] = lintOutput
            };

            // Request another tool-call streaming turn using the same endpoint as initial.
            var composerMode = settings.ComposerMode ?? "";
            var useComposer = !string.IsNullOrWhiteSpace(composerMode);
            var toolsEnabled = settings.ToolsEnabled;

            // In this V1 scaffolding we always use the composer tool-call endpoint for fix turns.
            // Once a separate RAG-vs-composer selection is added, route accordingly.
            if (toolsEnabled && useComposer)
            {
                await backend.StreamComposerQueryWithToolsAsync(
                    settings.BackendBaseUrl,
                    originalPrompt,
                    composerMode,
                    contextExtra,
                    onAnswerDelta,
                    onToolCalls,
                    onUsage,
                    CancellationToken.None);
            }
            else if (toolsEnabled)
            {
                // Tools enabled but no composerMode: use the rag tools endpoint.
                await backend.StreamRagQueryWithToolsAsync(
                    settings.BackendBaseUrl,
                    originalPrompt,
                    contextExtra,
                    onAnswerDelta,
                    onToolCalls,
                    onUsage,
                    CancellationToken.None);
            }
            else
            {
                // Tools disabled: just stream answer only.
                await backend.StreamComposerQueryAnswerOnlyAsync(
                    settings.BackendBaseUrl,
                    originalPrompt,
                    composerMode,
                    onAnswerDelta,
                    CancellationToken.None);
            }
        }
    }
}

