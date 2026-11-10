// A .NET Framework host: Windows 10 1809 already includes Framework 4.7.2.
// No Python GUI packages, downloaded scripts, or privileged runtime installers.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    internal const string AppName = "星栖小镇";
    internal const string WebViewDownload = "https://developer.microsoft.com/microsoft-edge/webview2/#download-section";
    internal static readonly string DataDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "StarhavenTown");
    internal static string SelfTestFile;
    internal static string WorkingDataDirectory = DataDirectory;
    internal static int ExitCode;
    private static readonly object LogLock = new object();

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct OsVersion
    {
        internal uint Size, Major, Minor, Build, Platform;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] internal string ServicePack;
    }
    [DllImport("ntdll.dll", CharSet = CharSet.Unicode)]
    private static extern int RtlGetVersion(ref OsVersion info);

    [STAThread]
    private static int Main(string[] args)
    {
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--self-test" && i + 1 < args.Length) SelfTestFile = Path.GetFullPath(args[++i]);
            else if (args[i] == "--data-dir" && i + 1 < args.Length) WorkingDataDirectory = Path.GetFullPath(args[++i]);
            else return 2;
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        bool ownsMutex;
        // Tests use isolated data; installed and portable copies share one user instance.
        using (Mutex mutex = new Mutex(true, "Local\\StarhavenTownSingleInstance", out ownsMutex))
        {
            if (!ownsMutex) { MessageBox.Show(AppName + "已经在运行。", AppName); return 0; }
            try
            {
                OsVersion os = new OsVersion(); os.Size = (uint)Marshal.SizeOf(os);
                if (RtlGetVersion(ref os) != 0 || os.Major < 10 || (os.Major == 10 && os.Build < 17763) || !Environment.Is64BitOperatingSystem)
                    throw new InvalidOperationException("此版本需要 64 位 Windows 10 1809（内部版本 17763）或更新的系统。");
                Directory.CreateDirectory(WorkingDataDirectory);
                string runtime;
                try { runtime = CoreWebView2Environment.GetAvailableBrowserVersionString(); }
                catch (WebView2RuntimeNotFoundException) { runtime = null; }
                if (String.IsNullOrEmpty(runtime))
                {
                    if (SelfTestFile != null) throw new InvalidOperationException("Microsoft Edge WebView2 Runtime is missing.");
                    DialogResult answer = MessageBox.Show("这台电脑还没有 Microsoft Edge WebView2 运行时。\n\n点击“是”打开微软官网下载页，选择 Evergreen Standalone Installer 的 x64 版本并安装，然后重新打开小镇。\n\n小镇不会自动安装系统组件。", AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                    if (answer == DialogResult.Yes) OpenExternal(WebViewDownload);
                    return 3;
                }
                Log("Windows build " + os.Build + "; WebView2 " + runtime + "; .NET " + Environment.Version);
                Application.Run(new TownWindow());
            }
            catch (Exception exc) { Fail(exc); }
            finally { mutex.ReleaseMutex(); }
        }
        return ExitCode;
    }

    internal static void OpenExternal(string path)
    {
        Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
    }
    internal static void Log(string text)
    {
        try
        {
            lock (LogLock)
            {
                string logs = Path.Combine(WorkingDataDirectory, "logs"); Directory.CreateDirectory(logs);
                File.AppendAllText(Path.Combine(logs, "desktop-windows.log"), DateTime.Now.ToString("s") + " " + text + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch { }
    }
    internal static void Fail(Exception exc)
    {
        ExitCode = 1; Log(exc.ToString());
        if (SelfTestFile != null)
        {
            File.WriteAllText(SelfTestFile, new JavaScriptSerializer().Serialize(new { ok = false, error = exc.ToString() }), Encoding.UTF8);
            return;
        }
        MessageBox.Show("无法打开小镇：\n" + exc.Message + "\n\n详细日志：\n" + Path.Combine(WorkingDataDirectory, "logs", "desktop-windows.log"), AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
    // Windows CommandLineToArgvW-compatible quoting, including trailing slashes.
    internal static string Quote(string value)
    {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c); slashes = 0;
        }
        result.Append('\\', slashes * 2); return result.Append('"').ToString();
    }
}

internal sealed class TownWindow : Form
{
    private readonly WebView2 browser = new WebView2();
    private readonly Label status = new Label();
    private readonly CancellationTokenSource closing = new CancellationTokenSource();
    private readonly System.Windows.Forms.Timer health = new System.Windows.Forms.Timer();
    private Process backend;
    private readonly ChildProcessJob childJob = new ChildProcessJob();
    private string readyDirectory;
    private Uri origin;

    internal TownWindow()
    {
        Text = Program.AppName; Width = 1280; Height = 850; MinimumSize = new Size(800, 600);
        StartPosition = FormStartPosition.CenterScreen;
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        var menu = new MenuStrip(); var townMenu = new ToolStripMenuItem("小镇");
        townMenu.DropDownItems.Add("小镇设置", null, delegate { if (origin != null && browser.CoreWebView2 != null) browser.CoreWebView2.Navigate(origin + "#settings"); });
        townMenu.DropDownItems.Add("打开数据文件夹", null, delegate { Program.OpenExternal(Program.WorkingDataDirectory); });
        townMenu.DropDownItems.Add("下载新版本", null, delegate { Program.OpenExternal("https://github.com/2017Rocke/starhaven-town/releases/latest"); });
        townMenu.DropDownItems.Add("退出", null, delegate { Close(); });
        menu.Items.Add(townMenu); MainMenuStrip = menu;
        browser.Dock = DockStyle.Fill; Controls.Add(browser); Controls.Add(menu);
        status.Text = "正在唤醒小镇…"; status.Dock = DockStyle.Fill; status.TextAlign = ContentAlignment.MiddleCenter; Controls.Add(status); status.BringToFront();
        Shown += async delegate { await StartTown(); };
        health.Interval = 700;
        health.Tick += delegate { if (backend != null && backend.HasExited && !closing.IsCancellationRequested) { Program.Fail(new Exception("小镇服务意外停止，请重新打开。")); Close(); } };
    }

    private async Task StartTown()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string backendDir = Path.Combine(root, "backend");
            string python = Path.Combine(backendDir, "runtime", "python.exe");
            string runner = Path.Combine(backendDir, "desktop_runner.py");
            if (!File.Exists(python) || !File.Exists(runner)) throw new FileNotFoundException("安装包不完整，请重新下载安装。");
            readyDirectory = Path.Combine(Path.GetTempPath(), "starhaven-win-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(readyDirectory);
            string ready = Path.Combine(readyDirectory, "ready.json");
            var start = new ProcessStartInfo(python, "-I -X utf8 -B " + Program.Quote(runner) + " --data-dir " + Program.Quote(Program.WorkingDataDirectory) + " --ready-file " + Program.Quote(ready) + " --parent-pid " + Process.GetCurrentProcess().Id + " --stop-file " + Program.Quote(Path.Combine(readyDirectory, "stop.request")));
            start.WorkingDirectory = backendDir; start.UseShellExecute = false; start.CreateNoWindow = true;
            start.RedirectStandardError = true; start.RedirectStandardOutput = true;
            backend = new Process(); backend.StartInfo = start;
            backend.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (!String.IsNullOrWhiteSpace(e.Data)) Program.Log(e.Data); };
            backend.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (!String.IsNullOrWhiteSpace(e.Data)) Program.Log(e.Data); };
            backend.Start(); childJob.Attach(backend); backend.BeginErrorReadLine(); backend.BeginOutputReadLine();
            DateTime deadline = DateTime.UtcNow.AddSeconds(45); int port = 0;
            while (DateTime.UtcNow < deadline)
            {
                closing.Token.ThrowIfCancellationRequested();
                if (backend.HasExited) throw new Exception("小镇服务启动失败，请查看日志。内置 Python 需要 Windows 通用 C 运行库；请保持系统更新。");
                if (File.Exists(ready))
                {
                    var payload = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(ready, Encoding.UTF8));
                    port = Convert.ToInt32(payload["port"]); break;
                }
                await Task.Delay(60, closing.Token);
            }
            if (port < 1 || port > 65535) throw new TimeoutException("小镇服务启动超时。");
            origin = new Uri("http://127.0.0.1:" + port + "/");
            var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Program.WorkingDataDirectory, "WebView2"));
            closing.Token.ThrowIfCancellationRequested();
            await browser.EnsureCoreWebView2Async(environment);
            browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
            browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            if (Program.SelfTestFile != null)
                await browser.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync("window.__starhavenTestErrors=[]; window.addEventListener('error',function(e){window.__starhavenTestErrors.push(e.message||'resource error')}); window.addEventListener('unhandledrejection',function(e){window.__starhavenTestErrors.push(String(e.reason))});");
            browser.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            {
                Uri uri; if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out uri) || uri.GetLeftPart(UriPartial.Authority) != origin.GetLeftPart(UriPartial.Authority))
                { e.Cancel = true; if (uri != null && (uri.Scheme == "https" || uri.Scheme == "http")) Program.OpenExternal(uri.AbsoluteUri); }
            };
            browser.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
            { e.Handled = true; Uri uri; if (Uri.TryCreate(e.Uri, UriKind.Absolute, out uri) && (uri.Scheme == "https" || uri.Scheme == "http")) Program.OpenExternal(uri.AbsoluteUri); };
            browser.CoreWebView2.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
            {
                if (Program.SelfTestFile == null) return;
                try
                {
                    if (!e.IsSuccess) throw new Exception("WebView2 navigation failed: " + e.WebErrorStatus);
                    var serializer = new JavaScriptSerializer();
                    Dictionary<string, object> page = null;
                    DateTime renderDeadline = DateTime.UtcNow.AddSeconds(15);
                    while (DateTime.UtcNow < renderDeadline)
                    {
                        string value = await browser.CoreWebView2.ExecuteScriptAsync("JSON.stringify({title:document.title,ready:!!(document.querySelector('.map-art')&&document.querySelector('.map-art').complete&&document.querySelector('.map-art').naturalWidth>0&&document.querySelector('.map-resident[data-agent]')),residents:document.querySelectorAll('.map-resident[data-agent]').length,errors:window.__starhavenTestErrors||[]})");
                        page = serializer.Deserialize<Dictionary<string, object>>(serializer.Deserialize<string>(value));
                        if (Convert.ToBoolean(page["ready"])) break;
                        await Task.Delay(100, closing.Token);
                    }
                    if (page == null || !Convert.ToBoolean(page["ready"])) throw new Exception("Town map did not render: " + serializer.Serialize(page));
                    var errors = page["errors"] as System.Collections.ICollection;
                    if (errors != null && errors.Count > 0) throw new Exception("Town script errors: " + serializer.Serialize(errors));
                    File.WriteAllText(Program.SelfTestFile, serializer.Serialize(new { ok = true, port = port, backend_pid = backend.Id, title = page["title"], residents = page["residents"], data_directory = Program.WorkingDataDirectory }), Encoding.UTF8);
                }
                catch (Exception exc) { Program.Fail(exc); }
                finally { Close(); }
            };
            status.Visible = false; browser.CoreWebView2.Navigate(origin.AbsoluteUri); health.Start();
        }
        catch (OperationCanceledException) { }
        catch (Exception exc) { if (!closing.IsCancellationRequested) { Program.Fail(exc); Close(); } }
    }
    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        closing.Cancel(); health.Stop(); browser.Dispose();
        if (backend != null)
        {
            bool graceful = false;
            try
            {
                if (!backend.HasExited)
                {
                    File.WriteAllText(Path.Combine(readyDirectory, "stop.request"), "stop", Encoding.UTF8);
                    graceful = backend.WaitForExit(8000) && backend.ExitCode == 0;
                }
                else graceful = backend.ExitCode == 0;
                if (!backend.HasExited) { childJob.Dispose(); backend.WaitForExit(3000); }
                if (Program.SelfTestFile != null && File.Exists(Program.SelfTestFile))
                {
                    var serializer = new JavaScriptSerializer();
                    var result = serializer.Deserialize<Dictionary<string, object>>(File.ReadAllText(Program.SelfTestFile, Encoding.UTF8));
                    result["graceful_shutdown"] = graceful;
                    File.WriteAllText(Program.SelfTestFile, serializer.Serialize(result), Encoding.UTF8);
                }
            }
            catch (Exception exc) { Program.Log("Shutdown: " + exc); }
            backend.Dispose(); backend = null;
        }
        childJob.Dispose();
        if (readyDirectory != null) { try { Directory.Delete(readyDirectory, true); } catch { } }
        base.OnFormClosed(e);
    }
}

// All model subprocesses inherit this job. Even a native-host crash closes its
// last handle and terminates the descendants; no global process-name killing.
internal sealed class ChildProcessJob : IDisposable
{
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
    {
        internal long PerProcessUserTime, PerJobUserTime;
        internal uint LimitFlags;
        internal UIntPtr MinimumWorkingSet, MaximumWorkingSet;
        internal uint ActiveProcessLimit;
        internal UIntPtr Affinity;
        internal uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters { internal ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        internal BasicLimits Basic;
        internal IoCounters Io;
        internal UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);
    private IntPtr handle;

    internal ChildProcessJob()
    {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        var limits = new ExtendedLimits(); limits.Basic.LimitFlags = 0x2000; // KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits)))
        {
            int error = Marshal.GetLastWin32Error(); Dispose();
            throw new System.ComponentModel.Win32Exception(error);
        }
    }
    internal void Attach(Process process)
    {
        if (!AssignProcessToJobObject(handle, process.Handle))
        {
            int error = Marshal.GetLastWin32Error();
            try { process.Kill(); } catch { }
            throw new System.ComponentModel.Win32Exception(error, "无法建立小镇服务进程组。");
        }
    }
    public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
}
