package server

import (
	"context"
	"encoding/json"
	"github.com/gorilla/websocket"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
	"vta-fpga/internal/denoise"
	"vta-fpga/internal/engine"
	"vta-fpga/internal/protocol"
	"vta-fpga/internal/telemetry"
)

const request = `{"version":1,"type":"denoise","request_id":"r1","data":{"time_unit":"min","time":[0,1,2],"channels":["a"],"intensity":[[1],[2],[3]]},"config":{"method":"mean","params":{"window_size":3}}}`

func setup(t *testing.T, r Runner, configure func(*Config)) (*Server, string, *engine.Gate) {
	t.Helper()
	reg := denoise.NewRegistry()
	if r == nil {
		r = engine.New(reg)
	}
	g := engine.NewGate(1)
	cfg := DefaultConfig()
	cfg.HeartbeatInterval = 20 * time.Millisecond
	cfg.PingInterval = 30 * time.Millisecond
	cfg.PongTimeout = time.Second
	if configure != nil {
		configure(&cfg)
	}
	s := New(cfg, reg, r, g, telemetry.New(telemetry.Config{}, nil))
	ts := httptest.NewServer(s.Handler())
	t.Cleanup(func() {
		ctx, c := context.WithTimeout(context.Background(), time.Second)
		defer c()
		s.Close(ctx)
		ts.Close()
	})
	return s, "ws" + strings.TrimPrefix(ts.URL, "http") + "/vta", g
}
func dial(t *testing.T, url string) *websocket.Conn {
	t.Helper()
	c, _, e := websocket.DefaultDialer.Dial(url, nil)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { c.Close() })
	return c
}
func receive(t *testing.T, c *websocket.Conn, typ string) map[string]any {
	t.Helper()
	c.SetReadDeadline(time.Now().Add(2 * time.Second))
	for {
		_, b, e := c.ReadMessage()
		if e != nil {
			t.Fatal(e)
		}
		var m map[string]any
		if e = json.Unmarshal(b, &m); e != nil {
			t.Fatal(e)
		}
		if m["type"] == typ {
			return m
		}
	}
}
func TestConnection(t *testing.T) {
	_, url, _ := setup(t, nil, nil)
	c := dial(t, url)
	h := receive(t, c, "hello")
	if len(h) != 6 {
		t.Fatal(h)
	}
	hb := receive(t, c, "heartbeat")
	if hb["seq"] != float64(1) || len(hb["connection_id"].(string)) != 36 {
		t.Fatal(hb)
	}
	for i := 0; i < 2; i++ {
		if e := c.WriteMessage(websocket.TextMessage, []byte(request)); e != nil {
			t.Fatal(e)
		}
		res := receive(t, c, "result")
		if res["status"] != "ok" {
			t.Fatal(res)
		}
	}
}

type blocked struct {
	entered chan struct{}
	release chan struct{}
}

