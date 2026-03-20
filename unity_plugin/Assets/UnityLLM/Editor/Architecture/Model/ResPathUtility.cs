using System;
using System.IO;
using UnityEngine;

namespace UnityLLM.Editor.Architecture.Model
{
    internal static class ResPathUtility
    {
        public static string GetProjectRoot()
        {
            // Application.dataPath ends with ".../<Project>/Assets".
            var dataPath = Application.dataPath;
            if (string.IsNullOrWhiteSpace(dataPath))
                return Directory.GetCurrentDirectory();

            var parent = Directory.GetParent(dataPath);
            return parent != null ? parent.FullName : Directory.GetCurrentDirectory();
        }

        public static string ToAbsolutePath(string resPath)
        {
            if (string.IsNullOrWhiteSpace(resPath))
                throw new ArgumentException("resPath is required");

            // Accept both res://foo and Assets/foo forms.
            var projectRoot = GetProjectRoot();
            var p = resPath.Trim();

            if (p.StartsWith("res://", StringComparison.OrdinalIgnoreCase))
                p = p.Substring("res://".Length);

            p = p.TrimStart('/', '\\');
            p = p.Replace('\\', '/');

            // Unity file path convention for this project: "res://<relative under Assets>"
            // so "res://scripts/Foo.cs" maps to "<ProjectRoot>/Assets/scripts/Foo.cs".
            if (!p.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                p = "Assets/" + p;

            var absolute = Path.Combine(projectRoot, p);
            return absolute;
        }

        public static string ToResPath(string absolutePath)
        {
            if (string.IsNullOrWhiteSpace(absolutePath))
                throw new ArgumentException("absolutePath is required");

            var projectRoot = GetProjectRoot();
            var normalizedAbs = absolutePath.Replace('\\', '/');

            // Normalize to project-relative path.
            var rel = normalizedAbs;
            if (rel.StartsWith(projectRoot.Replace('\\', '/'), StringComparison.OrdinalIgnoreCase))
                rel = rel.Substring(projectRoot.Replace('\\', '/').Length).TrimStart('/');

            rel = rel.Replace('\\', '/');
            if (rel.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                return "res://" + rel;

            // Fallback: still emit res:// relative to project root.
            return "res://" + rel;
        }
    }
}

