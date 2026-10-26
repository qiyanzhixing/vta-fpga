package engine

import (
	"context"
	"math"
	"testing"
	"vta-fpga/internal/denoise"
	"vta-fpga/internal/protocol"
)

func TestGate(t *testing.T) {
	g := NewGate(1)
	l, ok := g.TryAcquire()
	if !ok {
		t.Fatal("first admission")
	}
	l.MarkRecovering()
	if _, ok = g.TryAcquire(); ok {
		t.Fatal("released before worker exit")
	}
	if s := g.Snapshot(); s.State != "recovering" || s.ActiveRequests != 1 {
		t.Fatal(s)
	}
	l.Release()
	l.Release()
	if _, ok = g.TryAcquire(); !ok {
		t.Fatal("not released")
	}
}

type badApply struct{ short bool }

func (b badApply) Apply(ctx context.Context, d protocol.Data, c denoise.Config) (protocol.Data, []protocol.ColumnResult, error) {
	out := protocol.Data{TimeUnit: d.TimeUnit, Time: d.Time, Channels: d.Channels, Intensity: [][]float64{{math.NaN()}}}
	if b.short {
		out.Intensity = nil
	}
	return out, []protocol.ColumnResult{{Channel: "a", Status: "processed", RequestedMethod: "none", AppliedMethod: "none", Code: "PROCESSED", EffectiveParams: map[string]any{}}}, nil
}
func TestRunner(t *testing.T) {
	req := protocol.Request{RequestID: "test", Data: protocol.Data{TimeUnit: "min", Time: []float64{0}, Channels: []string{"a"}, Intensity: [][]float64{{1}}}}
	for _, short := range []bool{false, true} {
		r := &Runner{alg: badApply{short}}
		res, e := r.Run(context.Background(), req, denoise.Config{Method: "none"})
		if e != nil || res.Status != "partial" || res.Data.Intensity[0][0] != 1 || res.Stats.Errors != 1 {
			t.Fatal(res, e)
		}
	}
	reg := denoise.NewRegistry()
	cfg, _ := reg.Parse(protocol.Request{Data: req.Data, Config: protocol.Config{Method: "none"}}, 4095)
	res, e := New(reg).Run(context.Background(), req, cfg)
	if e != nil || res.Status != "ok" || res.Stats.Skipped != 1 {
		t.Fatal(res, e)
	}
}
