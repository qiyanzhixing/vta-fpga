package server

import (
	"encoding/json"
	"github.com/gorilla/websocket"
	"time"
	"vta-fpga/internal/protocol"
)

func (c *connection) write(body []byte) bool {
	if c.ctx.Err() != nil {
		return false
	}
	c.ws.SetWriteDeadline(time.Now().Add(c.s.cfg.Limits.WriteTimeout))
	if e := c.ws.WriteMessage(websocket.TextMessage, body); e != nil {
		c.close(1011, "WRITE_FAILED")
		return false
	}
	return true
}
func (c *connection) writeLoop() {
	hello := protocol.Hello{Version: 1, Type: "hello", DeviceSummary: c.s.metrics.Summary(), Functions: map[string][]string{"denoise": c.s.reg.Methods()}}
	body, e := json.Marshal(hello)
	if e != nil || !c.write(body) {
		return
	}
	c.ready.Store(true)
	seq := uint64(0)
	heartbeat := func() bool {
		h := c.s.metrics.Snapshot(c.s.gate.Snapshot())
		h.ConnectionID = c.id
		h.Seq = seq + 1
		b, e := json.Marshal(h)
		if e != nil || len(b) > 8192 {
			c.close(1011, "HEARTBEAT_ENCODING_FAILED")
			return false
		}
		if !c.write(b) {
			return false
		}
		seq++
		return true
	}
	if !heartbeat() {
		return
	}
	ticker := time.NewTicker(c.s.cfg.HeartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case <-c.ctx.Done():
			return
		case t := <-c.out:
			if !c.write(t.body) {
				return
			}
			c.mu.Lock()
			if c.pending == t.job {
				c.pending = nil
			}
			c.mu.Unlock() // Any delayed heartbeat uses a fresh snapshot after this complete message.
			select {
			case <-ticker.C:
				if !heartbeat() {
					return
				}
			default:
			}
		case <-ticker.C:
			if !heartbeat() {
				return
			}
		}
	}
}
func (c *connection) pingLoop() {
	ticker := time.NewTicker(c.s.cfg.PingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-c.ctx.Done():
			return
		case <-ticker.C:
			if time.Since(c.started)-time.Duration(c.lastPong.Load()) >= c.s.cfg.PongTimeout {
				c.close(4001, "HEARTBEAT_TIMEOUT")
				return
			}
			if e := c.ws.WriteControl(websocket.PingMessage, nil, time.Now().Add(c.s.cfg.Limits.WriteTimeout)); e != nil {
				c.close(1011, "PING_FAILED")
				return
			}
		}
	}
}
