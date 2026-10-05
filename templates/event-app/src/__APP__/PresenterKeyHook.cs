using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;

namespace __APP__;

/// <summary>
/// Presenter shortcuts that work no matter where keyboard focus is.
///
/// WinUI 3 only routes keys to KeyboardAccelerators when an element inside the XAML
/// island has focus. Right after launch (or after a button disables itself) focus can
/// sit on the top-level window instead, and F5 would silently do nothing on stage.
/// This hook catches the same keys at the Win32 window level as a backstop.
/// </summary>
internal sealed class PresenterKeyHook
{
    private const uint WM_KEYDOWN = 0x0100;
    private const uint WM_SYSKEYDOWN = 0x0104;
    private const int VK_CONTROL = 0x11;

    private delegate IntPtr SubclassProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam, UIntPtr id, UIntPtr refData);

    [DllImport("comctl32.dll", SetLastError = true)]
    private static extern bool SetWindowSubclass(IntPtr hWnd, SubclassProc proc, UIntPtr id, UIntPtr refData);

    [DllImport("comctl32.dll")]
    private static extern IntPtr DefSubclassProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern short GetKeyState(int key);

    private readonly SubclassProc _proc;
    private readonly Func<Windows.System.VirtualKey, bool, bool> _handler;

    /// <param name="handler">(key, ctrlDown) => handled</param>
    public PresenterKeyHook(Window window, Func<Windows.System.VirtualKey, bool, bool> handler)
    {
        _handler = handler;
        _proc = WndProc; // keep the delegate alive for the life of the window
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        SetWindowSubclass(hwnd, _proc, (UIntPtr)0x5A5A, UIntPtr.Zero);
    }

    private IntPtr WndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam, UIntPtr id, UIntPtr refData)
    {
        if (msg is WM_KEYDOWN or WM_SYSKEYDOWN)
        {
            var ctrl = GetKeyState(VK_CONTROL) < 0;
            try
            {
                if (_handler((Windows.System.VirtualKey)(int)wParam, ctrl))
                    return IntPtr.Zero;
            }
            catch
            {
                // A shortcut must never take the demo down.
            }
        }
        return DefSubclassProc(hWnd, msg, wParam, lParam);
    }
}
