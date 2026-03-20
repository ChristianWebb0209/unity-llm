using System;
using UnityLLM.Editor.Backend;
using UnityLLM.Editor.Architecture.Model;

namespace UnityLLM.Editor.Tools
{
    public interface IToolExecutor
    {
        /// <summary>
        /// True if this executor is responsible for executing the given tool name.
        /// </summary>
        bool CanExecute(string toolName);

        /// <summary>
        /// Execute a tool call and return an edit record suitable for Pending/Timeline UI.
        /// Must throw on malformed arguments for fail-hard behavior.
        /// </summary>
        PendingTimelineRecord Execute(ToolCall toolCall);
    }
}

