using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

#nullable enable
namespace UnityLLM.Editor.Backend
{
    /// <summary>
    /// Client for the local FastAPI backend.
    /// Supports streaming endpoints that append sentinel blocks for tool calls + usage.
    /// </summary>
    public sealed class BackendClient
    {
        private readonly HttpClient _http;

        public BackendClient(HttpClient? httpClient = null)
        {
            _http = httpClient ?? new HttpClient();
            _http.Timeout = Timeout.InfiniteTimeSpan;
        }

        public async Task StreamComposerQueryWithToolsAsync(
            string baseUrl,
            string question,
            string composerMode,
            Action<string> onAnswerDelta,
            Action<IReadOnlyList<ToolCall>> onToolCalls,
            Action<JsonElement> onUsage,
            CancellationToken cancellationToken)
        {
            await StreamComposerQueryWithToolsAsync(
                baseUrl: baseUrl,
                question: question,
                composerMode: composerMode,
                contextExtra: null,
                onAnswerDelta: onAnswerDelta,
                onToolCalls: onToolCalls,
                onUsage: onUsage,
                cancellationToken: cancellationToken);
        }

        public async Task StreamComposerQueryWithToolsAsync(
            string baseUrl,
            string question,
            string composerMode,
            Dictionary<string, object?>? contextExtra,
            Action<string> onAnswerDelta,
            Action<IReadOnlyList<ToolCall>> onToolCalls,
            Action<JsonElement> onUsage,
            CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(baseUrl))
                throw new ArgumentException("baseUrl is required");
            if (string.IsNullOrWhiteSpace(question))
                throw new ArgumentException("question is required");

            var endpoint = baseUrl.TrimEnd('/') + "/composer/query_stream_with_tools";

            var payload = new Dictionary<string, object?>
            {
                ["question"] = question,
                ["context"] = contextExtra == null ? null : new Dictionary<string, object?> { ["extra"] = contextExtra },
                ["top_k"] = 8,
                ["composer_mode"] = composerMode
            };

            await StreamWithSentinelsAsync(endpoint, payload, onAnswerDelta, onToolCalls, onUsage, cancellationToken);
        }

        public async Task StreamRagQueryWithToolsAsync(
            string baseUrl,
            string question,
            Action<string> onAnswerDelta,
            Action<IReadOnlyList<ToolCall>> onToolCalls,
            Action<JsonElement> onUsage,
            CancellationToken cancellationToken)
        {
            await StreamRagQueryWithToolsAsync(baseUrl, question, contextExtra: null, onAnswerDelta, onToolCalls, onUsage, cancellationToken);
        }

        public async Task StreamRagQueryWithToolsAsync(
            string baseUrl,
            string question,
            Dictionary<string, object?>? contextExtra,
            Action<string> onAnswerDelta,
            Action<IReadOnlyList<ToolCall>> onToolCalls,
            Action<JsonElement> onUsage,
            CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(baseUrl))
                throw new ArgumentException("baseUrl is required");
            if (string.IsNullOrWhiteSpace(question))
                throw new ArgumentException("question is required");

            var endpoint = baseUrl.TrimEnd('/') + "/query_stream_with_tools";

            var payload = new Dictionary<string, object?>
            {
                ["question"] = question,
                ["context"] = contextExtra == null ? null : new Dictionary<string, object?> { ["extra"] = contextExtra },
                ["top_k"] = 8
            };

