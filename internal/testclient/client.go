package testclient

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/gorilla/websocket"
	"math/rand/v2"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
	"vta-fpga/internal/protocol"
)

type Config struct {
	Random                                                                              func() float64
	Now                                                                                 func() time.Time
	ConnectTimeout, HelloTimeout, CloseTimeout, PingInterval, PongTimeout, StaleTimeout time.Duration
}
type outcome struct {
	result protocol.Result
	err    *protocol.Error
}
type pending struct {
	id   string
	done chan outcome
}
type Client struct {
	cfg       Config
	mu        sync.Mutex
	policy    Policy
	conn      *websocket.Conn
	hello     protocol.Hello
	heartbeat *protocol.Heartbeat
	received  time.Time
	pending   *pending
	unknown   bool
	running   bool
	cancel    context.CancelFunc
	done      chan struct{}
	state     string
}

func New(c Config) *Client {
	if c.Random == nil {
		c.Random = rand.Float64
	}
	if c.Now == nil {
		c.Now = time.Now
	}
	for _, p := range []struct {
		ptr   *time.Duration
		value time.Duration
	}{{&c.ConnectTimeout, 5 * time.Second}, {&c.HelloTimeout, 15 * time.Second}, {&c.CloseTimeout, 3 * time.Second}, {&c.PingInterval, 20 * time.Second}, {&c.PongTimeout, 60 * time.Second}, {&c.StaleTimeout, 15 * time.Second}} {
		if *p.ptr <= 0 {
			*p.ptr = p.value
		}
	}
	return &Client{cfg: c, state: "DISCONNECTED"}
}
func validateHello(b []byte) (h protocol.Hello, err error) {
	v, e := protocol.StrictJSON(context.Background(), b)
	if e != nil {
		return h, e
	}
	m, ok := v.(map[string]any)
	if !ok {
		return h, fmt.Errorf("expected hello object")
	}
	for _, k := range []string{"version", "type", "device", "cpu", "memory", "functions"} {
		if _, ok := m[k]; !ok {
			return h, fmt.Errorf("missing hello.%s", k)
		}
	}
	if e = json.Unmarshal(b, &h); e != nil || h.Version != 1 || h.Type != "hello" {
		return h, fmt.Errorf("incompatible hello")
	}
	check := func(obj any, fields ...string) bool {
		o, ok := obj.(map[string]any)
		if !ok {
			return false
		}
		for _, k := range fields {
			if _, ok := o[k]; !ok {
				return false
			}
		}
		return true
	}
	if !check(m["device"], "id", "model", "os") || !check(m["cpu"], "model", "logical_cores") || !check(m["memory"], "total_bytes") {
		return h, fmt.Errorf("missing device summary")
	}
	dm := m["device"].(map[string]any)
	if !check(dm["os"], "name", "distribution", "version", "kernel", "arch") {
		return h, fmt.Errorf("missing OS summary")
	}
	if h.CPU.LogicalCores != nil && *h.CPU.LogicalCores <= 0 || h.Memory.TotalBytes != nil && *h.Memory.TotalBytes == 0 {
		return h, fmt.Errorf("invalid hardware summary")
	}
	if h.Functions == nil {
		return h, fmt.Errorf("missing functions")
	}
	for k, ops := range h.Functions {
		if k == "" || len(ops) == 0 {
			return h, fmt.Errorf("empty capability")
		}
		seen := map[string]bool{}
		for _, op := range ops {
			if op == "" || seen[op] {
				return h, fmt.Errorf("duplicate or empty operator")
			}
			seen[op] = true
		}
	}
	return h, nil
}
func (c *Client) Connect(ctx context.Context, u string) error {
	c.mu.Lock()
	if c.running {
		c.mu.Unlock()
		return fmt.Errorf("client already running")
	}
	runCtx, cancel := context.WithCancel(ctx)
	c.cancel = cancel
	c.done = make(chan struct{})
	c.running = true
	c.policy.Desired = true
	c.mu.Unlock()
	ready := make(chan error, 1)
	go c.manage(runCtx, u, ready)
	select {
	case e := <-ready:
		return e
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (c *Client) manage(ctx context.Context, u string, ready chan error) {
	defer func() { c.mu.Lock(); c.running = false; c.state = "DISCONNECTED"; close(c.done); c.mu.Unlock() }()
	notified := false
	notify := func(e error) {
		if !notified {
			ready <- e
			notified = true
		}
	}
	k := 0
	for {
		if ctx.Err() != nil {
			notify(ctx.Err())
			return
		}
		c.mu.Lock()
		if !c.policy.Desired {
			c.mu.Unlock()
			notify(context.Canceled)
			return
		}
		generation := c.policy.Begin()
		c.state = "CONNECTING"
		c.heartbeat = nil
		c.received = time.Time{}
		c.mu.Unlock()
		dialer := websocket.Dialer{HandshakeTimeout: c.cfg.ConnectTimeout}
		ws, response, e := dialer.DialContext(ctx, u, nil)
		retryAfter := time.Duration(0)
		retry := false
		code := 1006
		if e != nil {
			retry = response == nil
			if response != nil {
				retry = RetryHTTP(response.StatusCode)
				retryAfter = parseRetryAfter(response, c.cfg.Now())
				response.Body.Close()
			}
			c.mu.Lock()
			c.policy.Disconnected(generation, 1006)
			c.mu.Unlock()
		} else {
			ws.SetReadLimit(protocol.DefaultLimits().MaxResponseBytes)
			c.mu.Lock()
			c.conn = ws
			c.state = "WAIT_HELLO"
			c.mu.Unlock()
			socketDone := make(chan struct{})
			go func() {
				select {
				case <-ctx.Done():
					ws.Close()
				case <-socketDone:
				}
			}()
			ws.SetReadDeadline(time.Now().Add(c.cfg.HelloTimeout))
			typ, b, readErr := ws.ReadMessage()
			h, helloErr := validateHello(b)
			if readErr != nil {
				e = readErr
				code = 4000
				retry = true
			} else if typ != websocket.TextMessage || helloErr != nil {
				e = fmt.Errorf("incompatible hello: %v", helloErr)
				code = 1000
				retry = false
				ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1000, "INCOMPATIBLE_HELLO"), time.Now().Add(c.cfg.CloseTimeout))
			} else {
				c.mu.Lock()
				stopped := !c.policy.Desired || ctx.Err() != nil
				if !stopped {
					c.hello = h
					c.state = "READY"
				}
				c.mu.Unlock()
				if stopped {
					e = context.Canceled
					code = 1000
					retry = false
				} else {
					notify(nil)
					connected := c.cfg.Now()
					code, e = c.readConnection(ctx, ws, generation)
					if c.cfg.Now().Sub(connected) >= 30*time.Second {
						k = 0
					}
					retry = RetryClose(code)
				}
			}
			close(socketDone)
			ws.Close()
			c.mu.Lock()
			if c.policy.Generation == generation {
				if c.conn == ws {
					c.conn = nil
				}
				if p := c.pending; p != nil {
					c.pending = nil
					c.unknown = true
					p.done <- outcome{err: protocol.Failure(p.id, "RESULT_UNKNOWN", "", "connection ended before a complete response")}
				}
			}
			retry = c.policy.Disconnected(generation, code) && retry
			c.mu.Unlock()
		}
		c.mu.Lock()
		desired := c.policy.Desired
		c.state = "RECONNECT_WAIT"
		c.mu.Unlock()
		if !retry || !desired {
			notify(e)
			return
		}
		delay := max(Backoff(k, c.cfg.Random()), retryAfter)
		k = min(k+1, 6)
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			notify(ctx.Err())
			return
		case <-timer.C:
		}
	}
}
func parseRetryAfter(r *http.Response, now time.Time) time.Duration {
	s := r.Header.Get("Retry-After")
	if n, e := strconv.ParseInt(s, 10, 64); e == nil && n >= 0 && n <= 86400 {
		return time.Duration(n) * time.Second
	}
	if t, e := http.ParseTime(s); e == nil {
		return max(0, t.Sub(now))
	}
	return 0
}
func (c *Client) readConnection(ctx context.Context, ws *websocket.Conn, generation uint64) (int, error) {
	alive, cancel := context.WithCancel(ctx)
	defer cancel()
	ws.SetReadDeadline(time.Now().Add(c.cfg.PongTimeout))
	ws.SetPongHandler(func(string) error { return ws.SetReadDeadline(time.Now().Add(c.cfg.PongTimeout)) })
	go func() {
		tick := time.NewTicker(c.cfg.PingInterval)
		defer tick.Stop()
		for {
			select {
			case <-alive.Done():
				return
			case <-tick.C:
				if ws.WriteControl(websocket.PingMessage, nil, time.Now().Add(c.cfg.CloseTimeout)) != nil {
					ws.Close()
					return
				}
			}
		}
	}()
	bad := func(reason string) (int, error) {
		ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1000, reason), time.Now().Add(c.cfg.CloseTimeout))
		return 1000, errors.New(reason)
	}
	for {
		typ, b, e := ws.ReadMessage()
		if e != nil {
			var closeErr *websocket.CloseError
			if errors.As(e, &closeErr) {
				return closeErr.Code, e
			}
			return 1006, e
		}
		if typ != websocket.TextMessage {
			return bad("TEXT_REQUIRED")
		}
		if _, e = protocol.StrictJSON(context.Background(), b); e != nil {
			return bad("INVALID_JSON")
		}
		var env struct {
			Version   int    `json:"version"`
			Type      string `json:"type"`
			RequestID string `json:"request_id"`
		}
		if json.Unmarshal(b, &env) != nil || env.Version != 1 {
			return bad("INVALID_ENVELOPE")
		}
		switch env.Type {
		case "heartbeat":
			var h protocol.Heartbeat
			if json.Unmarshal(b, &h) != nil || h.Seq == 0 || !validUUID(h.ConnectionID) || h.SampleAgeMS < 0 {
				return bad("INVALID_HEARTBEAT")
			}
			if _, e = time.Parse(time.RFC3339Nano, h.Timestamp); e != nil {
				return bad("INVALID_HEARTBEAT")
			}
			c.mu.Lock()
			if generation != c.policy.Generation {
				c.mu.Unlock()
				continue
			}
			if c.heartbeat != nil && c.heartbeat.ConnectionID != h.ConnectionID {
				c.mu.Unlock()
				return bad("CONNECTION_ID_CHANGED")
			}
			if c.heartbeat == nil || h.Seq > c.heartbeat.Seq {
				c.heartbeat = &h
				c.received = c.cfg.Now()
			}
			c.mu.Unlock()
		case "result", "error":
			var o outcome
			if env.Type == "result" {
				if json.Unmarshal(b, &o.result) != nil {
					return bad("INVALID_RESULT")
				}
			} else {
				var er protocol.Error
				if json.Unmarshal(b, &er) != nil {
					return bad("INVALID_ERROR")
				}
				o.err = &er
			}
			c.mu.Lock()
			p := c.pending
			if generation == c.policy.Generation && p != nil && p.id == env.RequestID {
				c.pending = nil
				c.state = "READY"
				p.done <- o
			}
			c.mu.Unlock()
		default:
			return bad("UNEXPECTED_MESSAGE")
		}
	}
}
func validUUID(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, ch := range s {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			if ch != '-' {
				return false
			}
		} else if !strings.ContainsRune("0123456789abcdefABCDEF", ch) {
			return false
		}
	}
	return true
}
func (c *Client) Denoise(ctx context.Context, r protocol.Request) (protocol.Result, *protocol.Error) {
	c.mu.Lock()
	if c.conn == nil || c.state != "READY" || c.pending != nil || c.unknown {
		c.mu.Unlock()
		return protocol.Result{}, protocol.Failure(r.RequestID, "CLIENT_NOT_READY", "", "wait for hello and resolve any unknown previous result")
	}
	ws := c.conn
	p := &pending{id: r.RequestID, done: make(chan outcome, 1)}
	c.pending = p
	c.state = "PROCESSING"
	c.mu.Unlock()
	b, e := json.Marshal(r)
	if e == nil {
		deadline := time.Now().Add(10 * time.Second)
		if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
			deadline = d
		}
		ws.SetWriteDeadline(deadline)
		e = ws.WriteMessage(websocket.TextMessage, b)
	}
	if e != nil {
		ws.Close()
	}
	select {
	case o := <-p.done:
		return o.result, o.err
	case <-ctx.Done():
		ws.Close()
		c.mu.Lock()
		if c.pending == p {
			c.pending = nil
			c.unknown = true
		}
		c.mu.Unlock()
		return protocol.Result{}, protocol.Failure(r.RequestID, "RESULT_UNKNOWN", "", "request wait expired; original data must be retained")
	}
}

