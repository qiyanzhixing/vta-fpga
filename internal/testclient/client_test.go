package testclient

import (
	"context"
	"encoding/json"
	"github.com/gorilla/websocket"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"vta-fpga/internal/protocol"
)

func TestWaitsForHello(t *testing.T) {
	c := New(Config{})
	_, e := c.Denoise(context.Background(), protocol.Request{RequestID: "x"})
	if e == nil {
		t.Fatal("request before hello")
	}
}
func TestHelloValidation(t *testing.T) {
	for _, s := range []string{`{"version":1,"type":"hello"}`, `{"version":2,"type":"hello","device":{},"cpu":{},"memory":{},"functions":{}}`} {
		if _, e := validateHello([]byte(s)); e == nil {
			t.Fatal(s)
		}
	}
}
func TestHeartbeatDoesNotExtendTimeout(t *testing.T) {
	var attempts atomic.Int32
	up := websocket.Upgrader{}
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, e := up.Upgrade(w, r, nil)
		if e != nil {
			return
		}
		attempts.Add(1)
		defer ws.Close()
		ws.WriteJSON(protocol.Hello{Version: 1, Type: "hello", Functions: map[string][]string{"denoise": {"none"}}})
		ticker := time.NewTicker(5 * time.Millisecond)
		defer ticker.Stop()
		for seq := uint64(1); seq <= 100; seq++ {
			<-ticker.C
			h := protocol.Heartbeat{Version: 1, Type: "heartbeat", ConnectionID: "00000000-0000-4000-8000-000000000001", Seq: seq, Timestamp: time.Now().UTC().Format(time.RFC3339), CollectionErrors: []protocol.CollectionError{}}
			if ws.WriteJSON(h) != nil {
				return
			}
		}
	}))
	defer ts.Close()
	c := New(Config{Random: func() float64 { return 1 }})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if e := c.Connect(ctx, "ws"+strings.TrimPrefix(ts.URL, "http")); e != nil {
		t.Fatal(e)
	}
	wait, cancelWait := context.WithTimeout(ctx, 40*time.Millisecond)
	defer cancelWait()
	start := time.Now()
	_, e := c.Denoise(wait, protocol.Request{Version: 1, Type: "denoise", RequestID: "x", Config: protocol.Config{Method: "none", Params: json.RawMessage(`{}`)}})
	if e == nil || time.Since(start) > 300*time.Millisecond {
		t.Fatal(e, time.Since(start))
	}
	c.Close(ctx)
	time.Sleep(30 * time.Millisecond)
	if attempts.Load() != 1 {
		t.Fatal("manual close reconnected")
	}
}
func TestReconnectDoesNotReplay(t *testing.T) {
	var connects, requests atomic.Int32
	up := websocket.Upgrader{}
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, e := up.Upgrade(w, r, nil)
		if e != nil {
			return
		}
		defer ws.Close()
		n := connects.Add(1)
		ws.WriteJSON(protocol.Hello{Version: 1, Type: "hello", Functions: map[string][]string{"denoise": {"none"}}})
		for {
			_, b, e := ws.ReadMessage()
			if e != nil {
				return
			}
			requests.Add(1)
			if n == 1 {
				return
			}
			var req protocol.Request
			json.Unmarshal(b, &req)
			ws.WriteJSON(protocol.Result{Version: 1, Type: "result", RequestID: req.RequestID, Status: "ok", Data: req.Data})
		}
	}))
	defer ts.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	c := New(Config{Random: func() float64 { return 0 }})
	if e := c.Connect(ctx, "ws"+strings.TrimPrefix(ts.URL, "http")); e != nil {
		t.Fatal(e)
	}
	defer c.Close(ctx)
	req := protocol.Request{Version: 1, Type: "denoise", RequestID: "first"}
	_, e := c.Denoise(ctx, req)
	if e == nil || e.Detail.Code != "RESULT_UNKNOWN" {
		t.Fatal(e)
	}
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		state, _, _ := c.Status()
		if connects.Load() == 2 && state == "READY" {
			break
		}
		time.Sleep(time.Millisecond)
	}
	if connects.Load() != 2 {
		t.Fatal("did not reconnect")
	}
	if _, e = c.Denoise(ctx, req); e == nil || e.Detail.Code != "CLIENT_NOT_READY" {
		t.Fatal("unknown result allowed next request", e)
	}
	if requests.Load() != 1 {
		t.Fatal("automatic replay")
	}
	c.ResolveUnknown()
	req.RequestID = "retry-new-id"
	res, e := c.Denoise(ctx, req)
	if e != nil || res.RequestID != req.RequestID || requests.Load() != 2 {
		t.Fatal(res, e)
	}
}
func TestFreshness(t *testing.T) {
	now := time.Now()
	c := New(Config{Now: func() time.Time { return now }})
	c.received = now
	c.heartbeat = &protocol.Heartbeat{Seq: 2, SampleAgeMS: 14000}
	if _, _, fresh := c.Status(); !fresh {
		t.Fatal("prematurely stale")
	}
	now = now.Add(time.Second)
	if _, _, fresh := c.Status(); fresh {
		t.Fatal("stale heartbeat appeared current")
	}
}
func TestCloseDuringHello(t *testing.T) {
	accepted, release := make(chan struct{}), make(chan struct{})
	up := websocket.Upgrader{}
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, e := up.Upgrade(w, r, nil)
		if e != nil {
			return
		}
		defer ws.Close()
		close(accepted)
		<-release
		ws.WriteJSON(protocol.Hello{Version: 1, Type: "hello", Functions: map[string][]string{"denoise": {"none"}}})
	}))
	defer ts.Close()
	defer close(release)
	c := New(Config{HelloTimeout: time.Second, CloseTimeout: 20 * time.Millisecond})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	connected := make(chan error, 1)
	go func() { connected <- c.Connect(ctx, "ws"+strings.TrimPrefix(ts.URL, "http")) }()
	<-accepted
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		state, _, _ := c.Status()
		if state == "WAIT_HELLO" {
			break
		}
		time.Sleep(time.Millisecond)
	}
	c.Close(context.Background())
	select {
	case e := <-connected:
		if e == nil {
			t.Fatal("READY after Close")
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("hello reader remained blocked after Close")
	}
	select {
	case <-c.done:
	case <-time.After(100 * time.Millisecond):
		t.Fatal("manager did not stop")
	}
}
func TestContextCancelsSocket(t *testing.T) {
	for _, pendingRequest := range []bool{false, true} {
		t.Run(strconv.FormatBool(pendingRequest), func(t *testing.T) {
			up := websocket.Upgrader{}
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				ws, e := up.Upgrade(w, r, nil)
				if e != nil {
					return
				}
				defer ws.Close()
				ws.WriteJSON(protocol.Hello{Version: 1, Type: "hello", Functions: map[string][]string{"denoise": {"none"}}})
				for {
					if _, _, e := ws.ReadMessage(); e != nil {
						return
					}
				}
			}))
			defer ts.Close()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			c := New(Config{PongTimeout: time.Second, CloseTimeout: 20 * time.Millisecond})
			if e := c.Connect(ctx, "ws"+strings.TrimPrefix(ts.URL, "http")); e != nil {
				t.Fatal(e)
			}
			if pendingRequest {
				go c.Denoise(context.Background(), protocol.Request{Version: 1, Type: "denoise", RequestID: "cancel"})
				deadline := time.Now().Add(time.Second)
				for time.Now().Before(deadline) {
					c.mu.Lock()
					p := c.pending != nil
					c.mu.Unlock()
					if p {
						break
					}
					time.Sleep(time.Millisecond)
				}
			}
			cancel()
			select {
			case <-c.done:
			case <-time.After(100 * time.Millisecond):
				t.Fatal("context cancellation did not close socket")
			}
		})
	}
}
