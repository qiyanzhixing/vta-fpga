package denoise

import (
	"context"
	"math"
	"sort"
)

func identity(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	return append([]float64(nil), x...), ctx.Err()
}
func reflectIndex(i, n int) int {
	i %= 2 * n
	if i < 0 {
		i += 2 * n
	}
	if i >= n {
		i = 2*n - 1 - i
	}
	return i
}
func meanOp(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	return mean(ctx, x, p["window_size"].(int))
}
func mean(ctx context.Context, x []float64, w int) ([]float64, error) {
	y := make([]float64, len(x))
	s := 0.
	for k := -w / 2; k < w-w/2; k++ {
		s += x[reflectIndex(k, len(x))]
	}
	for i := range x {
		if i%256 == 0 && ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if i > 0 {
			s += x[reflectIndex(i+w-w/2-1, len(x))] - x[reflectIndex(i-1-w/2, len(x))]
		}
		y[i] = s / float64(w)
	}
	return y, nil
}
func rankFilter(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	w := p["window_size"].(int)
	rank := w / 2
	quant := p["_method"] == "quantile"
	if quant {
		percentile := float64(p["quantile"].(float64) * 100)
		rank = min(w-1, int(float64(float64(w)*percentile)/100))
	}
	y := make([]float64, len(x))
	buf := make([]float64, w)
	for i := range x {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		for k := range buf {
			buf[k] = x[reflectIndex(i+k-w/2, len(x))]
		}
		sort.Float64s(buf)
		if quant {
			if x[i] >= buf[rank] {
				y[i] = x[i]
			}
		} else {
			y[i] = buf[rank]
		}
	}
	return y, nil
}
func stdThreshold(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	h := p["window_size"].(int) / 2
	sigma := p["sigma_multiple"].(float64)
	s, q := make([]float64, len(x)+1), make([]float64, len(x)+1)
	for i, v := range x {
		s[i+1] = s[i] + v
		q[i+1] = q[i] + float64(v*v)
	}
	y := append([]float64(nil), x...)
	for i, v := range x {
		if i%256 == 0 && ctx.Err() != nil {
			return nil, ctx.Err()
		}
		l, r := max(0, i-h), min(len(x), i+h+1)
		n := float64(r - l)
		mu := (s[r] - s[l]) / n
		variance := math.Max(0, (q[r]-q[l])/n-float64(mu*mu))
		if v < mu+float64(sigma*math.Sqrt(variance)) {
			y[i] = 0
		}
	}
	return y, nil
}
