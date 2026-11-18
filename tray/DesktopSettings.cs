// 改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）的 winapp/DesktopSettings.cs。
// 托盘设置文件：解压目录下的 desktop.env，数据默认在解压目录下的 data。
// 解压目录不能写（比如放进了 Program Files）时两者都退回 %LOCALAPPDATA%\Mortis。
// 设置文件的位置不随 MORTIS_DATA_DIR 变：要先读它才知道数据目录在哪。

using System.Text;

namespace Mortis.Tray;

internal static class DesktopSettings
{
    internal const string FileName = "desktop.env";
    internal const string DefaultHost = "127.0.0.1";
    internal const string DefaultPort = "8100";

    internal static readonly string[] EditableKeys =
    [
        "MORTIS_DATA_DIR",
        "MORTIS_HOST",
        "MORTIS_PORT",
    ];

    /// zip 解压出的目录，Mortis.exe 所在的位置。
    internal static string AppRoot { get; } = Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory);

    /// 解压目录能写：设置、数据和更新都放在里面。
    internal static bool Portable { get; } = IsWritable(AppRoot);

    private static string HomeDir { get; } =
        Portable
            ? AppRoot
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Mortis");

    internal static string DefaultDataDir { get; } = Portable ? Path.Combine(AppRoot, "data") : HomeDir;

    internal static string SettingsPath { get; } = Path.Combine(HomeDir, FileName);

    internal static string Template { get; } =
        $"""
        # 莫提斯托盘设置。每行一个 键=值，# 开头的行是注释。
        # 同名的系统环境变量优先于这个文件。改完从托盘菜单点「重启服务」生效。
        #
        # 数据目录（数据库、图片、日志、设置和 key）。可以改到别的盘，相对路径按莫提斯所在的目录算；
        # 改了不会搬走已有数据，要自己把旧目录里的东西挪过去。
        # MORTIS_DATA_DIR={DefaultDataDir}
        #
        # 监听地址。默认只有本机能访问；写 0.0.0.0 或 :: 让局域网也能访问，托盘自己仍访问 127.0.0.1。
        # MORTIS_HOST={DefaultHost}
        #
        # 端口。和别的程序冲突时改。
        # MORTIS_PORT={DefaultPort}
        #
        # 这个文件只认这几个键：{string.Join("、", EditableKeys)}
        """;

    internal readonly record struct Parsed(Dictionary<string, string> Values, List<string> UnknownKeys);

    /// 托盘这次启动后端要用的配置。真实环境变量 > 设置文件 > 默认值。
    internal sealed record Config(string DataDir, string Host, string Port, IReadOnlyList<string> UnknownKeys)
    {
        internal string LogPath => Path.Combine(DataDir, "logs", "server.out.log");

        internal string BaseUrl => $"http://{AccessHost(Host)}:{Port}";
    }

    internal static Config Load()
    {
        CreateIfMissing();
        var parsed = Parse(ReadFile());
        var dataDir = Resolve(parsed, "MORTIS_DATA_DIR");
        return new Config(
            dataDir is null ? DefaultDataDir : Path.GetFullPath(dataDir, AppRoot),
            Resolve(parsed, "MORTIS_HOST") ?? DefaultHost,
            Resolve(parsed, "MORTIS_PORT") ?? DefaultPort,
            parsed.UnknownKeys
        );
    }

    /// dotenv 的子集：键=值，值两边去掉空白和成对引号。
    internal static Parsed Parse(string text)
    {
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        var unknown = new List<string>();
        foreach (var rawLine in text.Split('\n'))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }

            var separator = line.IndexOf('=');
            if (separator < 0)
            {
                unknown.Add(line);
                continue;
            }

            var key = line[..separator].Trim();
            if (!EditableKeys.Contains(key, StringComparer.Ordinal))
            {
                unknown.Add(key);
                continue;
            }

            values[key] = Unquoted(line[(separator + 1)..]);
        }

        return new Parsed(values, unknown);
    }

    /// 首次运行写模板；已有文件不覆盖。返回这次是否新写了文件。
    internal static bool CreateIfMissing()
    {
        if (File.Exists(SettingsPath))
        {
            return false;
        }

        try
        {
            Directory.CreateDirectory(HomeDir);
            File.WriteAllText(SettingsPath, Template, new UTF8Encoding(false));
            return true;
        }
        catch (Exception)
        {
            // 写不了就当没有设置文件，按默认值启动。
            return false;
        }
    }

    /// 只看目录权限不可靠，真的建一个文件试。
    private static bool IsWritable(string folder)
    {
        var probe = Path.Combine(folder, $".mortis-write-test-{Environment.ProcessId}");
        try
        {
            File.WriteAllBytes(probe, []);
            File.Delete(probe);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// 通配地址不能拿来访问；IPv6 字面量在网址里要加方括号，zone id 的 % 要转义。
    internal static string AccessHost(string host)
    {
        var trimmed = host.Trim();
        var bare = trimmed.StartsWith('[') && trimmed.EndsWith(']') ? trimmed[1..^1] : trimmed;
        if (bare.Length == 0 || bare == "0.0.0.0" || bare == "::")
        {
            return "127.0.0.1";
        }

        return bare.Contains(':') ? $"[{bare.Replace("%", "%25")}]" : bare;
    }

    private static string? Resolve(Parsed parsed, string key)
    {
        var real = Environment.GetEnvironmentVariable(key);
        if (!string.IsNullOrEmpty(real))
        {
            return real;
        }

        return parsed.Values.TryGetValue(key, out var configured) && configured.Length > 0 ? configured : null;
    }

    private static string ReadFile()
    {
        try
        {
            return File.ReadAllText(SettingsPath);
        }
        catch (Exception)
        {
            return "";
        }
    }

    private static string Unquoted(string raw)
    {
        var value = raw.Trim();
        if (value.Length < 2)
        {
            return value;
        }

        var first = value[0];
        if ((first == '"' || first == '\'') && value[^1] == first)
        {
            return value[1..^1];
        }

        return value;
    }
}
