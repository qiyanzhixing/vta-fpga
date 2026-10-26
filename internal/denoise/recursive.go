package denoise

import "context"

func ewma(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	a := p["alpha"].(float64)
	y := make([]float64, len(x))
	prev := x[0]
	for i, v := range x {
		if i%256 == 0 && ctx.Err() != nil {
			return nil, ctx.Err()
		}
		prev = a*v + (1-a)*prev
		y[i] = prev
	}
	return y, nil
}
func kalman(ctx context.Context, x []float64, p map[string]any) ([]float64, error) {
	q, r := p["process_variance"].(float64), p["measurement_variance"].(float64)
	est, cov := x[0], 1.
	y := make([]float64, len(x))
	for i, v := range x {
		if i%256 == 0 && ctx.Err() != nil {
			return nil, ctx.Err()
		}
		pred := cov + q
		gain := pred / (pred + r)
		est = est + gain*(v-est)
		cov = (1 - gain) * pred
		y[i] = est
	}
	return y, nil
}
