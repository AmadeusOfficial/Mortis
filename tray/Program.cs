// 改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）的 winapp/Program.cs。
// 莫提斯托盘：单实例，拉起并监督后端 onedir\Mortis.Server.exe，托盘菜单。页面和接口都由后端提供。

using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace Mortis.Tray;

internal static class Program
{
    private const string MutexName = @"Local\Mortis.Tray";

    [STAThread]
    private static int Main()
    {
        Mutex? mutex;
        bool createdNew;
        try
        {
            mutex = new Mutex(true, MutexName, out createdNew);
        }
        catch (AbandonedMutexException ex)
        {
            mutex = ex.Mutex;
            createdNew = true;
        }

        using (mutex)
        {
            if (!createdNew)
            {
                App.OpenUrl(DesktopSettings.Load().BaseUrl);
                return 0;
            }

            return App.Run();
        }
    }
}

internal sealed class App
{
    private const uint IdOpen = 1001;
    private const uint IdDataDir = 1002;
    private const uint IdSettings = 1003;
    private const uint IdRestart = 1004;
    private const uint IdUpdate = 1005;
    private const uint IdQuit = 1006;
    private const nuint PollTimerId = 1;

    /// 后端发现端口被占时的退出码，见 src/mortis/server.py 的 PORT_IN_USE。
    private const int ExitPortInUse = 3;

    private const int MaxQuickExits = 5;
    private static readonly TimeSpan QuickExit = TimeSpan.FromSeconds(30);
    private static readonly int[] BackoffSeconds = [1, 2, 4, 8, 16];
    private const string ReleaseApi = "https://api.github.com/repos/AmadeusOfficial/Mortis/releases/latest";

    private static App? _instance;
    private static Native.WndProc? _wndProc;

    private enum ServerState
    {
        Starting,
        Running,
        Stopped,
    }

    private readonly object _gate = new();
    private readonly ConcurrentQueue<Action> _ui = new();
    private readonly AutoResetEvent _wake = new(false);
    private readonly HttpClient _local = new(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(5) };
    private readonly HttpClient _web = new() { Timeout = TimeSpan.FromSeconds(20) };
    private readonly Version _version;

    private nint _hwnd;
    private nint _hIcon;
    private nint _hMenu;
    private nint _hJob;
    private Native.NotifyIconData _nid;
    private uint _taskbarCreated;
    private bool _trayAdded;
    private bool _unwinding;

    // 以下只在界面线程读写。
    private DesktopSettings.Config _config;
    private ServerState _state = ServerState.Starting;
    private string _reason = "";
    private bool _pollInFlight;
    private bool _openedBrowser;
    private IReadOnlyList<string> _warnedKeys = Array.Empty<string>();

    // 以下由 _gate 保护，监督线程和界面线程都会碰。
    private Process? _server;
    private bool _stopping;
    private bool _manualRestart;

    private App()
    {
        var v = Assembly.GetExecutingAssembly().GetName().Version ?? new Version(0, 0, 0);
        _version = new Version(v.Major, v.Minor, Math.Max(v.Build, 0));
        _config = DesktopSettings.Load();
        _web.DefaultRequestHeaders.UserAgent.ParseAdd($"Mortis/{VersionText}");
        _web.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
    }

    private string VersionText => _version.ToString(3);

    internal static int Run()
    {
        var app = new App();
        _instance = app;
        return app.RunLoop();
    }

    private int RunLoop()
    {
        Native.SetProcessDpiAwarenessContext(Native.DpiAwarenessContextPerMonitorAwareV2);

        if (!CreateUi())
        {
            return 1;
        }

        _hJob = CreateKillOnCloseJob();
        _ = Task.Factory.StartNew(Supervise, TaskCreationOptions.LongRunning);
        Native.SetTimer(_hwnd, PollTimerId, 1500, 0);

        while (Native.GetMessage(out var msg, 0, 0, 0))
        {
            Native.TranslateMessage(in msg);
            Native.DispatchMessage(in msg);
        }

        Teardown();
        return 0;
    }

    // MARK: - 后端

    private static string RootDir => AppContext.BaseDirectory;

    private static string? ServerBinary()
    {
        var overrideBin = Environment.GetEnvironmentVariable("MORTIS_SERVER_BIN");
        if (!string.IsNullOrEmpty(overrideBin))
        {
            return File.Exists(overrideBin) ? overrideBin : null;
        }

        var candidate = Path.Combine(RootDir, "onedir", "Mortis.Server.exe");
        return File.Exists(candidate) ? candidate : null;
    }