            await StreamWithSentinelsAsync(endpoint, payload, onAnswerDelta, onToolCalls, onUsage, cancellationToken);
        }

        public async Task StreamRagQueryAnswerOnlyAsync(
            string baseUrl,
            string question,
            Action<string> onAnswerDelta,
            CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(baseUrl))
                throw new ArgumentException("baseUrl is required");
            if (string.IsNullOrWhiteSpace(question))
                throw new ArgumentException("question is required");

            var endpoint = baseUrl.TrimEnd('/') + "/query_stream";

            var payload = new Dictionary<string, object?>
            {
                ["question"] = question,
                ["context"] = null,
                ["top_k"] = 8
            };

            if (onAnswerDelta == null)
                throw new ArgumentNullException(nameof(onAnswerDelta));

            using var req = new HttpRequestMessage(HttpMethod.Post, endpoint);
            var json = JsonSerializer.Serialize(payload);
            req.Content = new StringContent(json, Encoding.UTF8, "application/json");

            using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            resp.EnsureSuccessStatusCode();

            // Unity/Editor API compatibility: some profiles don’t provide the
            // ReadAsStreamAsync(CancellationToken) overload and may not provide
            // StreamReader(Stream, Encoding) either. Use the parameterless overloads.
            using var stream = await resp.Content.ReadAsStreamAsync();
            using var reader = new System.IO.StreamReader(stream);

            char[] buf = new char[1024];
            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                int read = await reader.ReadAsync(buf.AsMemory(0, buf.Length), cancellationToken);
                if (read <= 0) break;

                var chunk = new string(buf, 0, read);
                if (!string.IsNullOrEmpty(chunk))
                    onAnswerDelta(chunk);
            }
        }

        public async Task StreamComposerQueryAnswerOnlyAsync(
            string baseUrl,
            string question,
            string composerMode,
            Action<string> onAnswerDelta,
            CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(baseUrl))
                throw new ArgumentException("baseUrl is required");

            var endpoint = baseUrl.TrimEnd('/') + "/composer/query_stream";

            var payload = new Dictionary<string, object?>
            {
                ["question"] = question,
                ["context"] = null,
                ["top_k"] = 8,
                ["composer_mode"] = composerMode
            };

            if (onAnswerDelta == null)
                throw new ArgumentNullException(nameof(onAnswerDelta));

            using var req = new HttpRequestMessage(HttpMethod.Post, endpoint);
            var json = JsonSerializer.Serialize(payload);
            req.Content = new StringContent(json, Encoding.UTF8, "application/json");

            using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            resp.EnsureSuccessStatusCode();

            using var stream = await resp.Content.ReadAsStreamAsync();
            using var reader = new System.IO.StreamReader(stream);

            char[] buf = new char[1024];
            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                int read = await reader.ReadAsync(buf.AsMemory(0, buf.Length), cancellationToken);
                if (read <= 0) break;

                var chunk = new string(buf, 0, read);
                if (!string.IsNullOrEmpty(chunk))
                    onAnswerDelta(chunk);
            }
        }

        private async Task StreamWithSentinelsAsync(
            string endpoint,
            Dictionary<string, object?> payload,
            Action<string> onAnswerDelta,
            Action<IReadOnlyList<ToolCall>> onToolCalls,
            Action<JsonElement> onUsage,
            CancellationToken cancellationToken)
        {
            if (onAnswerDelta == null) throw new ArgumentNullException(nameof(onAnswerDelta));
            if (onToolCalls == null) throw new ArgumentNullException(nameof(onToolCalls));
            if (onUsage == null) throw new ArgumentNullException(nameof(onUsage));

            using var req = new HttpRequestMessage(HttpMethod.Post, endpoint);
            var json = JsonSerializer.Serialize(payload);
            req.Content = new StringContent(json, Encoding.UTF8, "application/json");

            using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            resp.EnsureSuccessStatusCode();

            using var stream = await resp.Content.ReadAsStreamAsync();
            using var reader = new System.IO.StreamReader(stream);

            var parser = new StreamParser();
            parser.OnAnswerDelta += onAnswerDelta;
            parser.OnToolCallsParsed += onToolCalls;
            parser.OnUsageParsed += onUsage;

            char[] buf = new char[1024];
            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                int read = await reader.ReadAsync(buf.AsMemory(0, buf.Length), cancellationToken);
                if (read <= 0) break;

                var chunk = new string(buf, 0, read);
                parser.Consume(chunk);
            }

            // Must be called to parse __USAGE__ and to fail hard on missing/malformed markers.
            parser.Complete();
        }
    }
}

