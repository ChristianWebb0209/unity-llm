using System;
using System.Linq;
using System.Reflection;
using UnityEditor;

namespace UnityLLM.Editor.Docking
{
    internal static class DockRightHelper
    {
        /// <summary>
        /// Best-effort dock to the right. If Unity internals differ, fall back to Show().
        /// </summary>
        public static void DockRight(EditorWindow window)
        {
            if (window == null) return;

            try
            {
                // Unity uses internal docking APIs; this is intentionally reflection-heavy.
                var dockAreaType = typeof(EditorWindow).Assembly.GetType("UnityEditor.DockArea");
                var dockPositionType = typeof(EditorWindow).Assembly.GetType("UnityEditor.DockArea+DockPosition");
                if (dockAreaType == null || dockPositionType == null)
                {
                    window.Show();
                    return;
                }

                var dockRight = Enum.Parse(dockPositionType, "Right", ignoreCase: true);
                var allMethods = dockAreaType.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);

                // Try common signatures (varies by Unity version).
                // Example signatures observed in various editor builds:
                //   Dock(EditorWindow window, DockPosition pos)
                //   Dock(EditorWindow window, DockPosition pos, int index)
                foreach (var m in allMethods)
                {
                    var ps = m.GetParameters();
                    if (ps.Length == 2 &&
                        ps[0].ParameterType.IsAssignableFrom(typeof(EditorWindow)) &&
                        ps[1].ParameterType == dockPositionType)
                    {
                        m.Invoke(null, new object[] { window, dockRight });
                        return;
                    }
                }

                window.Show();
                window.Focus();
            }
            catch
            {
                // If anything fails, we still want the window visible.
                window.Show();
                window.Focus();
            }
        }
    }
}
