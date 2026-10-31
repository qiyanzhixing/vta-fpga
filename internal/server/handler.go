// Package server owns connection lifetimes; algorithms never perform socket I/O.
package server

import (
	"context"
	"github.com/gorilla/websocket"
	"net/http"
	"sync"
	"time"
	"vta-fpga/internal/denoise"
	"vta-fpga/internal/engine"
	"vta-fpga/internal/protocol"
	"vta-fpga/internal/telemetry"
)

type Runner interface {
	Run(context.Context, protocol.Request, denoise.Config) (protocol.Result, *protocol.Error)
}
type Config struct {
	Limits                                                     protocol.Limits
	HeartbeatInterval, PingInterval, PongTimeout, CloseTimeout time.Duration
}

func DefaultConfig() Config {
	l := protocol.DefaultLimits()
	return Config{l, l.ReportInterval, l.PingInterval, l.PongTimeout, 3 * time.Second}
}

type Server struct {
	cfg         Config
	reg         *denoise.Registry
	runner      Runner
	gate        *engine.Gate
	metrics     *telemetry.Sampler
	mu          sync.Mutex
	connections map[*connection]bool
	reserved    int
	closing     bool
	wg          sync.WaitGroup
}

func New(cfg Config, reg *denoise.Registry, run Runner, gate *engine.Gate, metrics *telemetry.Sampler) *Server {
	return &Server{cfg: cfg, reg: reg, runner: run, gate: gate, metrics: metrics, connections: map[*connection]bool{}}
}
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/vta", s.handle)
	return mux
}
func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/vta" {
		http.NotFound(w, r)
		return
	}
	s.mu.Lock()
	if s.closing || s.reserved >= s.cfg.Limits.MaxConnections {
		s.mu.Unlock()
		w.Header().Set("Retry-After", "1")
		http.Error(w, "service unavailable", 503)
		return
	}
	s.reserved++
	s.wg.Add(1)
	s.mu.Unlock()
	defer s.wg.Done()
	defer func() { s.mu.Lock(); s.reserved--; s.mu.Unlock() }()
	up := websocket.Upgrader{HandshakeTimeout: 5 * time.Second, ReadBufferSize: 4096, WriteBufferSize: 4096}
	ws, e := up.Upgrade(w, r, nil)
	if e != nil {
		return
	}
	c, e := newConnection(s, ws)
	if e != nil {
		ws.Close()
		return
	}
	s.mu.Lock()
	s.connections[c] = true
	closing := s.closing
	s.mu.Unlock()
	defer func() { s.mu.Lock(); delete(s.connections, c); s.mu.Unlock() }()
	if closing {
		c.close(1001, "SERVER_SHUTDOWN")
	}
	c.serve()
}
func (s *Server) Close(ctx context.Context) error {
	s.mu.Lock()
	s.closing = true
	cs := make([]*connection, 0, len(s.connections))
	for c := range s.connections {
		cs = append(cs, c)
	}
	s.mu.Unlock()
	for _, c := range cs {
		go c.close(1001, "SERVER_SHUTDOWN")
	}
	done := make(chan struct{})
	go func() { s.wg.Wait(); close(done) }()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		for _, c := range cs {
			c.ws.Close()
		}
		return ctx.Err()
	}
}
