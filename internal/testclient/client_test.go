package testclient

import (
	"context"
	"encoding/json"
	"github.com/gorilla/websocket"
	"net/http"
	"net/http/httptest"
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
