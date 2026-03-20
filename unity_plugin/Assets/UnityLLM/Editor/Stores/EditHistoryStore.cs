using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using UnityLLM.Editor.Architecture.Model;
using UnityEngine;

namespace UnityLLM.Editor.Stores
{
    /// <summary>
    /// Persisted local store for applied edit history.
    /// V1 stores file edits (old/new content) so the UI can offer "revert selected".
    /// </summary>
    public sealed class EditHistoryStore
    {
        private const string DefaultStoreRelativePath = "Library/UnityLLM/edit_history.json";

        private readonly string _storePath;
        private readonly JsonSerializerOptions _jsonOptions;

        private readonly List<EditRecord> _history = new List<EditRecord>();

        public IReadOnlyList<EditRecord> History => _history;

        public EditHistoryStore()
        {
            _storePath = Path.Combine(ResPathUtility.GetProjectRoot(), DefaultStoreRelativePath.Replace('/', Path.DirectorySeparatorChar));

            _jsonOptions = new JsonSerializerOptions
            {
                WriteIndented = true,
                AllowTrailingCommas = true,
                PropertyNameCaseInsensitive = true
            };

            Load();
        }

        public void Clear()
        {
            _history.Clear();
            Save();
        }

        public void RecordApplied(EditRecord record)
        {
            if (record == null) return;
            if (string.IsNullOrWhiteSpace(record.EditId))
                record.EditId = Guid.NewGuid().ToString("N");

            if (record.TimestampUtc == default)
                record.TimestampUtc = DateTime.UtcNow;

            _history.Insert(0, record);
            Save();
        }

        public bool TryGetEdit(string editId, out EditRecord record)
        {
            record = null;
            if (string.IsNullOrWhiteSpace(editId)) return false;

            for (int i = 0; i < _history.Count; i++)
            {
                if (string.Equals(_history[i].EditId, editId, StringComparison.OrdinalIgnoreCase))
                {
                    record = _history[i];
                    return true;
                }
            }

            return false;
        }

        /// <summary>
        /// Revert a file edit by rewriting the file content to the stored OldContent.
        /// Returns false if the record is missing required fields.
        /// </summary>
        public bool RevertEdit(string editId)
        {
            if (!TryGetEdit(editId, out var record) || record == null)
                return false;

            if (string.IsNullOrWhiteSpace(record.FilePathRes))
                return false;

            if (record.OldContent == null)
                return false;

            try
            {
                var absolutePath = ResPathUtility.ToAbsolutePath(record.FilePathRes);
                var dir = Path.GetDirectoryName(absolutePath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);

                File.WriteAllText(absolutePath, record.OldContent, System.Text.Encoding.UTF8);
                return true;
            }
            catch
            {
                // Intentionally swallow in V1; UI can surface a generic failure.
                return false;
            }
        }

        private void Load()
        {
            try
            {
                var dir = Path.GetDirectoryName(_storePath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);

                if (!File.Exists(_storePath))
                    return;

                var text = File.ReadAllText(_storePath);
                if (string.IsNullOrWhiteSpace(text))
                    return;

                var loaded = JsonSerializer.Deserialize<List<EditRecord>>(text, _jsonOptions);
                if (loaded != null)
                {
                    _history.Clear();
                    _history.AddRange(loaded);
                }
            }
            catch
            {
                // V1: keep empty history if load fails.
            }
        }

        private void Save()
        {
            try
            {
                var dir = Path.GetDirectoryName(_storePath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);

                var json = JsonSerializer.Serialize(_history, _jsonOptions);
                File.WriteAllText(_storePath, json);
            }
            catch
            {
                // Ignore save failures in V1.
            }
        }
    }
}

