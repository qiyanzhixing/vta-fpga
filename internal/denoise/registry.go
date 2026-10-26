package denoise

import (
	"context"
	"fmt"
	"math"
	"sort"
	"vta-fpga/internal/protocol"
)

type Config struct {
	Method string
	Params map[string]any
}
type operator func(context.Context, []float64, map[string]any) ([]float64, error)
type Registry struct{ ops map[string]operator }

var operators = map[string]operator{"none": identity, "mean": meanOp, "std_threshold": stdThreshold, "ewma": ewma, "median": rankFilter, "quantile": rankFilter, "kalman": kalman}

func NewRegistry() *Registry {
	m := map[string]operator{}
	for k, v := range operators {
		m[k] = v
	}
	return &Registry{m}
}
func (r *Registry) Has(method string) bool { _, ok := r.ops[method]; return ok }
func (r *Registry) Methods() []string {
	ss := []string{}
	for k := range r.ops {
		ss = append(ss, k)
	}
	sort.Strings(ss)
	return ss
}
func preserved(cr *protocol.ColumnResult, code, status string) {
	cr.Code = code
	cr.Status = status
	cr.AppliedMethod = "none"
	cr.EffectiveParams = map[string]any{}
	cr.Calibration = nil
}

var matrixApply func(context.Context, protocol.Data, Config) (protocol.Data, []protocol.ColumnResult, error)

func (r *Registry) Apply(ctx context.Context, d protocol.Data, cfg Config) (out protocol.Data, cols []protocol.ColumnResult, err error) {
	if ctx.Err() != nil {
		return out, nil, ctx.Err()
	}
	if cfg.Method == "mz5_11" && matrixApply != nil {
		return matrixApply(ctx, d, cfg)
	}
	op, ok := r.ops[cfg.Method]
	if !ok {
		return out, nil, fmt.Errorf("unregistered operator")
	}
	out = copyData(d)
	cols = make([]protocol.ColumnResult, len(d.Channels))
	for j, ch := range d.Channels {
		if ctx.Err() != nil {
			return out, nil, ctx.Err()
		}
		x := column(d, j)
		cr := protocol.ColumnResult{Channel: ch, Status: "processed", RequestedMethod: cfg.Method, AppliedMethod: cfg.Method, EffectiveParams: clone(cfg.Params), Code: "PROCESSED"}
		p := clone(cfg.Params)
		p["_method"] = cfg.Method
		y, e := safeApply(ctx, op, x, p)
		if ctx.Err() != nil {
			return out, nil, ctx.Err()
		}
		if e != nil {
			preserved(&cr, "ALGORITHM_ERROR", "error")
			cr.Message = e.Error()
		} else if len(y) != len(x) {
			preserved(&cr, "INVALID_RESULT_LENGTH", "error")
		} else {
			for _, v := range y {
				if math.IsInf(v, 0) || math.IsNaN(v) {
					preserved(&cr, "NON_FINITE_RESULT", "error")
					break
				}
			}
		}
		if cr.Status != "error" {
			if cfg.Method == "none" {
				preserved(&cr, "NONE", "skipped")
			}
			if cfg.Method == "savitzky_golay" && len(x) < cfg.Params["window_size"].(int) || cfg.Method == "wavelet" && len(x) < 8 {
				cr.Code = "FALLBACK_SHORT_SERIES"
				cr.AppliedMethod = "mean"
				cr.EffectiveParams = map[string]any{"window_size": min(3, len(x))}
			} else if cfg.Method == "wavelet" {
				lv := waveletLevel(len(x), cfg.Params["level"].(int))
				if lv != cfg.Params["level"] {
					cr.Code = "WAVELET_LEVEL_ADJUSTED"
					cr.EffectiveParams["level"] = lv
				}
			}
			for i := range y {
				out.Intensity[i][j] = y[i]
			}
		}
		cols[j] = cr
	}
	return out, cols, nil
}
func safeApply(ctx context.Context, op operator, x []float64, p map[string]any) (y []float64, e error) {
	defer func() {
		if recover() != nil {
			y = nil
			e = fmt.Errorf("operator failed")
		}
	}()
	return op(ctx, x, p)
}
func copyData(d protocol.Data) protocol.Data {
	o := protocol.Data{TimeUnit: d.TimeUnit, Time: append([]float64(nil), d.Time...), Channels: append([]string(nil), d.Channels...), Intensity: make([][]float64, len(d.Intensity))}
	for i, row := range d.Intensity {
		o.Intensity[i] = append([]float64(nil), row...)
	}
	return o
}
func column(d protocol.Data, j int) []float64 {
	x := make([]float64, len(d.Intensity))
	for i := range x {
		x[i] = d.Intensity[i][j]
	}
	return x
}
func waveletLevel(n, want int) int {
	mx := 0
	for n >= 14 {
		mx++
		n /= 2
	}
	return max(1, min(want, mx))
}
