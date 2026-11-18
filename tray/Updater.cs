// 原地更新：下载 GitHub Release 的 zip，按 SHA256SUMS 校验，解压到 update\staging，
// 再把解压目录里的程序文件逐项换成新的。data、desktop.env 和 update 不动。
// 正在运行的 Mortis.exe 不能覆盖但能改名，所以旧文件一律改名成 .old，下次启动再删。

using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text.Json;

namespace Mortis.Tray;

internal sealed class UpdateException(string message) : Exception(message);

internal sealed record Release(Version Version, string PageUrl, string ZipName, string? ZipUrl, string? SumsUrl);

internal static class Updater
{
    private const string ReleaseApi = "https://api.github.com/repos/AmadeusOfficial/Mortis/releases/latest";
    private const string PagePrefix = "https://github.com/AmadeusOfficial/Mortis/releases/";
    private const string DownloadPrefix = "https://github.com/AmadeusOfficial/Mortis/releases/download/";
    private const string OldSuffix = ".old";

    /// 更新时不碰的顶层条目：用户数据、托盘设置、更新的临时目录。
    private static readonly HashSet<string> Kept = new(StringComparer.OrdinalIgnoreCase)
    {
        "data",
        DesktopSettings.FileName,
        "update",
    };

    private static string UpdateDir => Path.Combine(DesktopSettings.AppRoot, "update");

    /// 还没发布过版本时返回 null。
    internal static async Task<Release?> FetchLatest(HttpClient web)
    {
        using var resp = await web.GetAsync(ReleaseApi);
        if (resp.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }

        if (!resp.IsSuccessStatusCode)
        {
            throw new UpdateException($"GitHub 返回 {(int)resp.StatusCode}，请稍后再试。");
        }

        await using var stream = await resp.Content.ReadAsStreamAsync();
        using var doc = await JsonDocument.ParseAsync(stream);
        var root = doc.RootElement;
        var tag = Text(root, "tag_name");
        var version = ParseVersion(tag) ?? throw new UpdateException($"看不懂最新版本号「{tag}」。");
        // 这些地址会交给系统打开或拿来下载，只认这个仓库的发布地址。
        var page = Text(root, "html_url");
        if (!page.StartsWith(PagePrefix, StringComparison.Ordinal))
        {
            page = PagePrefix + "latest";
        }

        string zipName = "", zipUrl = "", sumsUrl = "";
        if (root.TryGetProperty("assets", out var assets) && assets.ValueKind == JsonValueKind.Array)
        {
            foreach (var asset in assets.EnumerateArray())
            {
                var name = Text(asset, "name");
                var url = Text(asset, "browser_download_url");
                if (!url.StartsWith(DownloadPrefix, StringComparison.Ordinal))
                {
                    continue;
                }

                if (name.StartsWith("Mortis-", StringComparison.Ordinal)
                    && name.EndsWith("-windows-x64.zip", StringComparison.Ordinal))
                {
                    zipName = name;
                    zipUrl = url;
                }
                else if (name == "SHA256SUMS")
                {
                    sumsUrl = url;
                }
            }
        }

        return new Release(version, page, zipName, NullIfEmpty(zipUrl), NullIfEmpty(sumsUrl));
    }

    /// 下载、校验、解压，返回新版本的根目录（里面有 Mortis.exe）。
    internal static async Task<string> Prepare(HttpClient download, Release release)
    {
        if (release.ZipUrl is null || release.SumsUrl is null)
        {
            throw new UpdateException("这个版本的发布页上没有 Windows 压缩包或 SHA256SUMS。");
        }

        Directory.CreateDirectory(UpdateDir);
        var sums = await download.GetStringAsync(release.SumsUrl);
        var expected = ExpectedHash(sums, release.ZipName)
            ?? throw new UpdateException($"SHA256SUMS 里没有 {release.ZipName}。");

        var zip = Path.Combine(UpdateDir, release.ZipName);
        using (var resp = await download.GetAsync(release.ZipUrl, HttpCompletionOption.ResponseHeadersRead))
        {
            if (!resp.IsSuccessStatusCode)
            {
                throw new UpdateException($"下载失败，GitHub 返回 {(int)resp.StatusCode}。");
            }

            await using var source = await resp.Content.ReadAsStreamAsync();
            await using var target = File.Create(zip);
            await source.CopyToAsync(target);
        }

        string actual;
        await using (var file = File.OpenRead(zip))
        {
            actual = Convert.ToHexString(await SHA256.HashDataAsync(file));
        }

        if (!actual.Equals(expected, StringComparison.OrdinalIgnoreCase))
        {
            File.Delete(zip);
            throw new UpdateException("下载的压缩包和 SHA256SUMS 对不上，已删除，请稍后再试。");
        }

        var staging = Path.Combine(UpdateDir, "staging");
        if (Directory.Exists(staging))
        {
            Directory.Delete(staging, recursive: true);
        }

        ZipFile.ExtractToDirectory(zip, staging);
        var root = FindRoot(staging) ?? throw new UpdateException("压缩包里找不到 Mortis.exe。");
        foreach (var required in new[] { Path.Combine("onedir", "Mortis.Server.exe"), Path.Combine("web", "dist", "index.html") })
        {
            if (!File.Exists(Path.Combine(root, required)))
            {
                throw new UpdateException($"压缩包里缺 {required}。");
            }
        }

        return root;
    }

