package denoise

import (
	"context"
	"fmt"
	"math"
	"vta-fpga/internal/protocol"
)

func init() {
	defaults["mz5_11"] = map[string]any{"no_gas_mz": []string{}, "T_idx": []int{}, "T_frac": .1, "k": 5, "max_lag": 30, "aggregation": "mean"}
	operators["mz5_11"] = identity
	baselineParams = validateBaseline
	matrixApply = baseline
}
func validateBaseline(d protocol.Data, c *Config, provided map[string]any, maxWindow int) error {
	p := c.Params
	refs := p["no_gas_mz"].([]string)
	known := map[string]bool{}
	for _, s := range d.Channels {
		known[s] = true
	}
	seen := map[string]bool{}
	for _, s := range refs {
		if !known[s] || seen[s] {
			return fmt.Errorf("references must be unique existing channels")
		}
		seen[s] = true
	}
	if len(refs) < 1 || len(refs) >= len(d.Channels) {
		return fmt.Errorf("requires references and at least one target")
	}
	n := len(d.Time)
	if n < 10 {
		return fmt.Errorf("requires at least ten samples")
	}
	idx, hasIdx := provided["T_idx"]
	_, hasFrac := provided["T_frac"]
	_ = idx
	if hasIdx && hasFrac {
		return fmt.Errorf("T_idx and T_frac are exclusive")
	}
	if hasIdx {
		a := p["T_idx"].([]int)
		if a[0] < 0 || a[1] > n || a[1]-a[0] < 10 {
			return fmt.Errorf("invalid calibration interval")
		}
		delete(p, "T_frac")
	} else {
		p["T_idx"] = []int{0, min(n, max(10, int(float64(n)*p["T_frac"].(float64))))}
		delete(p, "T_frac")
	}
	k, lag := p["k"].(int), p["max_lag"].(int)
	if k < 3 || k%2 == 0 || k > maxWindow || lag < 0 || lag > maxWindow || p["aggregation"] != "mean" {
		return fmt.Errorf("invalid k, max_lag or aggregation")
	}
	return nil
}
func avg(x []float64) float64 {
	s := 0.
	for _, v := range x {
		s += v
	}
	return s / float64(len(x))
}
func meanSquare(x []float64) float64 {
	s := 0.
	for _, v := range x {
		s += float64(v * v)
	}
	return s / float64(len(x))
}
func variation(x []float64) float64 {
	mu := avg(x)
	s, a := 0., 0.
	for _, v := range x {
		d := v - mu
		s += float64(d * d)
		a += math.Abs(v)
	}
	return math.Sqrt(s/float64(len(x))) / (a/float64(len(x)) + 1e-300)
}
func shift(x []float64, lag int) []float64 {
	y := make([]float64, len(x))
	for i := range y {
		y[i] = x[max(0, min(len(x)-1, i+lag))]
	}
	return y
}
func bestLag(ctx context.Context, x, y []float64, maxLag int) (int, error) {
	n := len(x)
	if n < 2*maxLag {
		maxLag = max(1, n/4)
	}
	start, end := maxLag, n-maxLag
	if end <= start {
		start, end = 0, n
	}
	best, score := 0, math.Inf(-1)
	for lag := -maxLag; lag <= maxLag; lag++ {
		if ctx.Err() != nil {
			return 0, ctx.Err()
		}
		var a, b []float64
		if lag >= 0 {
			a = x[start:end]
			b = y[min(n, start+lag):min(n, end+lag)]
		} else {
			a = x[min(n, start-lag):min(n, end-lag)]
			b = y[start:end]
		}
		m := min(len(a), len(b))
		if m < 10 {
			continue
		}
		a, b = a[:m], b[:m]
		ma, mb := avg(a), avg(b)
		aa, bb, ab := 0., 0., 0.
		for i := range a {
			da, db := a[i]-ma, b[i]-mb
			aa += float64(da * da)
			bb += float64(db * db)
			ab += float64(da * db)
		}
		if aa == 0 || bb == 0 {
			continue
		}
		corr := ab / math.Sqrt(aa) / math.Sqrt(bb)
		corr = max(-1, min(1, corr))
		if corr > score {
			score, best = corr, lag
		}
	}
	return best, nil
}
func baseline(ctx context.Context, d protocol.Data, c Config) (protocol.Data, []protocol.ColumnResult, error) {
	out := copyData(d)
	refs := map[string]bool{}
	for _, s := range c.Params["no_gas_mz"].([]string) {
		refs[s] = true
	}
	x := make([]float64, len(d.Time))
	for j, ch := range d.Channels {
		if refs[ch] {
			for i := range x {
				x[i] += d.Intensity[i][j]
			}
		}
	}
	for i := range x {
		x[i] /= float64(len(refs))
	}
	p := c.Params
	idx := p["T_idx"].([]int)
	lo, hi := idx[0], idx[1]
	k := p["k"].(int)
	xt := x[lo:hi]
	smoothX, e := mean(ctx, xt, k)
	if e != nil {
		return out, nil, e
	}
	dx := make([]float64, len(xt))
	for i := range dx {
		dx[i] = xt[i] - smoothX[i]
	}
	sx := meanSquare(dx)
	cols := make([]protocol.ColumnResult, len(d.Channels))
	for j, ch := range d.Channels {
		if ctx.Err() != nil {
			return out, nil, ctx.Err()
		}
		cr := protocol.ColumnResult{Channel: ch, Status: "processed", RequestedMethod: "mz5_11", AppliedMethod: "mz5_11", EffectiveParams: clone(p), Code: "PROCESSED"}
		if refs[ch] {
			preserved(&cr, "REFERENCE_CHANNEL", "skipped")
			cols[j] = cr
			continue
		}
		y := column(d, j)
		yt := y[lo:hi]
		lag, e := bestLag(ctx, xt, yt, p["max_lag"].(int))
		if e != nil {
			return out, nil, e
		}
		smoothY, e := mean(ctx, yt, k)
		if e != nil {
			return out, nil, e
		}
		dy := make([]float64, len(yt))
		for i := range dy {
			dy[i] = yt[i] - smoothY[i]
		}
		if variation(xt) < .01 || variation(yt) < .01 {
			preserved(&cr, "CALIBRATION_LOW_VARIATION", "skipped")
			cols[j] = cr
			continue
		}
		a := 1.
		if sx > 0 {
			a = math.Sqrt(meanSquare(dy) / sx)
		}
		if !finite(a) || !finite(sx) {
			preserved(&cr, "CALIBRATION_FAILED", "skipped")
			cols[j] = cr
			continue
		}
		if a < .5 || a > 2 {
			preserved(&cr, "CALIBRATION_SCALE_OUT_OF_RANGE", "skipped")
			cols[j] = cr
			continue
		}
		shifted := shift(x, lag)
		aligned := shifted[lo:hi]
		m1, m2 := avg(aligned), meanSquare(aligned)
		variance := math.Max(0, m2-float64(m1*m1))
		delta := meanSquare(yt) - float64(a*a)*variance
		offset := -a * m1
		if delta >= 0 {
			s := math.Sqrt(delta)
			c1, c2 := -a*m1+s, -a*m1-s
			offset = c1
			if math.Abs(c2) < math.Abs(c1) {
				offset = c2
			}
		}
		if !finite(offset) {
			preserved(&cr, "CALIBRATION_FAILED", "skipped")
			cols[j] = cr
			continue
		}
		z := make([]float64, len(x))
		for i := range z {
			z[i] = float64(a*shifted[i]) + offset
		}
		sy, e := mean(ctx, y, k)
		if e != nil {
			return out, nil, e
		}
		sz, e := mean(ctx, z, k)
		if e != nil {
			return out, nil, e
		}
		valid := true
		for i := range sy {
			sy[i] -= sz[i]
			if !finite(sy[i]) {
				valid = false
			}
		}
		if !valid {
			preserved(&cr, "NON_FINITE_RESULT", "error")
		} else {
			for i := range sy {
				out.Intensity[i][j] = sy[i]
			}
			cr.Calibration = &protocol.Calibration{TIdx: [2]int{lo, hi}, Lag: lag, A: a, C: offset}
		}
		cols[j] = cr
	}
	return out, cols, nil
}
func finite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
