package telemetry

import (
	"context"
	"os"
	"sync"
	"testing"
	"time"
	"vta-fpga/internal/protocol"
)

type fakeClock struct {
	wall time.Time
	mono time.Duration
}

func (c *fakeClock) Now() time.Time           { return c.wall }
func (c *fakeClock) Monotonic() time.Duration { return c.mono }
func TestSampler(t *testing.T) {
	clock := &fakeClock{wall: time.Unix(100, 0)}
	files := map[string]string{"/proc/meminfo": "MemTotal: 1048576 kB\nMemAvailable: 786432 kB\n", "/proc/stat": "cpu 10 0 10 80 0 0 0 0 99 0\ncpu0 1\n", "/proc/uptime": "3600.5 0", "/proc/cpuinfo": "model name : test cpu\n", "/etc/os-release": "NAME=\"Test OS\"\nVERSION_ID=1\n", "/proc/sys/kernel/osrelease": "6.1"}
	read := func(p string) ([]byte, error) {
		s, ok := files[p]
		if !ok {
			return nil, os.ErrNotExist
		}
		return []byte(s), nil
	}
	s := New(Config{DeviceID: "a", Clock: clock}, read)
	h := s.Snapshot(protocol.ServiceStatus{State: "idle"})
	if h.Memory.UsedBytes == nil || *h.Memory.UsedBytes != 268435456 || *h.Memory.UsagePercent != 25 {
		t.Fatal(h.Memory)
	}
	if h.CPU.UsagePercent != nil || !hasError(h, "cpu.usage_percent", "WARMING_UP") {
		t.Fatal(h.CPU, h.CollectionErrors)
	}
	clock.mono = 5 * time.Second
	files["/proc/stat"] = "cpu 20 0 20 160 0 0 0 0 999 0\ncpu0 2\n"
	s.collect()
	h = s.Snapshot(protocol.ServiceStatus{})
	if h.CPU.UsagePercent == nil || *h.CPU.UsagePercent != 20 || *h.CPU.SampleWindowMS != 5000 {
		t.Fatal(h.CPU)
	}
	clock.wall = clock.wall.Add(-time.Hour)
	clock.mono += time.Second
	if h = s.Snapshot(protocol.ServiceStatus{}); h.SampleAgeMS != 1000 {
		t.Fatal(h.SampleAgeMS)
	}
	files["/proc/stat"] = "cpu 1 0 1 1 0 0 0 0\ncpu0 0\ncpu1 0\n"
	s.collect()
	h = s.Snapshot(protocol.ServiceStatus{})
	if !hasError(h, "cpu.usage_percent", "SAMPLE_RESET") {
		t.Fatal(h)
	}
	files["/proc/meminfo"] = "MemTotal: 1048576 kB\nMemFree: 786432 kB\n"
	s.collect()
	h = s.Snapshot(protocol.ServiceStatus{})
	if h.Memory.AvailableBytes != nil || !hasError(h, "memory.used_bytes", "NOT_AVAILABLE") {
		t.Fatal(h.Memory, h.CollectionErrors)
	}
}
func hasError(h protocol.Heartbeat, field, code string) bool {
	for _, e := range h.CollectionErrors {
		if e.Field == field && e.Code == code {
			return true
		}
	}
	return false
}
func TestPermission(t *testing.T) {
	s := New(Config{}, func(string) ([]byte, error) { return nil, os.ErrPermission })
	h := s.Snapshot(protocol.ServiceStatus{})
	if !hasError(h, "memory.total_bytes", "PERMISSION_DENIED") {
		t.Fatal(h.CollectionErrors)
	}
	b := s.Summary()
	if b.Memory.TotalBytes != nil {
		t.Fatal(b)
	}
}
func TestConcurrentSnapshots(t *testing.T) {
	s := New(Config{Interval: time.Millisecond, DeviceID: "original"}, nil)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 50; j++ {
				h := s.Snapshot(protocol.ServiceStatus{})
				*h.Device.ID = "changed"
				s.Summary()
			}
		}()
	}
	wg.Wait()
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("sampler did not stop")
	}
	if *s.Summary().Device.ID != "original" {
		t.Fatal("snapshot mutated cache")
	}
}
func TestBoundedSourceRead(t *testing.T) {
	release := make(chan struct{})
	defer close(release)
	done := make(chan *Sampler, 1)
	go func() { done <- New(Config{}, func(string) ([]byte, error) { <-release; return nil, os.ErrNotExist }) }()
	select {
	case s := <-done:
		h := s.Snapshot(protocol.ServiceStatus{})
		if !hasError(h, "memory.total_bytes", "READ_FAILED") {
			t.Fatal(h.CollectionErrors)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("blocked source prevented sampler initialization")
	}
}
