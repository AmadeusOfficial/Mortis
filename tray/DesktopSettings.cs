// 改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）的 winapp/DesktopSettings.cs。
// 托盘设置文件：%LOCALAPPDATA%\Mortis\desktop.env。
// 位置固定，不随 MORTIS_DATA_DIR 变：要先读它才知道数据目录在哪。

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

    internal static string DefaultDataDir { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Mortis"
    );

    internal static string SettingsPath { get; } = Path.Combine(DefaultDataDir, FileName);

    internal static string Template { get; } =
        $"""
        # 莫提斯托盘设置。每行一个 键=值，# 开头的行是注释。
        # 同名的系统环境变量优先于这个文件。改完从托盘菜单点「重启服务」生效。
        #
        # 数据目录（数据库、图片、日志、设置和 key）。可以改到别的盘；改了不会搬走已有数据。
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
        return new Config(
            Resolve(parsed, "MORTIS_DATA_DIR") ?? DefaultDataDir,
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

    /// 首次运行写模板；已有文件不覆盖。
    internal static void CreateIfMissing()
    {
        if (File.Exists(SettingsPath))
        {
            return;
        }

        try
        {
            Directory.CreateDirectory(DefaultDataDir);
            File.WriteAllText(SettingsPath, Template, new UTF8Encoding(false));
        }
        catch (Exception)
        {
            // 写不了就当没有设置文件，按默认值启动。
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
