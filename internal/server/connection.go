package server

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"github.com/gorilla/websocket"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
	"vta-fpga/internal/engine"
	"vta-fpga/internal/protocol"
)

type terminal struct {
	body []byte
	job  *job
}
type job struct {
	ctx    context.Context
	cancel context.CancelFunc
	mu     sync.Mutex
	id     string
	lease  *engine.Lease
}

func (j *job) cancelWork() {
	j.cancel()
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.lease != nil {
		j.lease.MarkRecovering()
	}
}
func (j *job) requestID() string { j.mu.Lock(); defer j.mu.Unlock(); return j.id }

type connection struct {
	s              *Server
	ws             *websocket.Conn
	id             string
	ctx            context.Context
	cancel         context.CancelFunc
	done           chan struct{}
	once           sync.Once
	ready          atomic.Bool
	closing        atomic.Bool
	started        time.Time
	lastPong       atomic.Int64
	mu             sync.Mutex
	publication    sync.RWMutex
	pending        *job
	out            chan terminal
	uploadDeadline time.Time
}

func newConnection(s *Server, ws *websocket.Conn) (*connection, error) {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		return nil, e
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	id := fmt.Sprintf("%x-%x-%x-%x-%x", b[:4], b[4:6], b[6:8], b[8:10], b[10:])
	ctx, cancel := context.WithCancel(context.Background())
	return &connection{s: s, ws: ws, id: id, ctx: ctx, cancel: cancel, done: make(chan struct{}), started: time.Now(), out: make(chan terminal, 1)}, nil
}
func (c *connection) close(code int, reason string) {
	c.once.Do(func() {
		c.closing.Store(true)
		c.cancel()
		c.mu.Lock()
		j := c.pending
		c.mu.Unlock()
		if j != nil {
			j.cancelWork()
		}
		deadline := time.Now().Add(c.s.cfg.CloseTimeout)
		go func() {
			timer := time.NewTimer(time.Until(deadline))
			defer timer.Stop()
			select {
			case <-c.done:
			case <-timer.C:
			}
			c.ws.Close()
		}()
		_ = c.ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(code, reason), deadline)
	})
}
func (c *connection) readDeadline() {
	deadline := c.started.Add(time.Duration(c.lastPong.Load())).Add(c.s.cfg.PongTimeout)
	if !c.uploadDeadline.IsZero() && c.uploadDeadline.Before(deadline) {
		deadline = c.uploadDeadline
	}
	_ = c.ws.SetReadDeadline(deadline)
}
func (c *connection) serve() {
	defer close(c.done)
	defer c.ws.Close()
	defer c.cancel()
	defer func() {
		c.mu.Lock()
		j := c.pending
		c.mu.Unlock()
		if j != nil {
			j.cancelWork()
		}
	}()
	c.ws.SetReadLimit(c.s.cfg.Limits.MaxRequestBytes)
	c.ws.SetPongHandler(func(string) error { c.lastPong.Store(int64(time.Since(c.started))); c.readDeadline(); return nil })
	c.ws.SetCloseHandler(func(code int, text string) error { c.close(code, ""); return nil })
	c.readDeadline()
	writerDone := make(chan struct{})
	go func() { defer close(writerDone); c.writeLoop() }()
	defer func() {
		c.cancel()
		if c.closing.Load() {
			_ = c.ws.SetReadDeadline(time.Now().Add(c.s.cfg.CloseTimeout))
			for {
				_, _, err := c.ws.NextReader()
				if err != nil {
					break
				}
			}
		}
		c.ws.Close()
		<-writerDone
	}()
	go c.pingLoop()
	for {
		typ, reader, e := c.ws.NextReader()
		if e != nil {
			c.readFailure(e)
			return
		}
		if c.ctx.Err() != nil {
			return
		}
		if typ != websocket.TextMessage {
			c.close(1003, "TEXT_REQUIRED")
			<-c.ctx.Done()
			return
		}
		// The peer may already have received the final bytes while WriteMessage
		// has not returned. Observe publication together with write completion.
		c.publication.RLock()
		ready := c.ready.Load()
		c.mu.Lock()
		busy := c.pending != nil
		c.mu.Unlock()
		c.publication.RUnlock()
		if c.ctx.Err() != nil {
			return
		}
		if !ready {
			c.close(1008, "HELLO_REQUIRED")
			return
		}
		if busy {
			c.close(1008, "ONE_REQUEST_AT_A_TIME")
			return
		}
		c.uploadDeadline = time.Now().Add(c.s.cfg.Limits.UploadTimeout)
		c.readDeadline()
		raw, e := io.ReadAll(io.LimitReader(reader, c.s.cfg.Limits.MaxRequestBytes+1))
		c.uploadDeadline = time.Time{}
		c.readDeadline()
		if e != nil {
			c.readFailure(e)
			return
		}
		if int64(len(raw)) > c.s.cfg.Limits.MaxRequestBytes {
			c.close(1009, "REQUEST_TOO_LARGE")
			return
		}
		if !utf8.Valid(raw) {
			c.close(1007, "INVALID_UTF8")
			return
		}
		ctx, cancel := context.WithTimeout(c.ctx, c.s.cfg.Limits.ProcessingTimeout)
		j := &job{ctx: ctx, cancel: cancel}
		c.mu.Lock()
		c.pending = j
		c.mu.Unlock()
		c.s.wg.Add(1)
		go func() { defer c.s.wg.Done(); c.process(j, raw) }()
	}
}
func (c *connection) readFailure(e error) {
	if c.ctx.Err() != nil {
		return
	}
	if ne, ok := e.(net.Error); ok && ne.Timeout() {
		if time.Since(c.started)-time.Duration(c.lastPong.Load()) >= c.s.cfg.PongTimeout {
			c.close(4001, "HEARTBEAT_TIMEOUT")
		} else {
			c.close(1008, "UPLOAD_TIMEOUT")
		}
	} else if e == websocket.ErrReadLimit {
		c.close(1009, "REQUEST_TOO_LARGE")
	} else {
		c.cancel()
	}
}
func (c *connection) process(j *job, raw []byte) {
	defer j.cancel()
	result := make(chan []byte, 1)
	c.s.wg.Add(1)
	go func() { defer c.s.wg.Done(); result <- c.execute(j, raw) }()
	var body []byte
	select {
	case body = <-result:
		if j.ctx.Err() != nil {
			body = nil
		}
	case <-j.ctx.Done():
	}
	if body == nil {
		j.cancelWork()
		if c.ctx.Err() != nil {
			return
		}
		body, _ = json.Marshal(protocol.Failure(j.requestID(), "PROCESSING_TIMEOUT", "", "processing deadline exceeded"))
	}
	select {
	case c.out <- terminal{body, j}:
	case <-c.ctx.Done():
	}
}
func (c *connection) execute(j *job, raw []byte) (body []byte) {
	var lease *engine.Lease
	defer func() {
		if lease != nil {
			lease.Release()
		}
		if recover() != nil {
			body, _ = json.Marshal(protocol.Failure(j.requestID(), "INTERNAL_ERROR", "", "request execution failed"))
		}
	}()
	encodeError := func(e *protocol.Error) []byte { b, _ := json.Marshal(e); return b }
	req, e := protocol.DecodeRequest(j.ctx, raw, c.s.cfg.Limits)
	j.mu.Lock()
	j.id = req.RequestID
	j.mu.Unlock()
	if e != nil {
		return encodeError(e)
	}
	cfg, e := c.s.reg.Parse(req, c.s.cfg.Limits.MaxWindowSize)
	if e != nil {
		return encodeError(e)
	}
	if j.ctx.Err() != nil {
		return nil
	}
	var ok bool
	lease, ok = c.s.gate.TryAcquire()
	if !ok {
		return encodeError(protocol.Failure(req.RequestID, "BUSY", "", "device is processing another request"))
	}
	j.mu.Lock()
	j.lease = lease
	if j.ctx.Err() != nil {
		lease.MarkRecovering()
	}
	j.mu.Unlock()
	if j.ctx.Err() != nil {
		return nil
	}
	res, e := c.s.runner.Run(j.ctx, req, cfg)
	if e != nil {
		return encodeError(e)
	}
	if j.ctx.Err() != nil {
		return nil
	}
	body, err := json.Marshal(res)
	if err != nil {
		return encodeError(protocol.Failure(req.RequestID, "INTERNAL_ERROR", "", "result serialization failed"))
	}
	if int64(len(body)) > c.s.cfg.Limits.MaxResponseBytes {
		return encodeError(protocol.Failure(req.RequestID, "RESPONSE_TOO_LARGE", "", "encoded result exceeds response budget"))
	}
	if j.ctx.Err() != nil {
		return nil
	}
	return body
}
