// Custodian Setup.exe: installs Custodian on this PC, or updates it.
//
//   Custodian Setup.exe            inside a Custodian folder: runs install.ps1 there. Anywhere else:
//                                  asks where to put it (default %USERPROFILE%\Custodian), gets the
//                                  code with git (installing git with winget if it's missing), then
//                                  runs install.ps1 - Python, the packages, the web app, Jarvis and its
//                                  voice, the shortcuts.
//   Custodian Setup.exe --update   runs update.ps1: the newest code, then whatever it needs.
//
// It does nothing by itself that install.ps1 and update.ps1 don't (read them in the folder); it only
// finds or fetches them. Built by installer\build.ps1 with the C# compiler that comes with Windows.

using System;
using System.Diagnostics;
using System.IO;

static class Setup
{
    static int Main(string[] args)
    {
        Console.Title = "Custodian Setup";
        bool update = Array.Exists(args, a => a == "--update" || a == "/update");
        try
        {
            string root = FindRoot(AppDomain.CurrentDomain.BaseDirectory);
            if (root == null)
            {
                if (update)
                {
                    Say("No Custodian folder was found next to Custodian Setup.exe to update.", ConsoleColor.Yellow);
                    return Finish(1);
                }
                root = Fetch();
                if (root == null) return Finish(1);
            }
            Say((update ? "Updating" : "Setting up") + " Custodian in " + root, ConsoleColor.Cyan);
            int code = RunPowerShell(Path.Combine(root, update ? "update.ps1" : "install.ps1"), root, update ? " -NoPause" : "");
            if (code == 0 && !update) Say("Done. Start it from the Start menu (Custodian > Jarvis) or the Jarvis icon on the desktop.", ConsoleColor.Green);
            return Finish(code);
        }
        catch (Exception e)
        {
            Say("Setup stopped: " + e.Message, ConsoleColor.Red);
            return Finish(1);
        }
    }

    // The Custodian folder this exe is in (or under, like installer\dist), or null.
    static string FindRoot(string start)
    {
        var dir = new DirectoryInfo(start);
        for (int i = 0; dir != null && i < 4; i++, dir = dir.Parent)
        {
            if (File.Exists(Path.Combine(dir.FullName, "install.ps1")) && Directory.Exists(Path.Combine(dir.FullName, "backend")))
                return dir.FullName;
        }
        return null;
    }

    static string Fetch()
    {
        string suggested = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Custodian");
        Console.Write("Install Custodian where? [" + suggested + "] ");
        string answer = (Console.ReadLine() ?? "").Trim().Trim('"');
        string target = answer.Length > 0 ? answer : suggested;
        if (File.Exists(Path.Combine(target, "install.ps1"))) return target;  // there already: set it up again
        if (Directory.Exists(target) && Directory.GetFileSystemEntries(target).Length > 0)
        {
            Say(target + " isn't empty; choose an empty or new folder.", ConsoleColor.Yellow);
            return null;
        }
        if (!HasGit())
        {
            Say("Custodian is fetched with git, which isn't installed. Installing it with winget...", ConsoleColor.Cyan);
            if (Run("winget", "install -e --id Git.Git --accept-source-agreements --accept-package-agreements", null) != 0 || !HasGit())
            {
                Say("Couldn't install git. Install it from https://git-scm.com and run Setup again.", ConsoleColor.Yellow);
                return null;
            }
        }
        Say("Getting Custodian from " + Repository.Url + " ...", ConsoleColor.Cyan);
        if (Run(GitExe(), "clone --depth 50 \"" + Repository.Url + "\" \"" + target + "\"", null) != 0)
        {
            Say("git couldn't get it (no network, or no access to the repository).", ConsoleColor.Yellow);
            return null;
        }
        return target;
    }

    static bool HasGit()
    {
        try { return Run(GitExe(), "--version", null, quiet: true) == 0; } catch { return false; }
    }

    static string GitExe()
    {
        string installed = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Git", "cmd", "git.exe");
        return File.Exists(installed) ? installed : "git";  // just installed: not on this process's PATH yet
    }

    static int RunPowerShell(string script, string folder, string extra)
    {
        string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
        return Run(powershell, "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"" + extra, folder);
    }

    static int Run(string file, string arguments, string folder, bool quiet = false)
    {
        var start = new ProcessStartInfo(file, arguments) { UseShellExecute = false, RedirectStandardOutput = quiet, RedirectStandardError = quiet };
        if (folder != null) start.WorkingDirectory = folder;
        using (var process = Process.Start(start))
        {
            if (quiet) { process.StandardOutput.ReadToEnd(); process.StandardError.ReadToEnd(); }
            process.WaitForExit();
            return process.ExitCode;
        }
    }

    static void Say(string text, ConsoleColor color)
    {
        Console.ForegroundColor = color;
        Console.WriteLine(text);
        Console.ResetColor();
    }

    static int Finish(int code)
    {
        Console.WriteLine();
        Console.Write("Press Enter to close.");
        Console.ReadLine();
        return code;
    }
}
