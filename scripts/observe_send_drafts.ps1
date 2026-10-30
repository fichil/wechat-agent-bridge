param([int[]]$ProcessIds)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$sha = [System.Security.Cryptography.SHA256]::Create()
$records = @()
foreach ($processId in $ProcessIds) {
    try {
        $process = Get-Process -Id $processId
        $handle = $process.MainWindowHandle
        if ($handle -eq 0) { continue }
        $rootElement = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
        $condition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
        $edits = $rootElement.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
        $values = @()
        for ($index = 0; $index -lt $edits.Count; $index++) {
            $pattern = $null
            if ($edits[$index].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
                $value = ([System.Windows.Automation.ValuePattern]$pattern).Current.Value
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($value)
                $values += @{ length = $bytes.Length; sha256 = [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLower(); readonly = ([System.Windows.Automation.ValuePattern]$pattern).Current.IsReadOnly }
            }
        }
        $records += @{ pid = $processId; hwnd = $handle.ToInt64(); edits = $values }
    } catch { $records += @{ pid = $processId; unavailable = $true } }
}
$sha.Dispose()
ConvertTo-Json -InputObject @($records) -Depth 5 -Compress
