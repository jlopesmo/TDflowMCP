param([int]$ProcId, [string]$Out)
# Captures the visible top-level windows of ONE process (the disposable TouchDesigner the harness started) to PNGs.
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static List<IntPtr> Of(uint pid) {
    var o = new List<IntPtr>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid && IsWindowVisible(h)) o.Add(h); return true; }, IntPtr.Zero);
    return o;
  }
}
"@
$i = 0
$files = @()
foreach ($h in [W]::Of([uint32]$ProcId)) {
  $r = New-Object W+RECT
  [void][W]::GetWindowRect($h, [ref]$r)
  $w = $r.R - $r.L; $hh = $r.B - $r.T
  if ($w -lt 120 -or $hh -lt 60) { continue }
  $bmp = New-Object System.Drawing.Bitmap $w, $hh
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $dc = $g.GetHdc()
  [void][W]::PrintWindow($h, $dc, 2)
  $g.ReleaseHdc($dc)
  $f = "$Out-$i.png"
  $bmp.Save($f, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  $files += $f
  $i++
}
$files -join ";"
