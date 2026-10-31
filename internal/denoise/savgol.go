package denoise

import (
	"context"
	"fmt"
	"gonum.org/v1/gonum/mat"
)

func init() {
	defaults["savitzky_golay"] = map[string]any{"window_size": 11, "polyorder": 3}
	operators["savitzky_golay"] = savgol
}

// Scaled Vandermonde QR avoids normal equations. Workspace is capped before allocation.
func savgol(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	w, degree := p["window_size"].(int), p["polyorder"].(int)
	if len(x) < w {
		return mean(ctx, x, min(3, len(x)))
	}
	q := degree + 1
	if w < 1 || q < 1 || q > w || int64(w)*int64(q)*32+int64(q)*int64(q)*16 > 32<<20 {
		return nil, fmt.Errorf("Savitzky-Golay workspace exceeds 32 MiB")
	}
	a := mat.NewDense(w, q, nil)
	half := w / 2
	scale := float64(max(1, half))
	for i := 0; i < w; i++ {
		v := 1.
		z := float64(i-half) / scale
		for j := 0; j < q; j++ {
			a.Set(i, j, v)
			v *= z
		}
	}
	var qr mat.QR
	qr.Factorize(a)
	weights := func(pos int) ([]float64, error) {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		b := mat.NewDense(q, 1, nil)
		v := 1.
		z := float64(pos-half) / scale
		for j := 0; j < q; j++ {
			b.Set(j, 0, v)
			v *= z
		}
		var dst mat.Dense
		if e := qr.SolveTo(&dst, true, b); e != nil {
			return nil, fmt.Errorf("ill-conditioned polynomial fit: %w", e)
		}
		return append([]float64(nil), dst.RawMatrix().Data...), nil
	}
	center, e := weights(half)
	if e != nil {
		return nil, e
	}
	y := make([]float64, len(x))
	for i := range x {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		start := max(0, min(i-half, len(x)-w))
		pos := i - start
		ww := center
		if pos != half {
			ww, e = weights(pos)
			if e != nil {
				return nil, e
			}
		}
		for j, v := range ww {
			y[i] += v * x[start+j]
		}
	}
	return y, nil
}