// ResolveUnknown is an explicit upper-layer decision to retry with a new ID or discard.
func (c *Client) ResolveUnknown() { c.mu.Lock(); defer c.mu.Unlock(); c.unknown = false }
func (c *Client) Status() (string, *protocol.Heartbeat, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.heartbeat == nil {
		return c.state, nil, false
	}
	b, _ := json.Marshal(c.heartbeat)
	var h protocol.Heartbeat
	json.Unmarshal(b, &h)
	fresh := c.cfg.Now().Sub(c.received)+time.Duration(h.SampleAgeMS)*time.Millisecond < c.cfg.StaleTimeout
	return c.state, &h, fresh
}
func (c *Client) Close(ctx context.Context) error {
	c.mu.Lock()
	c.policy.Stop()
	ws, done, cancel := c.conn, c.done, c.cancel
	c.mu.Unlock()
	if cancel != nil {
		defer cancel()
	}
	if done == nil {
		return nil
	}
	deadline := time.Now().Add(c.cfg.CloseTimeout)
	if ws != nil {
		_ = ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1000, "CLIENT_CLOSE"), deadline)
	} else if cancel != nil {
		cancel()
	}
	timer := time.NewTimer(time.Until(deadline))
	defer timer.Stop()
	select {
	case <-done:
		return nil
	case <-timer.C:
		if cancel != nil {
			cancel()
		}
		if ws != nil {
			ws.Close()
		}
		select {
		case <-done:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	case <-ctx.Done():
		if ws != nil {
			ws.Close()
		}
		return ctx.Err()
	}
}
