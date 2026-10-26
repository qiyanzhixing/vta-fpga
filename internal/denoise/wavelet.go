package denoise

import (
	"context"
	"math"
	"sort"
)

// db4 filter bank from PyWavelets 1.10.0 Wavelet("db4").filter_bank.
// Symmetric half-sample extension and decimation phase match pywt.wavedec.
var db4Low = []float64{-0.010597401785069032, 0.0328830116668852, 0.030841381835560764, -0.18703481171909309, -0.027983769416859854, 0.6308807679298589, 0.7148465705529157, 0.2303778133088965}
var db4High = []float64{-0.2303778133088965, 0.7148465705529157, -0.6308807679298589, -0.027983769416859854, 0.18703481171909309, 0.030841381835560764, -0.0328830116668852, -0.010597401785069032}

func init() {
	defaults["wavelet"] = map[string]any{"wavelet": "db4", "level": 3, "threshold_type": "soft"}
	operators["wavelet"] = wavelet
}
func dwt(ctx context.Context, x []float64) ([]float64, []float64, error) {
	a, d := make([]float64, (len(x)+7)/2), make([]float64, (len(x)+7)/2)
	for i := range a {
		if i%256 == 0 && ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		for k := range db4Low {
			v := x[reflectIndex(2*i+1-k, len(x))]
			a[i] += db4Low[k] * v
			d[i] += db4High[k] * v
		}
	}
	return a, d, nil
}
func idwt(ctx context.Context, a, d []float64) ([]float64, error) {
	n := 2*len(d) - 6
	y := make([]float64, n)
	for i := range d {
		if i%256 == 0 && ctx.Err() != nil {
			return nil, ctx.Err()
		}
		for k := 0; k < 8; k++ {
			j := 2*i + k - 6
			if j >= 0 && j < n {
				y[j] += a[i]*db4Low[7-k] + d[i]*db4High[7-k]
			}
		}
	}
	return y, nil
}
func wavelet(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	if len(x) < 8 {
		return mean(ctx, x, min(3, len(x)))
	}
	lv := waveletLevel(len(x), p["level"].(int))
	a := x
	details := make([][]float64, lv)
	var e error
	for i := 0; i < lv; i++ {
		a, details[i], e = dwt(ctx, a)
		if e != nil {
			return nil, e
		}
	}
	abs := make([]float64, len(details[0]))
	for i, v := range details[0] {
		abs[i] = math.Abs(v)
	}
	sort.Float64s(abs)
	med := abs[len(abs)/2]
	if len(abs)%2 == 0 {
		med = (med + abs[len(abs)/2-1]) / 2
	}
	threshold := med / .6745 * math.Sqrt(2*math.Log(float64(len(x))))
	for _, d := range details {
		for i, v := range d {
			if p["threshold_type"] == "hard" {
				if math.Abs(v) < threshold {
					d[i] = 0
				}
			} else { // pywt soft threshold intentionally yields NaN for 0/0; column validation preserves input.
				ratio := threshold / math.Abs(v)
				d[i] = v * math.Max(1-ratio, 0)
			}
		}
	}
	for i := lv - 1; i >= 0; i-- {
		a, e = idwt(ctx, a, details[i])
		if e != nil {
			return nil, e
		}
		if i > 0 && len(a) > len(details[i-1]) {
			a = a[:len(details[i-1])]
		}
	}
	return a[:len(x)], nil
}
