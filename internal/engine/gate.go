package engine

import (
	"sync"
	"vta-fpga/internal/protocol"
)

// A Lease remains occupied until the worker (including encoding) actually returns.
type Gate struct {
	mu     sync.Mutex
	max    int
	leases map[*Lease]bool
}
type Lease struct{ gate *Gate }

func NewGate(n int) *Gate { return &Gate{max: n, leases: map[*Lease]bool{}} }
func (g *Gate) TryAcquire() (*Lease, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.leases) >= g.max {
		return nil, false
	}
	l := &Lease{g}
	g.leases[l] = false
	return l, true
}
func (l *Lease) MarkRecovering() {
	g := l.gate
	g.mu.Lock()
	defer g.mu.Unlock()
	if _, ok := g.leases[l]; ok {
		g.leases[l] = true
	}
}
func (l *Lease) Release() { g := l.gate; g.mu.Lock(); defer g.mu.Unlock(); delete(g.leases, l) }
func (g *Gate) Snapshot() protocol.ServiceStatus {
	g.mu.Lock()
	defer g.mu.Unlock()
	s := protocol.ServiceStatus{State: "idle", ActiveRequests: len(g.leases)}
	if s.ActiveRequests > 0 {
		s.State = "processing"
	}
	for _, recovering := range g.leases {
		if recovering {
			s.State = "recovering"
		}
	}
	return s
}
