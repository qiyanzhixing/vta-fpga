// Package telemetry shares immutable snapshots across all connections.
package telemetry

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
	"vta-fpga/internal/protocol"
)

type Clock interface {
	Now() time.Time
	Monotonic() time.Duration
}
type Config struct {
	DeviceID, DeviceModel string
	Interval              time.Duration
	SourceTimeout         time.Duration
	Clock                 Clock
}
type realClock struct{ start time.Time }

func (c realClock) Now() time.Time           { return time.Now() }
func (c realClock) Monotonic() time.Duration { return time.Since(c.start) }

type cpuSample struct {
	ticks  [8]uint64
	online string
	cores  int
}
type Sampler struct {
	mu           sync.RWMutex
	cfg          Config
	read         func(string) ([]byte, error)
	clock        Clock
	static       protocol.DeviceSummary
	staticErrors []protocol.CollectionError
	h            protocol.Heartbeat
	at           time.Duration
	previous     *cpuSample
	previousAt   time.Duration
}

func pointer[T any](v T) *T { return &v }
func bounded(s string) *string {
	s = strings.Trim(strings.TrimSpace(s), "\x00")
	if !utf8.ValidString(s) {
		return nil
	}
	if len(s) > 128 {
		s = s[:128]
		for !utf8.ValidString(s) {
			s = s[:len(s)-1]
		}
	}
	if s == "" {
		return nil
	}
	return &s
}
func readBounded(path string) ([]byte, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, 1<<20))
	return b, e
}
func New(cfg Config, readFile func(string) ([]byte, error)) *Sampler {
	if cfg.Interval <= 0 {
		cfg.Interval = 5 * time.Second
	}
	clock := cfg.Clock
	if clock == nil {
		clock = realClock{time.Now()}
	}
	if readFile == nil {
		if linuxHost {
			readFile = readBounded
		} else {
			readFile = func(string) ([]byte, error) { return nil, os.ErrNotExist }
		}
	}
	if cfg.SourceTimeout <= 0 {
		cfg.SourceTimeout = 100 * time.Millisecond
	}
	s := &Sampler{cfg: cfg, read: boundedSource(readFile, cfg.SourceTimeout), clock: clock}
	s.loadStatic()
	s.collect()
	return s
}
func codeFor(e error) string {
	if errors.Is(e, os.ErrPermission) {
		return "PERMISSION_DENIED"
	}
	if errors.Is(e, os.ErrNotExist) {
		return "NOT_AVAILABLE"
	}
	return "READ_FAILED"
}
func (s *Sampler) loadStatic() {
	d := protocol.DeviceInfo{ID: bounded(s.cfg.DeviceID), Model: bounded(s.cfg.DeviceModel), OS: protocol.OSInfo{Arch: bounded(runtime.GOARCH)}}
	name := runtime.GOOS
	if linuxHost {
		name = "Linux"
	}
	d.OS.Name = bounded(name)
	errs := []protocol.CollectionError{}
	add := func(field string, p *string, e error) {
		if p == nil {
			code := "NOT_AVAILABLE"
			if e != nil {
				code = codeFor(e)
			}
			errs = append(errs, protocol.CollectionError{Field: field, Code: code})
		}
	}
	add("device.id", d.ID, nil)
	if d.Model == nil {
		b, e := s.read("/proc/device-tree/model")
		if e != nil {
			b, e = s.read("/sys/class/dmi/id/product_name")
		}
		d.Model = bounded(string(b))
		add("device.model", d.Model, e)
	}
	b, e := s.read("/etc/os-release")
	vals := map[string]string{}
	for _, line := range strings.Split(string(b), "\n") {
		k, v, ok := strings.Cut(line, "=")
		if ok {
			if strings.HasPrefix(v, "\"") {
				if u, err := strconv.Unquote(v); err == nil {
					v = u
				}
			}
			vals[k] = strings.Trim(v, "'")
		}
	}
	d.OS.Distribution = bounded(vals["NAME"])
	d.OS.Version = bounded(vals["VERSION_ID"])
	add("device.os.distribution", d.OS.Distribution, e)
	add("device.os.version", d.OS.Version, e)
	b, e = s.read("/proc/sys/kernel/osrelease")
	d.OS.Kernel = bounded(string(b))
	add("device.os.kernel", d.OS.Kernel, e)
	b, e = s.read("/proc/cpuinfo")
	var model *string
	for _, line := range strings.Split(string(b), "\n") {
		k, v, ok := strings.Cut(line, ":")
		if ok && (strings.TrimSpace(k) == "model name" || strings.TrimSpace(k) == "Processor" || strings.TrimSpace(k) == "Hardware") {
			model = bounded(v)
			if model != nil {
				break
			}
		}
	}
	add("cpu.model", model, e)
	s.static = protocol.DeviceSummary{Device: d, CPU: protocol.CPUSummary{Model: model}}
	s.staticErrors = errs
}
func parseCPU(b []byte) (cpuSample, error) {
	s := cpuSample{}
	found := false
	var ids []string
	for _, line := range strings.Split(string(b), "\n") {
		f := strings.Fields(line)
		if len(f) == 0 {
			continue
		}
		if f[0] == "cpu" {
			if len(f) < 9 {
				return s, errors.New("short CPU counters")
			}
			for i := 0; i < 8; i++ {
				v, e := strconv.ParseUint(f[i+1], 10, 64)
				if e != nil {
					return s, e
				}
				s.ticks[i] = v
			}
			found = true
		} else if strings.HasPrefix(f[0], "cpu") {
			if _, e := strconv.Atoi(f[0][3:]); e == nil {
				ids = append(ids, f[0])
			}
		}
	}
	if !found || len(ids) == 0 {
		return s, errors.New("missing CPU data")
	}
	s.cores = len(ids)
	s.online = strings.Join(ids, ",")
	return s, nil
}
func parseMemory(b []byte) (*uint64, *uint64) {
	var total, available *uint64
	for _, line := range strings.Split(string(b), "\n") {
		f := strings.Fields(line)
		if len(f) != 3 || f[2] != "kB" {
			continue
		}
		v, e := strconv.ParseUint(f[1], 10, 64)
		if e != nil || v > math.MaxUint64/1024 {
			continue
		}
		v *= 1024
		if f[0] == "MemTotal:" && v > 0 {
			total = &v
		}
		if f[0] == "MemAvailable:" {
			available = &v
		}
	}
	return total, available
}
func (s *Sampler) collect() {
	h := protocol.Heartbeat{Version: 1, Type: "heartbeat", Device: s.static.Device, CPU: protocol.CPUInfo{CPUSummary: s.static.CPU}, CollectionErrors: append([]protocol.CollectionError{}, s.staticErrors...)}
	add := func(field, code string) {
		h.CollectionErrors = append(h.CollectionErrors, protocol.CollectionError{Field: field, Code: code})
	}
	b, e := s.read("/proc/meminfo")
	total, avail := parseMemory(b)
	memcode := "NOT_AVAILABLE"
	if e != nil {
		memcode = codeFor(e)
	}
	h.Memory.TotalBytes = total
	h.Memory.AvailableBytes = avail
	if total == nil {
		add("memory.total_bytes", memcode)
	}
	if avail == nil {
		add("memory.available_bytes", memcode)
	}
	if total != nil && avail != nil && *avail <= *total {
		used := *total - *avail
		h.Memory.UsedBytes = &used
		h.Memory.UsagePercent = pointer(100 * float64(used) / float64(*total))
	} else {
		if total != nil && avail != nil {
			h.Memory.AvailableBytes = nil
			add("memory.available_bytes", "READ_FAILED")
			memcode = "READ_FAILED"
		}
		add("memory.used_bytes", memcode)
		add("memory.usage_percent", memcode)
	}
	now := s.clock.Monotonic()
	b, e = s.read("/proc/stat")
	cpu, parseErr := parseCPU(b)
	cpuCode := "WARMING_UP"
	if e != nil {
		cpuCode = codeFor(e)
	} else if parseErr != nil {
		cpuCode = "READ_FAILED"
	}
	if e == nil && parseErr == nil {
		h.CPU.LogicalCores = pointer(cpu.cores)
		if s.previous != nil {
			cpuCode = "SAMPLE_RESET"
			old := s.previous
			delta := float64(0)
			idle := float64(0)
			valid := cpu.online == old.online
			for i, v := range cpu.ticks {
				if v < old.ticks[i] {
					valid = false
					break
				}
				diff := float64(v - old.ticks[i])
				delta += diff
				if i == 3 || i == 4 {
					idle += diff
				}
			}
			ms := (now - s.previousAt).Milliseconds()
			if valid && delta > 0 && idle <= delta && ms > 0 {
				h.CPU.UsagePercent = pointer(100 * (delta - idle) / delta)
				h.CPU.SampleWindowMS = &ms
			}
		}
		s.previous = &cpu
		s.previousAt = now
	} else {
		s.previous = nil
		add("cpu.logical_cores", cpuCode)
	}
	if h.CPU.UsagePercent == nil {
		add("cpu.usage_percent", cpuCode)
		add("cpu.sample_window_ms", cpuCode)
	}
	b, e = s.read("/proc/uptime")
	fields := strings.Fields(string(b))
	if e == nil && len(fields) > 0 {
		v, err := strconv.ParseFloat(fields[0], 64)
		if err == nil && v >= 0 && !math.IsInf(v, 0) && !math.IsNaN(v) {
			h.UptimeSec = &v
		}
	}
	if h.UptimeSec == nil {
		code := "READ_FAILED"
		if e != nil {
			code = codeFor(e)
		}
		add("uptime_sec", code)
	}
	h.Timestamp = s.clock.Now().UTC().Format(time.RFC3339Nano)
	s.mu.Lock()
	s.h = h
	s.at = s.clock.Monotonic()
	s.mu.Unlock()
}
func (s *Sampler) Run(ctx context.Context) {
	ticker := time.NewTicker(s.cfg.Interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.collect()
		}
	}
}

