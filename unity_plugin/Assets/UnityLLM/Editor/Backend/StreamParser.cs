using System;
using System.Collections.Generic;
using System.Text;
using System.Text.Json;
using UnityLLM.Editor.Backend;

namespace UnityLLM.Editor.Backend
{
    /// <summary>
    /// Parses the backend streaming text format:
    ///   answer text ... then "\n__TOOL_CALLS__\n" + JSON array of tool calls
    ///   then "\n__USAGE__\n" + JSON usage object
    ///
    /// Designed to handle sentinel markers split across arbitrary stream chunk boundaries.
    /// </summary>
    public sealed class StreamParser
    {
        private const string ToolPrefix = "\n__TOOL_CALLS__\n";
        private const string UsagePrefix = "\n__USAGE__\n";

        private readonly StringBuilder _buffer = new StringBuilder();
        private int _answerEmittedUpTo = 0;

        private bool _toolCallsParsed = false;
        private IReadOnlyList<ToolCall> _toolCalls = Array.Empty<ToolCall>();

        private bool _usageParsed = false;
        private JsonElement _usageRoot;

        public event Action<string>? OnAnswerDelta;
        public event Action<IReadOnlyList<ToolCall>>? OnToolCallsParsed;
        public event Action<JsonElement>? OnUsageParsed;

        public void Consume(string chunk)
        {
            if (string.IsNullOrEmpty(chunk))
                return;

            _buffer.Append(chunk);

            var bufferStr = _buffer.ToString();

            if (!_toolCallsParsed)
            {
                int toolIdx = bufferStr.IndexOf(ToolPrefix, StringComparison.Ordinal);

                if (toolIdx >= 0)
                {
                    // Emit answer portion up to tool marker start.
                    EmitAnswerDelta(bufferStr, from: _answerEmittedUpTo, toExclusive: toolIdx);

                    // Parse tool_calls JSON once usage marker exists (so we know where JSON ends).
                    int usageIdx = bufferStr.IndexOf(UsagePrefix, toolIdx + ToolPrefix.Length, StringComparison.Ordinal);
                    if (usageIdx < 0)
                    {
                        // Wait for more chunks.
                        return;
                    }

                    var toolJson = bufferStr.Substring(toolIdx + ToolPrefix.Length, usageIdx - (toolIdx + ToolPrefix.Length)).Trim();
                    _toolCalls = ParseToolCalls(toolJson);
                    _toolCallsParsed = true;
                    OnToolCallsParsed?.Invoke(_toolCalls);

                    // Move answer emitted pointer to end of tool JSON (so future emits won't duplicate).
                    _answerEmittedUpTo = usageIdx; // usage marker start

                    // We intentionally do not parse usageRoot yet; do it in TryFinalize/at completion.
                    return;
                }

                // No tool marker yet; emit only safe prefix that cannot include the start of ToolPrefix.
                var safeMax = Math.Max(_answerEmittedUpTo, bufferStr.Length - (ToolPrefix.Length - 1));
                if (safeMax > _answerEmittedUpTo)
                {
                    EmitAnswerDelta(bufferStr, from: _answerEmittedUpTo, toExclusive: safeMax);
                    _answerEmittedUpTo = safeMax;
                }
            }
        }

        public void Complete()
        {
            if (_usageParsed)
                return;

            var bufferStr = _buffer.ToString();
            int usageIdx = bufferStr.IndexOf(UsagePrefix, StringComparison.Ordinal);
            if (usageIdx < 0)
                throw new InvalidOperationException("Stream ended but __USAGE__ marker was not found.");

            // Emit any remaining answer text before usage marker (if tool calls were parsed but answer wasn't fully emitted).
            if (!_toolCallsParsed)
            {
                // If tools weren't enabled, we might still be in answer-only mode.
                EmitAnswerDelta(bufferStr, from: _answerEmittedUpTo, toExclusive: usageIdx);
                _answerEmittedUpTo = usageIdx;
            }

            var usageJson = bufferStr.Substring(usageIdx + UsagePrefix.Length).Trim();
            if (string.IsNullOrWhiteSpace(usageJson))
                throw new InvalidOperationException("__USAGE__ marker was present but usage JSON was empty.");

            try
            {
                _usageRoot = JsonDocument.Parse(usageJson).RootElement.Clone();
            }
            catch (Exception e)
            {
                throw new InvalidOperationException("Failed to parse __USAGE__ JSON from stream.", e);
            }

            _usageParsed = true;
            OnUsageParsed?.Invoke(_usageRoot);
        }

        public IReadOnlyList<ToolCall> ToolCalls => _toolCalls;
        public JsonElement UsageRoot => _usageRoot;

        private void EmitAnswerDelta(string bufferStr, int from, int toExclusive)
        {
            if (toExclusive <= from) return;
            var delta = bufferStr.Substring(from, toExclusive - from);
            if (!string.IsNullOrEmpty(delta))
                OnAnswerDelta?.Invoke(delta);
        }

        private static IReadOnlyList<ToolCall> ParseToolCalls(string toolJson)
        {
            if (string.IsNullOrWhiteSpace(toolJson))
                throw new InvalidOperationException("__TOOL_CALLS__ marker was present but tool JSON was empty.");

            JsonDocument doc;
            try
            {
                doc = JsonDocument.Parse(toolJson);
            }
            catch (Exception e)
            {
                throw new InvalidOperationException("Failed to parse __TOOL_CALLS__ JSON from stream.", e);
            }

            if (doc.RootElement.ValueKind != JsonValueKind.Array)
                throw new InvalidOperationException("__TOOL_CALLS__ JSON must be an array.");

            var toolCalls = new List<ToolCall>();
            foreach (var el in doc.RootElement.EnumerateArray())
            {
                if (el.ValueKind != JsonValueKind.Object)
                    throw new InvalidOperationException("Tool call array element must be a JSON object.");

                if (!el.TryGetProperty("tool_name", out var toolNameEl))
                    throw new InvalidOperationException("Tool call object missing required key 'tool_name'.");

                var toolName = toolNameEl.GetString();
                if (string.IsNullOrWhiteSpace(toolName))
                    throw new InvalidOperationException("Tool call 'tool_name' must be a non-empty string.");

                var tc = new ToolCall { ToolName = toolName };

                if (el.TryGetProperty("arguments", out var argsEl))
                {
                    if (argsEl.ValueKind != JsonValueKind.Object)
                        throw new InvalidOperationException("Tool call 'arguments' must be a JSON object.");

                    foreach (var prop in argsEl.EnumerateObject())
                        tc.Arguments[prop.Name] = prop.Value.Clone();
                }

                if (el.TryGetProperty("output", out var outputEl))
                {
                    // output may be null: represent as null-able JsonElement.
                    if (outputEl.ValueKind != JsonValueKind.Null)
                        tc.Output = outputEl.Clone();
                    else
                        tc.Output = null;
                }

                toolCalls.Add(tc);
            }

            return toolCalls;
        }
    }
}

