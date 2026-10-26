package denoise

import (
	"context"
	"encoding/json"
	"math"
	"testing"
	"vta-fpga/internal/protocol"
)

func TestSavgol(t *testing.T) {
	r := NewRegistry()
	d := sample()
	d.Time = make([]float64, 25)
	d.Intensity = make([][]float64, 25)
	for i := range d.Time {
		d.Time[i] = float64(i)
		d.Intensity[i] = []float64{math.Pow(float64(i), 3) - 2*float64(i)}
	}
	c, e := r.Parse(protocol.Request{Data: d, Config: protocol.Config{Method: "savitzky_golay"}}, 4095)
	if e != nil {
		t.Fatal(e)
	}
	o, cols, err := r.Apply(context.Background(), d, c)
	if err != nil || cols[0].Code != "PROCESSED" {
		t.Fatal(err, cols)
	}
	for i := range d.Time {
		if math.Abs(o.Intensity[i][0]-d.Intensity[i][0]) > 1e-8 {
			t.Fatal(i, o.Intensity[i])
		}
	}
	d = sample()
	o, cols, err = r.Apply(context.Background(), d, c)
	if err != nil || cols[0].Code != "FALLBACK_SHORT_SERIES" || math.Abs(o.Intensity[0][0]-4./3) > 1e-14 {
		t.Fatal(err, cols, o)
	}
}
func TestSavgolBudget(t *testing.T) {
	r := NewRegistry()
	d := sample()
	d.Time = make([]float64, 4095)
	d.Intensity = make([][]float64, 4095)
	for i := range d.Intensity {
		d.Intensity[i] = []float64{1}
	}
	c := Config{"savitzky_golay", map[string]any{"window_size": 4095, "polyorder": 4094}}
	_, cr, e := r.Apply(context.Background(), d, c)
	if e != nil || cr[0].Code != "ALGORITHM_ERROR" {
		t.Fatal(e, cr)
	}
}
func TestWavelet(t *testing.T) {
	r := NewRegistry()
	c, e := r.Parse(protocol.Request{Config: protocol.Config{Method: "wavelet"}}, 4095)
	if e != nil {
		t.Fatal(e)
	}
	_, cols, err := r.Apply(context.Background(), sample(), c)
	if err != nil || cols[0].Code != "FALLBACK_SHORT_SERIES" {
		t.Fatal(cols, err)
	}
	_, e = r.Parse(protocol.Request{Config: protocol.Config{Method: "wavelet", Params: json.RawMessage(`{"wavelet":"db5"}`)}}, 4095)
	if e == nil || e.Detail.Code != "UNSUPPORTED_METHOD" {
		t.Fatal(e)
	}
}