// Deep copies keep consumers from changing the shared cache or racing its next sample.
func (s *Sampler) Snapshot(service protocol.ServiceStatus) protocol.Heartbeat {
	s.mu.RLock()
	b, _ := json.Marshal(s.h)
	at := s.at
	s.mu.RUnlock()
	var h protocol.Heartbeat
	_ = json.Unmarshal(b, &h)
	h.Service = service
	h.SampleAgeMS = max(0, (s.clock.Monotonic() - at).Milliseconds())
	return h
}
func (s *Sampler) Summary() protocol.DeviceSummary {
	h := s.Snapshot(protocol.ServiceStatus{})
	return protocol.DeviceSummary{Device: h.Device, CPU: h.CPU.CPUSummary, Memory: h.Memory.MemorySummary}
}

// At most one source read can be stuck. Timeouts do not start replacement
// reads until that worker actually exits, so a bad kernel source cannot cause
// an accumulating goroutine queue or stop null/error heartbeats.
func boundedSource(read func(string) ([]byte, error), timeout time.Duration) func(string) ([]byte, error) {
	busy := make(chan struct{}, 1)
	type result struct {
		body []byte
		err  error
	}
	return func(path string) ([]byte, error) {
		select {
		case busy <- struct{}{}:
		default:
			return nil, errors.New("previous system read still running")
		}
		done := make(chan result, 1)
		go func() { b, e := read(path); <-busy; done <- result{b, e} }()
		timer := time.NewTimer(timeout)
		defer timer.Stop()
		select {
		case r := <-done:
			return r.body, r.err
		case <-timer.C:
			return nil, errors.New("system read timed out")
		}
	}
}
