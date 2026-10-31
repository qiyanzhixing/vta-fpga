package engine

import (
	"context"
	"math"
	"time"
	"vta-fpga/internal/denoise"
	"vta-fpga/internal/protocol"
)

type applier interface {
	Apply(context.Context, protocol.Data, denoise.Config) (protocol.Data, []protocol.ColumnResult, error)
}
type Runner struct{ alg applier }

func New(reg *denoise.Registry) *Runner { return &Runner{reg} }
func (r *Runner) Run(ctx context.Context, req protocol.Request, cfg denoise.Config) (res protocol.Result, err *protocol.Error) {
	start := time.Now()
	defer func() {
		if recover() != nil {
			res = protocol.Result{}
			err = protocol.Failure(req.RequestID, "INTERNAL_ERROR", "", "execution failed")
		}
	}()
	d, cols, e := r.alg.Apply(ctx, req.Data, cfg)
	if e != nil {
		code := "INTERNAL_ERROR"
		if ctx.Err() != nil {
			code = "PROCESSING_TIMEOUT"
		}
		return res, protocol.Failure(req.RequestID, code, "", e.Error())
	}
	if len(cols) != len(req.Data.Channels) {
		return res, protocol.Failure(req.RequestID, "INTERNAL_ERROR", "", "missing column statuses")
	}
	res = protocol.Result{Version: 1, Type: "result", RequestID: req.RequestID, Status: "ok", AlgorithmVersion: "1.0.0", Data: protocol.Data{TimeUnit: req.Data.TimeUnit, Time: append([]float64(nil), req.Data.Time...), Channels: append([]string(nil), req.Data.Channels...), Intensity: make([][]float64, len(req.Data.Intensity))}, Stats: protocol.Stats{Total: len(cols)}, ColumnResults: cols}
	for i, row := range req.Data.Intensity {
		res.Data.Intensity[i] = append([]float64(nil), row...)
	}
	for j := range cols {
		if ctx.Err() != nil {
			return protocol.Result{}, protocol.Failure(req.RequestID, "PROCESSING_TIMEOUT", "", "processing canceled")
		}
		cr := &cols[j]
		code := ""
		if len(d.Intensity) != len(req.Data.Intensity) {
			code = "INVALID_RESULT_LENGTH"
		} else {
			for _, row := range d.Intensity {
				if len(row) != len(cols) {
					code = "INVALID_RESULT_LENGTH"
					break
				}
				if math.IsNaN(row[j]) || math.IsInf(row[j], 0) {
					code = "NON_FINITE_RESULT"
					break
				}
			}
		}
		if code != "" {
			cr.Status = "error"
			cr.Code = code
			cr.AppliedMethod = "none"
			cr.EffectiveParams = map[string]any{}
			cr.Calibration = nil
		}
		if cr.Status == "processed" {
			for i := range d.Intensity {
				res.Data.Intensity[i][j] = d.Intensity[i][j]
			}
		}
		switch cr.Status {
		case "processed":
			res.Stats.Processed++
		case "skipped":
			res.Stats.Skipped++
		default:
			res.Stats.Errors++
		}
		if cr.Code != "PROCESSED" && cr.Code != "NONE" && cr.Code != "REFERENCE_CHANNEL" {
			res.Status = "partial"
		}
	}
	res.Timing.ProcessingMS = float64(time.Since(start)) / float64(time.Millisecond)
	return res, nil
}