    private void Supervise()
    {
        var quickExits = 0;
        while (!IsStopping)
        {
            TakeManualRestart();
            var config = DesktopSettings.Load();
            OnUi(() => ApplyConfig(config));

            if (!int.TryParse(config.Port, out var port) || port is < 1 or > 65535)
            {
                if (!Halt($"端口设置无效：{config.Port}"))
                {
                    return;
                }

                quickExits = 0;
                continue;
            }

            var bin = ServerBinary();
            if (bin is null)
            {
                if (!Halt("找不到后端程序 onedir\\Mortis.Server.exe"))
                {
                    return;
                }

                quickExits = 0;
                continue;
            }

            ServerLog log;
            Process proc;
            try
            {
                log = ServerLog.Open(config.LogPath);
            }
            catch (Exception ex)
            {
                if (!Halt($"无法写日志：{ex.Message}"))
                {
                    return;
                }

                quickExits = 0;
                continue;
            }

            try
            {
                proc = StartServer(bin, config, log);
            }
            catch (Exception ex)
            {
                log.Dispose();
                if (!Halt($"无法启动后端：{ex.Message}"))
                {
                    return;
                }

                quickExits = 0;
                continue;
            }

            var startedAt = DateTime.UtcNow;
            bool stopNow;
            lock (_gate)
            {
                _server = proc;
                stopNow = _stopping;
            }

            if (stopNow)
            {
                KillTree(proc);
            }

            proc.WaitForExit();
            var code = proc.ExitCode;
            bool manual;
            lock (_gate)
            {
                _server = null;
                manual = _manualRestart;
                _manualRestart = false;
            }

            log.Line($"后端已退出，退出码 {code}");
            log.Dispose();
            proc.Dispose();

            if (IsStopping)
            {
                return;
            }

            if (manual)
            {
                quickExits = 0;
                continue;
            }

            if (code == ExitPortInUse)
            {
                if (!Halt($"端口 {port} 已被占用"))
                {
                    return;
                }

                quickExits = 0;
                continue;
            }

            quickExits = DateTime.UtcNow - startedAt < QuickExit ? quickExits + 1 : 0;
            if (quickExits >= MaxQuickExits)
            {
                if (!Halt($"后端连续 {MaxQuickExits} 次启动后很快退出，见 logs\\server.out.log"))
                {
                    return;
                }

                quickExits = 0;
                continue;
            }

            var delay = BackoffSeconds[Math.Min(Math.Max(quickExits - 1, 0), BackoffSeconds.Length - 1)];
            OnUi(() => SetState(ServerState.Starting, ""));
            _wake.WaitOne(TimeSpan.FromSeconds(delay));
            if (TakeManualRestart())
            {
                quickExits = 0;
            }
        }
    }