func (b *blocked) Run(ctx context.Context, r protocol.Request, c denoise.Config) (protocol.Result, *protocol.Error) {
	select {
	case b.entered <- struct{}{}:
	default:
	}
	<-b.release
	return engine.New(denoise.NewRegistry()).Run(context.Background(), r, c)
}
func TestTimeoutHoldsSlot(t *testing.T) {
	b := &blocked{make(chan struct{}, 1), make(chan struct{})}
	defer close(b.release)
	_, url, g := setup(t, b, func(c *Config) { c.Limits.ProcessingTimeout = 80 * time.Millisecond })
	c := dial(t, url)
	receive(t, c, "hello")
	c.WriteMessage(websocket.TextMessage, []byte(request))
	<-b.entered
	receive(t, c, "heartbeat")
	er := receive(t, c, "error")
	if er["error"].(map[string]any)["code"] != "PROCESSING_TIMEOUT" {
		t.Fatal(er)
	}
	if g.Snapshot().State != "recovering" {
		t.Fatal(g.Snapshot())
	}
	other := dial(t, url)
	receive(t, other, "hello")
	other.WriteMessage(websocket.TextMessage, []byte(request))
	er = receive(t, other, "error")
	if er["error"].(map[string]any)["code"] != "BUSY" {
		t.Fatal(er)
	}
}
func TestConcurrentRequest(t *testing.T) {
	b := &blocked{make(chan struct{}, 1), make(chan struct{})}
	defer close(b.release)
	_, url, _ := setup(t, b, nil)
	c := dial(t, url)
	receive(t, c, "hello")
	c.WriteMessage(websocket.TextMessage, []byte(request))
	<-b.entered
	c.WriteMessage(websocket.TextMessage, []byte(request))
	expectClose(t, c, 1008)
}
func expectClose(t *testing.T, c *websocket.Conn, code int) {
	t.Helper()
	c.SetReadDeadline(time.Now().Add(2 * time.Second))
	for {
		_, _, e := c.ReadMessage()
		if e != nil {
			ce, ok := e.(*websocket.CloseError)
			if !ok || ce.Code != code {
				t.Fatalf("close got %v want %d", e, code)
			}
			return
		}
	}
}
func TestFrames(t *testing.T) {
	for _, tc := range []struct {
		name      string
		typ, code int
		body      []byte
	}{{"binary", websocket.BinaryMessage, 1003, []byte("x")}, {"utf8", websocket.TextMessage, 1007, []byte{255}}, {"large", websocket.TextMessage, 1009, make([]byte, 1025)}} {
		t.Run(tc.name, func(t *testing.T) {
			_, u, _ := setup(t, nil, func(c *Config) { c.Limits.MaxRequestBytes = 1024 })
			ws := dial(t, u)
			receive(t, ws, "hello")
			ws.WriteMessage(tc.typ, tc.body)
			expectClose(t, ws, tc.code)
		})
	}
}
func TestProtocolErrorKeepsConnection(t *testing.T) {
	_, u, _ := setup(t, nil, nil)
	c := dial(t, u)
	receive(t, c, "hello")
	c.WriteMessage(websocket.TextMessage, []byte(`{"version":1,"version":1}`))
	er := receive(t, c, "error")
	if er["error"].(map[string]any)["code"] != "INVALID_JSON" {
		t.Fatal(er)
	}
	c.WriteMessage(websocket.TextMessage, []byte(request))
	receive(t, c, "result")
}
func TestPongTimeout(t *testing.T) {
	_, u, _ := setup(t, nil, func(c *Config) { c.PongTimeout = 80 * time.Millisecond; c.PingInterval = 20 * time.Millisecond })
	c := dial(t, u)
	c.SetPingHandler(func(string) error { return nil })
	receive(t, c, "hello")
	expectClose(t, c, 4001)
}
func TestResponseBudget(t *testing.T) {
	_, u, _ := setup(t, nil, func(c *Config) { c.Limits.MaxResponseBytes = 64 })
	c := dial(t, u)
	receive(t, c, "hello")
	c.WriteMessage(websocket.TextMessage, []byte(request))
	er := receive(t, c, "error")
	if er["error"].(map[string]any)["code"] != "RESPONSE_TOO_LARGE" {
		t.Fatal(er)
	}
}
func TestMaxConnections(t *testing.T) {
	_, u, _ := setup(t, nil, func(c *Config) { c.Limits.MaxConnections = 1 })
	c := dial(t, u)
	receive(t, c, "hello")
	other, response, e := websocket.DefaultDialer.Dial(u, nil)
	if other != nil {
		other.Close()
	}
	if e == nil || response.StatusCode != 503 || response.Header.Get("Retry-After") == "" {
		t.Fatal(response, e)
	}
	response.Body.Close()
}
func TestUploadDeadline(t *testing.T) {
	_, u, _ := setup(t, nil, func(c *Config) { c.Limits.UploadTimeout = 50 * time.Millisecond })
	c := dial(t, u)
	receive(t, c, "hello")
	w, e := c.NextWriter(websocket.TextMessage)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = w.Write([]byte(strings.Repeat(" ", 16384))); e != nil {
		t.Fatal(e)
	}
	expectClose(t, c, 1008)
}
func TestFragmented(t *testing.T) {
	_, u, _ := setup(t, nil, nil)
	c := dial(t, u)
	receive(t, c, "hello")
	w, e := c.NextWriter(websocket.TextMessage)
	if e != nil {
		t.Fatal(e)
	}
	w.Write([]byte(strings.Repeat(" ", 16384)))
	w.Write([]byte(request))
	if e = w.Close(); e != nil {
		t.Fatal(e)
	}
	receive(t, c, "result")
}
func TestShutdown(t *testing.T) {
	s, u, _ := setup(t, nil, nil)
	c := dial(t, u)
	receive(t, c, "hello")
	done := make(chan error, 1)
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		done <- s.Close(ctx)
	}()
	expectClose(t, c, 1001)
	if e := <-done; e != nil {
		t.Fatal(e)
	}
}
func TestHeartbeatWhileProcessing(t *testing.T) {
	b := &blocked{make(chan struct{}, 1), make(chan struct{})}
	defer close(b.release)
	_, u, g := setup(t, b, nil)
	c := dial(t, u)
	receive(t, c, "hello")
	c.WriteMessage(websocket.TextMessage, []byte(request))
	<-b.entered
	for i := 0; i < 3; i++ {
		receive(t, c, "heartbeat")
	}
	if g.Snapshot().ActiveRequests != 1 {
		t.Fatal(g.Snapshot())
	}
	other := dial(t, u)
	receive(t, other, "hello")
	other.WriteMessage(websocket.TextMessage, []byte(request))
	e := receive(t, other, "error")
	if e["error"].(map[string]any)["code"] != "BUSY" {
		t.Fatal(e)
	}
	c.Close()
	deadline := time.Now().Add(time.Second)
	for g.Snapshot().State != "recovering" && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if g.Snapshot().State != "recovering" {
		t.Fatal(g.Snapshot())
	}
}