    /// 把新版本的顶层条目逐个换进解压目录。中途失败就把已经换的全部换回去再抛出。
    internal static void Swap(string newRoot)
    {
        var app = DesktopSettings.AppRoot;
        var done = new List<(string Target, string? Old)>();
        try
        {
            foreach (var source in Directory.EnumerateFileSystemEntries(newRoot))
            {
                var name = Path.GetFileName(source);
                if (Kept.Contains(name))
                {
                    continue;
                }

                var target = Path.Combine(app, name);
                var old = target + OldSuffix;
                Remove(old);
                string? moved = null;
                if (Exists(target))
                {
                    Move(target, old);
                    moved = old;
                }

                done.Add((target, moved));
                Move(source, target);
            }
        }
        catch (Exception ex)
        {
            for (var i = done.Count - 1; i >= 0; i--)
            {
                var (target, old) = done[i];
                try
                {
                    if (old is not null)
                    {
                        if (Exists(target))
                        {
                            Remove(target);
                        }

                        Move(old, target);
                    }
                    else
                    {
                        Remove(target);
                    }
                }
                catch (Exception)
                {
                    // 尽量恢复，剩下的留给用户手动处理。
                }
            }

            throw new UpdateException($"替换文件失败，已恢复原来的版本：{ex.Message}");
        }
    }

    /// 启动时删掉上次更新留下的 .old 和 update 目录。旧托盘可能还没完全退出，删不掉就隔一会儿再试。
    internal static async Task CleanUp()
    {
        for (var attempt = 0; attempt < 10; attempt++)
        {
            var left = false;
            foreach (var entry in Directory.EnumerateFileSystemEntries(DesktopSettings.AppRoot, "*" + OldSuffix))
            {
                left |= !TryRemove(entry);
            }

            if (Directory.Exists(UpdateDir))
            {
                left |= !TryRemove(UpdateDir);
            }

            if (!left)
            {
                return;
            }

            await Task.Delay(TimeSpan.FromSeconds(3));
        }
    }

    /// 「v0.1.0」「0.1.0-rc.1」都取前面的数字部分。
    internal static Version? ParseVersion(string tag)
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

    /// SHA256SUMS 每行是「哈希  文件名」，文件名前可能带 * 表示二进制。
    private static string? ExpectedHash(string sums, string fileName)
    {
        foreach (var raw in sums.Split('\n'))
        {
            var parts = raw.Trim().Split(' ', 2, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 2 && parts[1].Trim().TrimStart('*') == fileName && parts[0].Length == 64)
            {
                return parts[0];
            }
        }

        return null;
    }

    /// zip 里通常是一层 Mortis\ 目录，也兼容直接放在根上。
    private static string? FindRoot(string staging)
    {
        if (File.Exists(Path.Combine(staging, "Mortis.exe")))
        {
            return staging;
        }

        var dirs = Directory.GetDirectories(staging);
        return dirs.Length == 1 && File.Exists(Path.Combine(dirs[0], "Mortis.exe")) ? dirs[0] : null;
    }

    private static string Text(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";

    private static string? NullIfEmpty(string value) => value.Length == 0 ? null : value;

    private static bool Exists(string path) => File.Exists(path) || Directory.Exists(path);

    private static void Move(string from, string to)
    {
        if (Directory.Exists(from))
        {
            Directory.Move(from, to);
        }
        else
        {
            File.Move(from, to);
        }
    }

    private static void Remove(string path)
    {
        if (Directory.Exists(path))
        {
            Directory.Delete(path, recursive: true);
        }
        else if (File.Exists(path))
        {
            File.Delete(path);
        }
    }

    private static bool TryRemove(string path)
    {
        try
        {
            Remove(path);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }
}
