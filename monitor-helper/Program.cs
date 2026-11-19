using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text.Json;
using LibreHardwareMonitor.Hardware;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--dump")
        {
            var probe = new Computer { IsCpuEnabled = true, IsGpuEnabled = true, IsMotherboardEnabled = true };
            probe.Open();
            probe.Accept(new UpdateVisitor());
            Thread.Sleep(1000);
            probe.Accept(new UpdateVisitor());
            foreach (var item in probe.Hardware.SelectMany(Flatten))
                foreach (var sensor in item.Sensors)
                    Console.WriteLine($"{item.HardwareType}|{sensor.SensorType}|{sensor.Name}|{sensor.Value}");
            probe.Close();
            return 0;
        }

        var computer = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
        };
        CpuClockReader? cpuClockReader = null;
        string? cpuClockReaderError = null;

        try
        {
            computer.Open();
            try
            {
                cpuClockReader = new CpuClockReader();
            }
            catch (System.ComponentModel.Win32Exception exception)
            {
                cpuClockReaderError = exception.Message;
            }
            var stop = Task.Run(() => Console.ReadLine() is null);
            while (!stop.IsCompleted)
            {
                var visitor = new UpdateVisitor();
                computer.Accept(visitor);
                var hardware = computer.Hardware.SelectMany(Flatten).ToArray();
                var cpu = hardware.Where(item => item.HardwareType == HardwareType.Cpu).ToArray();
                var gpus = hardware.Where(item => item.HardwareType == HardwareType.GpuNvidia).ToArray();
                var cpuSensors = cpu.SelectMany(item => item.Sensors).ToArray();
                var gpuSensors = gpus.SelectMany(item => item.Sensors).ToArray();
                var temperatures = cpuSensors
                    .Where(sensor => sensor.SensorType == SensorType.Temperature
                        && IsCoreTemperature(sensor.Name)
                        && sensor.Value.HasValue)
                    .Select(sensor => (double)sensor.Value!.Value)
                    .Where(double.IsFinite)
                    .ToArray();
                var cpuClocks = cpuSensors
                    .Where(sensor => sensor.SensorType == SensorType.Clock
                        && sensor.Name.Contains("Core", StringComparison.OrdinalIgnoreCase)
                        && sensor.Value.HasValue)
                    .Select(sensor => (double)sensor.Value!.Value)
                    .Where(double.IsFinite)
                    .ToArray();
                var cpuPackageTemperature = FirstValue(cpuSensors, sensor =>
                    sensor.SensorType == SensorType.Temperature
                    && (sensor.Name.Equals("CPU Package", StringComparison.OrdinalIgnoreCase)
                        || sensor.Name.Contains("Tctl", StringComparison.OrdinalIgnoreCase)
                        || sensor.Name.Contains("Tdie", StringComparison.OrdinalIgnoreCase)));
                var cpuHottestCore = temperatures.Length > 0 ? temperatures.Max() : (double?)null;
                if (temperatures.Length == 0 && cpuPackageTemperature.HasValue)
                    temperatures = new[] { cpuPackageTemperature.Value };
                var cpuLoad = FirstValue(cpuSensors, sensor =>
                    sensor.SensorType == SensorType.Load
                    && sensor.Name.Equals("CPU Total", StringComparison.OrdinalIgnoreCase));
                var cpuPower = FirstValue(cpuSensors, sensor =>
                    sensor.SensorType == SensorType.Power
                    && sensor.Name.Equals("CPU Package", StringComparison.OrdinalIgnoreCase));
                var cpuVoltage = FirstValue(cpuSensors, sensor =>
                    sensor.SensorType == SensorType.Voltage
                    && sensor.Name.Equals("CPU Core", StringComparison.OrdinalIgnoreCase));
                var gpuCoreClock = FirstValue(gpuSensors, sensor =>
                    sensor.SensorType == SensorType.Clock
                    && !sensor.Name.Contains("Memory", StringComparison.OrdinalIgnoreCase));
                var gpuMemoryClock = FirstValue(gpuSensors, sensor =>
                    sensor.SensorType == SensorType.Clock
                    && sensor.Name.Contains("Memory", StringComparison.OrdinalIgnoreCase));
                double? Gpu(SensorType type, string name) => FirstValue(gpuSensors, sensor =>
                    sensor.SensorType == type
                    && sensor.Name.Equals(name, StringComparison.OrdinalIgnoreCase));
                var gpuMemoryUsedMB = Gpu(SensorType.SmallData, "GPU Memory Used");
                var gpuMemoryTotalMB = Gpu(SensorType.SmallData, "GPU Memory Total");
                var gpuPower = Gpu(SensorType.Load, "GPU Board Power") ?? Gpu(SensorType.Load, "GPU Power");
                var memory = GetMemoryStatus();
                var performance = GetPerformance();
                var processCount = (int)performance.ProcessCount;

                var cpuClock = Average(cpuClocks);
                string? cpuClockError = cpuClockReaderError;
                if (!cpuClock.HasValue)
                {
                    try
                    {
                        cpuClock = cpuClockReader?.Read() ?? GetWindowsCpuClockMHz();
                    }
                    catch (System.ComponentModel.Win32Exception exception)
                    {
                        cpuClockError = exception.Message;
                    }
                }
                var hardwareErrors = visitor.Errors.Take(3).ToArray();
                if (cpuClockError is not null)
                    hardwareErrors = hardwareErrors.Append(cpuClockError).Take(3).ToArray();
                using var identity = WindowsIdentity.GetCurrent();
                var isElevated = new WindowsPrincipal(identity)
                    .IsInRole(WindowsBuiltInRole.Administrator);

                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    cpuClockMHz = cpuClock,
                    cpuClockMaxMHz = cpuClocks.Length > 0 ? cpuClocks.Max() : (double?)null,
                    cpuCoreTemperatures = temperatures,
                    cpuPackageTemperature,
                    cpuHottestCoreTemperature = cpuHottestCore ?? cpuPackageTemperature,
                    cpuLoadPercent = cpuLoad,
                    cpuPowerW = cpuPower,
                    cpuVoltageV = cpuVoltage,
                    cpuTemperatureAvailable = temperatures.Length > 0,
                    cpuDetected = cpu.Length > 0,
                    isElevated,
                    cpuTemperatureSensorCount = cpuSensors.Count(sensor =>
                        sensor.SensorType == SensorType.Temperature),
                    gpuClockMHz = gpuCoreClock,
                    gpuMemoryClockMHz = gpuMemoryClock,
                    gpuTemperature = Gpu(SensorType.Temperature, "GPU Core"),
                    gpuHotSpotTemperature = Gpu(SensorType.Temperature, "GPU Hot Spot"),
                    gpuMemoryJunctionTemperature = Gpu(SensorType.Temperature, "GPU Memory Junction"),
                    gpuLoadPercent = Gpu(SensorType.Load, "GPU Core"),
                    gpuPowerW = gpuPower,
                    gpuVoltageV = Gpu(SensorType.Voltage, "GPU Core Voltage"),
                    gpuFanRpm = Gpu(SensorType.Fan, "GPU Fan 1"),
                    gpuMemoryUsedMB,
                    gpuMemoryTotalMB,
                    nvidiaGpuDetected = gpus.Length > 0,
                    ramUsedBytes = memory.TotalPhysical - memory.AvailablePhysical,
                    ramTotalBytes = memory.TotalPhysical,
                    ramAvailableBytes = memory.AvailablePhysical,
                    cachedBytes = (ulong)performance.SystemCache * (ulong)performance.PageSize,
                    commitTotalBytes = (ulong)performance.CommitTotal * (ulong)performance.PageSize,
                    commitLimitBytes = (ulong)performance.CommitLimit * (ulong)performance.PageSize,
                    processCount,
                    threadCount = performance.ThreadCount,
                    handleCount = performance.HandleCount,
                    uptimeSeconds = Environment.TickCount64 / 1000,
                    hardwareErrors,
                }));
                Console.Out.Flush();
                Thread.Sleep(1000);
            }
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception.Message);
            return 1;
        }
        finally
        {
            cpuClockReader?.Dispose();
            computer.Close();
        }
    }

    private static bool IsCoreTemperature(string name) =>
        (name.Contains("Core #", StringComparison.OrdinalIgnoreCase)
            || name.StartsWith("CCD", StringComparison.OrdinalIgnoreCase))
        && !name.Contains("Distance", StringComparison.OrdinalIgnoreCase);

    private static IEnumerable<IHardware> Flatten(IHardware hardware)
    {
        yield return hardware;
        foreach (var child in hardware.SubHardware)
        foreach (var nested in Flatten(child))
            yield return nested;
    }

    private static double? FirstValue(IEnumerable<ISensor> sensors, Func<ISensor, bool> predicate)
    {
        var sensor = sensors.FirstOrDefault(sensor =>
            predicate(sensor) && sensor.Value.HasValue && float.IsFinite(sensor.Value.Value));
        return sensor?.Value is float value ? value : null;
    }

    private static double? Average(IEnumerable<double> values)
    {
        var samples = values.ToArray();
        return samples.Length == 0 ? null : samples.Average();
    }

    private static double? GetWindowsCpuClockMHz()
    {
        var processors = new PROCESSOR_POWER_INFORMATION[Environment.ProcessorCount];
        var status = CallNtPowerInformation(
            11, IntPtr.Zero, 0, processors, (uint)(Marshal.SizeOf<PROCESSOR_POWER_INFORMATION>()
                * processors.Length));
        if (status != 0)
            throw new System.ComponentModel.Win32Exception(
                status, "Windows could not read the current CPU frequency.");

        var frequencies = processors
            .Select(processor => (double)processor.CurrentMhz)
            .Where(frequency => frequency > 0 && double.IsFinite(frequency));
        return Average(frequencies);
    }

    private sealed class CpuClockReader : IDisposable
    {
        private readonly IntPtr _query;
        private readonly IntPtr _counter;

        public CpuClockReader()
        {
            var status = PdhOpenQueryW(null, IntPtr.Zero, out _query);
            if (status != 0)
                throw new System.ComponentModel.Win32Exception(
                    (int)status, "Windows could not open the CPU performance counter.");

            status = PdhAddEnglishCounterW(
                _query, @"\Processor Information(_Total)\% Processor Performance", 0, out _counter);
            if (status != 0)
            {
                PdhCloseQuery(_query);
                throw new System.ComponentModel.Win32Exception(
                    (int)status, "Windows could not read CPU performance.");
            }

            PdhCollectQueryData(_query);
        }

        public double? Read()
        {
            var baseFrequency = GetWindowsCpuClockMHz();
            if (PdhCollectQueryData(_query) != 0
                || PdhGetFormattedCounterValue(_counter, 0x200, out _, out var value) != 0
                || value.CStatus > 1
                || !double.IsFinite(value.DoubleValue)
                || value.DoubleValue <= 0)
                return baseFrequency;

            return baseFrequency * value.DoubleValue / 100;
        }

        public void Dispose() => PdhCloseQuery(_query);
    }

    private static PERFORMANCE_INFORMATION GetPerformance()
    {
        var info = new PERFORMANCE_INFORMATION { Size = (uint)Marshal.SizeOf<PERFORMANCE_INFORMATION>() };
        if (!GetPerformanceInfo(ref info, info.Size))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        return info;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PERFORMANCE_INFORMATION
    {
        public uint Size;
        public nuint CommitTotal;
        public nuint CommitLimit;
        public nuint CommitPeak;
        public nuint PhysicalTotal;
        public nuint PhysicalAvailable;
        public nuint SystemCache;
        public nuint KernelTotal;
        public nuint KernelPaged;
        public nuint KernelNonpaged;
        public nuint PageSize;
        public uint HandleCount;
        public uint ProcessCount;
        public uint ThreadCount;
    }

    [DllImport("psapi.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetPerformanceInfo(ref PERFORMANCE_INFORMATION info, uint size);

    private static MEMORYSTATUSEX GetMemoryStatus()
    {
        var status = new MEMORYSTATUSEX { Length = (uint)Marshal.SizeOf<MEMORYSTATUSEX>() };
        if (!GlobalMemoryStatusEx(ref status))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        return status;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MEMORYSTATUSEX
    {
        public uint Length;
        public uint MemoryLoad;
        public ulong TotalPhysical;
        public ulong AvailablePhysical;
        public ulong TotalPageFile;
        public ulong AvailablePageFile;
        public ulong TotalVirtual;
        public ulong AvailableVirtual;
        public ulong AvailableExtendedVirtual;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESSOR_POWER_INFORMATION
    {
        public uint Number;
        public uint MaxMhz;
        public uint CurrentMhz;
        public uint MhzLimit;
        public uint MaxIdleState;
        public uint CurrentIdleState;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PDH_FMT_COUNTERVALUE
    {
        public uint CStatus;
        public double DoubleValue;
    }

    [DllImport("pdh.dll", CharSet = CharSet.Unicode)]
    private static extern uint PdhOpenQueryW(
        string? dataSource, IntPtr userData, out IntPtr query);

    [DllImport("pdh.dll", CharSet = CharSet.Unicode)]
    private static extern uint PdhAddEnglishCounterW(
        IntPtr query, string counterPath, ulong userData, out IntPtr counter);

    [DllImport("pdh.dll")]
    private static extern uint PdhCollectQueryData(IntPtr query);

    [DllImport("pdh.dll")]
    private static extern uint PdhGetFormattedCounterValue(
        IntPtr counter, uint format, out uint counterType, out PDH_FMT_COUNTERVALUE value);

    [DllImport("pdh.dll")]
    private static extern uint PdhCloseQuery(IntPtr query);

    [DllImport("powrprof.dll", SetLastError = true)]
    private static extern int CallNtPowerInformation(
        int informationLevel,
        IntPtr inputBuffer,
        uint inputBufferLength,
        [Out] PROCESSOR_POWER_INFORMATION[] outputBuffer,
        uint outputBufferLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MEMORYSTATUSEX buffer);

    private sealed class UpdateVisitor : IVisitor
    {
        public List<string> Errors { get; } = new();

        public void VisitComputer(IComputer computer) => computer.Traverse(this);

        public void VisitHardware(IHardware hardware)
        {
            try
            {
                hardware.Update();
            }
            catch (Exception exception)
            {
                Errors.Add($"{hardware.Name}: {exception.Message}");
            }
            foreach (var child in hardware.SubHardware)
                child.Accept(this);
        }

        public void VisitSensor(ISensor sensor) { }
        public void VisitParameter(IParameter parameter) { }
    }
}
