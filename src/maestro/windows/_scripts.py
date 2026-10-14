"""Trusted PowerShell source for remote Windows Service Control Manager calls."""

WINDOWS_SERVICE = r"""
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][ValidateSet('Get', 'Start', 'Stop')][string]$Operation
)
$ErrorActionPreference = 'Stop'

$controller = $null
$failure = $null
$failureId = $null
$failureMessage = $null
$failureCategory = $null
$initialState = $null
$finalState = $null
$changed = $false
try {
    $controller = [System.ServiceProcess.ServiceController]::new()
    $controller.ServiceName = $Name
    $controller.Refresh()
    $initialState = [string]$controller.Status
    $finalState = $initialState

    if ($Operation -eq 'Start') {
        if ($initialState -eq 'Stopped') {
            $null = $controller.Start()
            $changed = $true
        } elseif ($initialState -ne 'Running') {
            $failureId = 'Maestro.WindowsServiceInvalidState'
            $failureMessage = 'The Windows service is not in a state that can be started.'
            $failureCategory = [System.Management.Automation.ErrorCategory]::InvalidOperation
        }
    } elseif ($Operation -eq 'Stop') {
        if ($initialState -eq 'Running' -or $initialState -eq 'Paused') {
            $nativeStopTypeName = 'Maestro.Windows.RemoteHelpers.V1.NativeServiceStop'
            if ($null -eq ($nativeStopTypeName -as [type])) {
                $nativeStopSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Maestro.Windows.RemoteHelpers.V1
{
    public static class NativeServiceStop
    {
        private const uint ScManagerConnect = 0x0001;
        private const uint ServiceStop = 0x0020;
        private const uint ServiceControlStop = 0x0001;

        [StructLayout(LayoutKind.Sequential)]
        private struct ServiceStatus
        {
            public uint ServiceType;
            public uint CurrentState;
            public uint ControlsAccepted;
            public uint Win32ExitCode;
            public uint ServiceSpecificExitCode;
            public uint CheckPoint;
            public uint WaitHint;
        }

        [DllImport("advapi32.dll", EntryPoint = "OpenSCManagerW", CharSet = CharSet.Unicode,
            ExactSpelling = true, SetLastError = true)]
        private static extern IntPtr OpenSCManagerW(
            string machineName, string databaseName, uint desiredAccess);

        [DllImport("advapi32.dll", EntryPoint = "OpenServiceW", CharSet = CharSet.Unicode,
            ExactSpelling = true, SetLastError = true)]
        private static extern IntPtr OpenServiceW(
            IntPtr manager, string serviceName, uint desiredAccess);

        [DllImport("advapi32.dll", EntryPoint = "ControlService", ExactSpelling = true,
            SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool ControlService(
            IntPtr service, uint control, out ServiceStatus status);

        [DllImport("advapi32.dll", EntryPoint = "CloseServiceHandle", ExactSpelling = true,
            SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseServiceHandle(IntPtr handle);

        public static void Stop(string serviceName)
        {
            IntPtr manager = IntPtr.Zero;
            IntPtr service = IntPtr.Zero;
            Exception failure = null;
            try
            {
                manager = OpenSCManagerW(null, null, ScManagerConnect);
                if (manager == IntPtr.Zero)
                {
                    int errorCode = Marshal.GetLastWin32Error();
                    throw new Win32Exception(errorCode);
                }

                service = OpenServiceW(manager, serviceName, ServiceStop);
                if (service == IntPtr.Zero)
                {
                    int errorCode = Marshal.GetLastWin32Error();
                    throw new Win32Exception(errorCode);
                }

                ServiceStatus status;
                if (!ControlService(service, ServiceControlStop, out status))
                {
                    int errorCode = Marshal.GetLastWin32Error();
                    throw new Win32Exception(errorCode);
                }
            }
            catch (Exception exception)
            {
                failure = exception;
            }
            finally
            {
                if (service != IntPtr.Zero && !CloseServiceHandle(service))
                {
                    int errorCode = Marshal.GetLastWin32Error();
                    if (failure == null)
                    {
                        failure = new Win32Exception(errorCode);
                    }
                }
                if (manager != IntPtr.Zero && !CloseServiceHandle(manager))
                {
                    int errorCode = Marshal.GetLastWin32Error();
                    if (failure == null)
                    {
                        failure = new Win32Exception(errorCode);
                    }
                }
            }

            if (failure != null)
            {
                throw failure;
            }
        }
    }
}
'@
                $null = Add-Type -TypeDefinition $nativeStopSource -ErrorAction Stop
            }
            $nativeStopType = $nativeStopTypeName -as [type]
            $nativeStopMethod = $nativeStopType.GetMethod('Stop')
            $null = $nativeStopMethod.Invoke($null, [object[]]@($Name))
            $changed = $true
        } elseif ($initialState -ne 'Stopped') {
            $failureId = 'Maestro.WindowsServiceInvalidState'
            $failureMessage = 'The Windows service is not in a state that can be stopped.'
            $failureCategory = [System.Management.Automation.ErrorCategory]::InvalidOperation
        }
    }

    if ($changed) {
        $controller.Refresh()
        $finalState = [string]$controller.Status
    }
} catch {
    $failure = $_
} finally {
    if ($null -ne $controller) {
        try {
            $controller.Dispose()
        } catch {
            if ($null -eq $failure -and $null -eq $failureId) {
                $failure = $_
            }
        }
    }
}

if ($null -ne $failure) {
    $failureId = 'Maestro.WindowsServiceCommandFailed'
    $failureMessage = 'Remote Windows service management failed.'
    $failureCategory = $failure.CategoryInfo.Category
    $nativeErrorCode = $null
    $exception = $failure.Exception
    while ($null -ne $exception) {
        if ($exception -is [System.ComponentModel.Win32Exception]) {
            $nativeErrorCode = $exception.NativeErrorCode
            break
        }
        $exception = $exception.InnerException
    }

    switch ($nativeErrorCode) {
        1060 {
            $failureId = 'Maestro.WindowsServiceNotFound'
            $failureMessage = 'The requested Windows service does not exist.'
            $failureCategory = [System.Management.Automation.ErrorCategory]::ObjectNotFound
        }
        5 {
            $failureId = 'Maestro.WindowsServiceDenied'
            $failureMessage = 'Access to the Windows service was denied.'
            $failureCategory = [System.Management.Automation.ErrorCategory]::PermissionDenied
        }
        1051 {
            $failureId = 'Maestro.WindowsServiceDependentServices'
            $failureMessage = 'The Windows service has dependent services that prevent ' +
                'this operation.'
            $failureCategory = [System.Management.Automation.ErrorCategory]::InvalidOperation
        }
        1058 {
            $failureId = 'Maestro.WindowsServiceDisabled'
            $failureMessage = 'The Windows service is disabled.'
            $failureCategory = [System.Management.Automation.ErrorCategory]::InvalidOperation
        }
    }

    $permissionDeniedCategory = [System.Management.Automation.ErrorCategory]::PermissionDenied
    if (
        $failureId -eq 'Maestro.WindowsServiceCommandFailed' -and
        $failure.CategoryInfo.Category -eq $permissionDeniedCategory
    ) {
        $failureId = 'Maestro.WindowsServiceDenied'
        $failureMessage = 'Access to the Windows service was denied.'
        $failureCategory = [System.Management.Automation.ErrorCategory]::PermissionDenied
    }
}

if ($null -ne $failureId) {
    Write-Error -Message $failureMessage -ErrorId $failureId -Category $failureCategory `
        -ErrorAction Continue
    return
}

@{ initial_state = $initialState; final_state = $finalState; changed = $changed } |
    ConvertTo-Json -Compress
"""