    private Process StartServer(string bin, DesktopSettings.Config config, ServerLog log)
    {
        Directory.CreateDirectory(config.DataDir);
        var psi = new ProcessStartInfo
        {
            FileName = bin,
            WorkingDirectory = Path.GetDirectoryName(bin) ?? RootDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
        };
        psi.Environment["MORTIS_DATA_DIR"] = config.DataDir;
        psi.Environment["MORTIS_HOST"] = config.Host;
        psi.Environment["MORTIS_PORT"] = config.Port;
        psi.Environment["PYTHONUTF8"] = "1";
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("MORTIS_WEB_DIST")))
        {
            var web = Path.Combine(RootDir, "web", "dist");
            if (File.Exists(Path.Combine(web, "index.html")))
            {
                psi.Environment["MORTIS_WEB_DIST"] = web;
            }
        }

        var proc = new Process { StartInfo = psi };
        proc.OutputDataReceived += (_, e) => log.Raw(e.Data);
        proc.ErrorDataReceived += (_, e) => log.Raw(e.Data);
        if (!proc.Start())
        {
            throw new InvalidOperationException("CreateProcess 失败");
        }

        if (_hJob != 0)
        {
            Native.AssignProcessToJobObject(_hJob, proc.Handle);
        }

        log.Line($"托盘启动后端 {bin}（PID {proc.Id}，端口 {config.Port}，数据目录 {config.DataDir}）");
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();
        return proc;
    }

    /// 停止自动重启，等菜单「重启服务」或退出。返回 false 表示托盘正在退出。
    private bool Halt(string reason)
    {
        OnUi(() => SetState(ServerState.Stopped, reason, balloon: true));
        _wake.WaitOne();
        return !IsStopping;
    }

    private bool TakeManualRestart()
    {
        lock (_gate)
        {
            var manual = _manualRestart;
            _manualRestart = false;
            return manual;
        }
    }

    private void RestartServer()
    {
        Process? proc;
        lock (_gate)
        {
            _manualRestart = true;
            proc = _server;
        }

        SetState(ServerState.Starting, "");
        if (proc is null)
        {
            _wake.Set();
            return;
        }

        _ = Task.Run(() => KillTree(proc));
    }

    private void StopServer(TimeSpan wait)
    {
        Process? proc;
        lock (_gate)
        {
            _stopping = true;
            proc = _server;
        }

        _wake.Set();
        if (proc is null)
        {
            return;
        }

        KillTree(proc);
        try
        {
            proc.WaitForExit((int)wait.TotalMilliseconds);
        }
        catch (Exception)
        {
            // 监督线程已经回收了这个进程。
        }
    }

    /// 后端没有关停接口，直接结束进程树。
    private static void KillTree(Process proc)
    {
        try
        {
            proc.Kill(entireProcessTree: true);
        }
        catch (Exception)
        {
            // 已经退出或已被回收。
        }
    }

    private bool IsStopping
    {
        get
        {
            lock (_gate)
            {
                return _stopping;
            }
        }
    }

    private static nint CreateKillOnCloseJob()
    {
        var job = Native.CreateJobObject(0, null);
        if (job == 0)
        {
            return 0;
        }

        var info = new Native.JobObjectExtendedLimitInformation
        {
            BasicLimitInformation = new Native.JobObjectBasicLimitInformation
            {
                // 托盘退出或崩溃时句柄关闭，后端随之结束，不留孤儿进程。
                LimitFlags = Native.JobObjectLimitKillOnJobClose,
            },
        };
        if (!Native.SetInformationJobObject(
                job,
                Native.JobObjectInfoExtendedLimit,
                ref info,
                (uint)Marshal.SizeOf<Native.JobObjectExtendedLimitInformation>()))
        {
            Native.CloseHandle(job);
            return 0;
        }

        return job;
    }

    // MARK: - 窗口和托盘

    private bool CreateUi()
    {
        _wndProc = WndProc;
        _hIcon = Native.LoadAppIcon(Environment.ProcessPath);
        var hInstance = Native.GetModuleHandle(null);
        const string className = "MortisTray";
        var wc = new Native.WndClassEx
        {
            cbSize = (uint)Marshal.SizeOf<Native.WndClassEx>(),
            style = Native.CsDblClks,
            lpfnWndProc = Marshal.GetFunctionPointerForDelegate(_wndProc),
            hInstance = hInstance,
            hIcon = _hIcon,
            hIconSm = _hIcon,
            lpszClassName = className,
        };
        if (Native.RegisterClassEx(ref wc) == 0)
        {
            return false;
        }

        _hwnd = Native.CreateWindowEx(
            Native.WsExToolwindow, className, "莫提斯", Native.WsPopup, 0, 0, 0, 0, 0, 0, hInstance, 0);
        if (_hwnd == 0)
        {
            return false;
        }

        _taskbarCreated = Native.RegisterWindowMessage("TaskbarCreated");
        BuildMenu();
        AddTray();
        return true;
    }

    private static nint WndProc(nint hWnd, uint msg, nint wParam, nint lParam)
    {
        var app = _instance;
        if (app is null || (hWnd != app._hwnd && app._hwnd != 0))
        {
            return Native.DefWindowProc(hWnd, msg, wParam, lParam);
        }

        return app.Handle(hWnd, msg, wParam, lParam);
    }

    private nint Handle(nint hWnd, uint msg, nint wParam, nint lParam)
    {
        if (msg == _taskbarCreated && _taskbarCreated != 0)
        {
            _trayAdded = false;
            AddTray();
            return 0;
        }

        switch (msg)
        {
            case Native.WmTray:
                if (lParam is Native.WmRButtonUp or Native.WmContextMenu)
                {
                    ShowMenu();
                }
                else if (lParam == Native.WmLButtonDblClk)
                {
                    OpenWeb();
                }

                return 0;
            case Native.WmTimer:
                if ((nuint)wParam == PollTimerId)
                {
                    QueuePoll();
                }

                return 0;
            case Native.WmDispatch:
                DrainUi();
                return 0;
            case Native.WmClose:
                Quit();
                return 0;
            case Native.WmEndSession:
                if (wParam != 0)
                {
                    Quit();
                }

                return 0;
            case Native.WmDestroy:
                Native.PostQuitMessage(0);
                return 0;
            default:
                return Native.DefWindowProc(hWnd, msg, wParam, lParam);
        }
    }

    private void AddTray()
    {
        _nid = new Native.NotifyIconData
        {
            cbSize = (uint)Marshal.SizeOf<Native.NotifyIconData>(),
            hWnd = _hwnd,
            uID = 1,
            uFlags = Native.NifMessage | Native.NifIcon | Native.NifTip,
            uCallbackMessage = Native.WmTray,
            hIcon = _hIcon,
            szTip = TrayTip(),
            szInfo = "",
            szInfoTitle = "",
        };
        if (Native.ShellNotifyIcon(_trayAdded ? Native.NimModify : Native.NimAdd, ref _nid))
        {
            _trayAdded = true;
        }
    }

    private void RemoveTray()
    {
        if (!_trayAdded)
        {
            return;
        }

        Native.ShellNotifyIcon(Native.NimDelete, ref _nid);
        _trayAdded = false;
    }

    private void BuildMenu()
    {
        _hMenu = Native.CreatePopupMenu();
        Native.AppendMenu(_hMenu, Native.MfString | Native.MfGrayed | Native.MfDisabled, 0, StatusText());
        Native.AppendMenu(_hMenu, Native.MfSeparator, 0, null);
        Native.AppendMenu(_hMenu, Native.MfString, IdOpen, "打开莫提斯");
        Native.AppendMenu(_hMenu, Native.MfString, IdDataDir, "打开数据目录");
        Native.AppendMenu(_hMenu, Native.MfString, IdSettings, "打开设置文件");
        Native.AppendMenu(_hMenu, Native.MfSeparator, 0, null);
        Native.AppendMenu(_hMenu, Native.MfString, IdRestart, "重启服务");
        Native.AppendMenu(_hMenu, Native.MfString, IdUpdate, "检查更新");
        Native.AppendMenu(_hMenu, Native.MfSeparator, 0, null);
        Native.AppendMenu(_hMenu, Native.MfString, IdQuit, "退出");
    }

    private void ShowMenu()
    {
        Native.GetCursorPos(out var pt);
        // TrackPopupMenu 按所属窗口的 DPI 绘制；把隐藏窗口挪到光标所在的显示器，高 DPI 任务栏上菜单才不糊。
        Native.SetWindowPos(_hwnd, 0, pt.X, pt.Y, 0, 0, Native.SwpNoSize | Native.SwpNoZOrder | Native.SwpNoActivate);
        Native.SetForegroundWindow(_hwnd);
        var cmd = Native.TrackPopupMenu(_hMenu, Native.TpmRightButton | Native.TpmReturnCmd, pt.X, pt.Y, 0, _hwnd, 0);
        Native.PostMessage(_hwnd, 0x0000, 0, 0);
        switch (cmd)
        {
            case IdOpen:
                OpenWeb();
                break;
            case IdDataDir:
                OpenDataDirectory();
                break;
            case IdSettings:
                OpenSettingsFile();
                break;
            case IdRestart:
                RestartServer();
                break;
            case IdUpdate:
                CheckUpdate();
                break;
            case IdQuit:
                Native.PostMessage(_hwnd, Native.WmClose, 0, 0);
                break;
        }
    }

    // MARK: - 状态

    private void ApplyConfig(DesktopSettings.Config config)
    {
        _config = config;
        if (_state != ServerState.Starting)
        {
            SetState(ServerState.Starting, "");
        }

        WarnUnknownKeys(config.UnknownKeys);
    }

    private void SetState(ServerState state, string reason, bool balloon = false)
    {
        _state = state;
        _reason = reason;
        if (_hMenu != 0)
        {
            Native.ModifyMenu(
                _hMenu, 0, Native.MfByPosition | Native.MfString | Native.MfGrayed | Native.MfDisabled, 0, StatusText());
        }

        if (!_trayAdded)
        {
            return;
        }

        _nid.szTip = TrayTip();
        _nid.uFlags = Native.NifMessage | Native.NifIcon | Native.NifTip;
        if (balloon)
        {
            _nid.uFlags |= Native.NifInfo;
            _nid.szInfoTitle = "莫提斯服务已停止";
            _nid.szInfo = Shorten($"{reason}。可以从托盘菜单点「重启服务」再试。", 250);
            _nid.dwInfoFlags = Native.NiifWarning;
        }

        Native.ShellNotifyIcon(Native.NimModify, ref _nid);
        _nid.szInfo = "";
        _nid.szInfoTitle = "";
    }

    private string StatusText() =>
        _state switch
        {
            ServerState.Running => "运行中",
            ServerState.Stopped => $"已停止：{Shorten(_reason, 60)}",
            _ => "正在启动",
        };

    private string TrayTip() => Shorten($"莫提斯 {VersionText} · {StatusText()}", 127);

    private static string Shorten(string text, int max) =>
        text.Length > max ? string.Concat(text.AsSpan(0, max - 1), "…") : text;

    private void QueuePoll()
    {
        if (_pollInFlight || _state == ServerState.Stopped)
        {
            return;
        }

        bool alive;
        lock (_gate)
        {
            alive = _server is not null;
        }

        if (!alive)
        {
            return;
        }

        _pollInFlight = true;
        var url = _config.BaseUrl + "/api/health";
        _ = Task.Run(async () =>
        {
            var ok = false;
            try
            {
                using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                using var resp = await _local.GetAsync(url, cts.Token);
                ok = resp.IsSuccessStatusCode;
            }
            catch (Exception)
            {
                // 还没起来或已经挂了。
            }

            OnUi(() => ApplyHealth(ok));
        });
    }

    private void ApplyHealth(bool ok)
    {
        _pollInFlight = false;
        if (_state == ServerState.Stopped)
        {
            return;
        }

        bool alive;
        lock (_gate)
        {
            alive = _server is not null && !_manualRestart;
        }

        var next = ok && alive ? ServerState.Running : ServerState.Starting;
        if (next != _state)
        {
            SetState(next, "");
        }

        if (next == ServerState.Running && !_openedBrowser)
        {
            _openedBrowser = true;
            OpenWeb();
        }
    }

    // MARK: - 菜单动作

    internal static void OpenUrl(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch (Exception)
        {
            // 没有默认浏览器之类，忽略。
        }
    }

    private void OpenWeb() => OpenUrl(_config.BaseUrl + "/");

    private void OpenDataDirectory()
    {
        try
        {
            Directory.CreateDirectory(_config.DataDir);
            Process.Start(new ProcessStartInfo(_config.DataDir) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            Alert("打不开数据目录", $"{_config.DataDir}\n{ex.Message}");
        }
    }

    /// 用 .env 的默认程序打开；没有关联时用记事本。
    private static void OpenSettingsFile()
    {
        DesktopSettings.CreateIfMissing();
        try
        {
            Process.Start(new ProcessStartInfo(DesktopSettings.SettingsPath) { UseShellExecute = true });
            return;
        }
        catch (Exception)
        {
            // 没有 .env 关联，换记事本。
        }

        try
        {
            Process.Start(new ProcessStartInfo("notepad.exe", $"\"{DesktopSettings.SettingsPath}\""));
        }
        catch (Exception)
        {
            // 忽略。
        }
    }

    private void CheckUpdate()
    {
        var current = _version;
        _ = Task.Run(async () =>
        {
            try
            {
                using var resp = await _web.GetAsync(ReleaseApi);
                if (resp.StatusCode == HttpStatusCode.NotFound)
                {
                    OnUi(() => Alert("检查更新", "还没有发布过版本。"));
                    return;
                }

                if (!resp.IsSuccessStatusCode)
                {
                    OnUi(() => Alert("检查更新失败", $"GitHub 返回 {(int)resp.StatusCode}，请稍后再试。"));
                    return;
                }

                await using var stream = await resp.Content.ReadAsStreamAsync();
                using var doc = await JsonDocument.ParseAsync(stream);
                var root = doc.RootElement;
                var tag = root.TryGetProperty("tag_name", out var t) ? t.GetString() ?? "" : "";
                var html = root.TryGetProperty("html_url", out var h) ? h.GetString() ?? "" : "";
                // 这个地址会交给系统打开，只认 GitHub 的网页。
                if (!html.StartsWith("https://github.com/", StringComparison.Ordinal))
                {
                    html = "";
                }
                var latest = ParseVersion(tag);
                if (latest is null)
                {
                    OnUi(() => Alert("检查更新失败", $"看不懂最新版本号「{tag}」。"));
                    return;
                }

                if (latest <= current || html.Length == 0)
                {
                    OnUi(() => Alert("检查更新", $"已经是最新版本（{current.ToString(3)}）。"));
                    return;
                }

                OnUi(() =>
                {
                    var answer = Native.MessageBox(
                        _hwnd,
                        $"有新版本 {latest.ToString(3)}（当前 {current.ToString(3)}）。\n要打开发布页面下载吗？",
                        "检查更新",
                        Native.MbYesNo | Native.MbIconInformation | Native.MbSetForeground);
                    if (answer == Native.IdYes)
                    {
                        OpenUrl(html);
                    }
                });
            }
            catch (Exception)
            {
                OnUi(() => Alert("检查更新失败", "暂时连不上 GitHub，请稍后再试。"));
            }
        });
    }

    /// 「v0.1.0」「0.1.0-rc.1」都取前面的数字部分。
    private static Version? ParseVersion(string tag)
    {
        var text = tag.Trim().TrimStart('v', 'V');
        var cut = text.IndexOfAny(['-', '+']);
        if (cut >= 0)
        {
            text = text[..cut];
        }

        if (!Version.TryParse(text, out var v))
        {
            return null;
        }

        return new Version(v.Major, v.Minor, Math.Max(v.Build, 0));
    }

    private void Quit()
    {
        if (_unwinding)
        {
            return;
        }

        _unwinding = true;
        Native.KillTimer(_hwnd, PollTimerId);
        StopServer(TimeSpan.FromSeconds(5));
        RemoveTray();
        Native.DestroyWindow(_hwnd);
    }

    private void Teardown()
    {
        if (_hMenu != 0)
        {
            Native.DestroyMenu(_hMenu);
            _hMenu = 0;
        }

        if (_hIcon != 0)
        {
            Native.DestroyIcon(_hIcon);
            _hIcon = 0;
        }

        if (_hJob != 0)
        {
            Native.CloseHandle(_hJob);
            _hJob = 0;
        }

        _local.Dispose();
        _web.Dispose();
        GC.KeepAlive(_wndProc);
    }

    // MARK: - 界面线程

    private void OnUi(Action action)
    {
        _ui.Enqueue(action);
        if (_hwnd != 0)
        {
            Native.PostMessage(_hwnd, Native.WmDispatch, 0, 0);
        }
    }

    private void DrainUi()
    {
        while (_ui.TryDequeue(out var action))
        {
            action();
        }
    }

    /// 设置文件里不认识的键提示一次；之后又出现新的键再提示。
    private void WarnUnknownKeys(IReadOnlyList<string> keys)
    {
        if (keys.Count == 0 || _warnedKeys.SequenceEqual(keys))
        {
            return;
        }

        _warnedKeys = keys.ToArray();
        Alert(
            "设置文件里有不认识的键",
            $"这些键会被忽略：\n{string.Join("\n", keys)}\n\n只认 {string.Join("、", DesktopSettings.EditableKeys)}。\n文件：{DesktopSettings.SettingsPath}");
    }

    private void Alert(string title, string message)
    {
        Native.MessageBox(_hwnd, message, title, Native.MbOk | Native.MbIconInformation | Native.MbSetForeground);
    }
}

/// 后端的 stdout 和 stderr 追加写进数据目录下的 logs\server.out.log（UTF-8）。
internal sealed class ServerLog : IDisposable
{
    private readonly object _gate = new();
    private readonly StreamWriter _writer;
    private bool _closed;

    private ServerLog(StreamWriter writer) => _writer = writer;

    internal static ServerLog Open(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
        return new ServerLog(new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true });
    }

    internal void Raw(string? line)
    {
        if (line is null)
        {
            return;
        }

        lock (_gate)
        {
            if (!_closed)
            {
                _writer.WriteLine(line);
            }
        }
    }

    internal void Line(string text) => Raw($"[托盘 {DateTime.Now:yyyy-MM-dd HH:mm:ss}] {text}");

    public void Dispose()
    {
        lock (_gate)
        {
            if (_closed)
            {
                return;
            }

            _closed = true;
            _writer.Dispose();
        }
    }
}