type hugeResult struct{}

func (hugeResult) Run(ctx context.Context, req protocol.Request, cfg denoise.Config) (protocol.Result, *protocol.Error) {
	rows := make([][]float64, 500000)
	for i := range rows {
		rows[i] = []float64{1.2345678901234567e-100}
	}
	return protocol.Result{Version: 1, Type: "result", RequestID: req.RequestID, Data: protocol.Data{Intensity: rows}}, nil
}
func TestSlowConsumerIsolation(t *testing.T) {
	s, u, _ := setup(t, hugeResult{}, func(c *Config) { c.Limits.WriteTimeout = 40 * time.Millisecond; c.CloseTimeout = 40 * time.Millisecond })
	slow := dial(t, u)
	receive(t, slow, "hello")
	slow.WriteMessage(websocket.TextMessage, []byte(request))
	monitor := dial(t, u)
	receive(t, monitor, "hello")
	for i := 0; i < 5; i++ {
		receive(t, monitor, "heartbeat")
	}
	slow.Close()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		s.mu.Lock()
		count := len(s.connections)
		s.mu.Unlock()
		if count == 1 {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("slow connection did not release resources")
}
func TestCancellationCompletionRace(t *testing.T) {
	for i := 0; i < 15; i++ {
		b := &blocked{make(chan struct{}, 1), make(chan struct{})}
		s, u, g := setup(t, b, func(c *Config) { c.CloseTimeout = 30 * time.Millisecond })
		c := dial(t, u)
		receive(t, c, "hello")
		c.WriteMessage(websocket.TextMessage, []byte(request))
		<-b.entered
		go c.Close()
		close(b.release)
		deadline := time.Now().Add(time.Second)
		for g.Snapshot().ActiveRequests != 0 && time.Now().Before(deadline) {
			time.Sleep(time.Millisecond)
		}
		if g.Snapshot().ActiveRequests != 0 {
			t.Fatal("lease leaked")
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		s.Close(ctx)
		cancel()
	}
}
