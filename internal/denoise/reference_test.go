package denoise

import (
	"context"
	"encoding/json"
	"math"
	"os"
	"strings"
	"testing"
	"vta-fpga/internal/protocol"
)

type fixture struct {
	Name         string          `json:"name"`
	Data         protocol.Data   `json:"data"`
	Config       protocol.Config `json:"config"`
	Expected     [][]float64     `json:"expected_intensity"`
	Codes        []string        `json:"expected_column_codes"`
	Calibrations map[string]struct {
		Lag int     `json:"lag"`
		A   float64 `json:"a"`
		C   float64 `json:"c"`
	} `json:"calibrations"`
	RTol float64 `json:"rtol"`
	ATol float64 `json:"atol"`
}

func TestReference(t *testing.T) {
	b, e := os.ReadFile("testdata/reference.json")
	if e != nil {
		t.Fatal(e)
	}
	var doc struct {
		Cases []fixture `json:"cases"`
	}
	if e = json.Unmarshal(b, &doc); e != nil {
		t.Fatal(e)
	}
	r := NewRegistry()
	for _, f := range doc.Cases {
		t.Run(f.Name, func(t *testing.T) {
			if !r.Has(f.Config.Method) {
				t.Skip("operator not yet registered")
			}
			cfg, e := r.Parse(protocol.Request{Data: f.Data, Config: f.Config}, 4095)
			if e != nil {
				t.Fatal(e)
			}
			out, cols, err := r.Apply(context.Background(), f.Data, cfg)
			if err != nil {
				t.Fatal(err)
			}
			for j, cr := range cols {
				if cr.Code != f.Codes[j] {
					t.Fatalf("column %d code %s want %s", j, cr.Code, f.Codes[j])
				}
				scale := 0.
				for _, row := range f.Data.Intensity {
					scale = math.Max(scale, math.Abs(row[j]))
				}
				for i, row := range out.Intensity {
					want := f.Expected[i][j]
					tol := f.ATol + f.RTol*math.Max(math.Abs(want), scale)
					if math.Abs(row[j]-want) > tol {
						t.Fatalf("[%d,%d] got %.17g want %.17g tolerance %g", i, j, row[j], want, tol)
					}
					if f.Config.Method == "none" && row[j] != want {
						t.Fatal("none modified input")
					}
					if (f.Config.Method == "quantile" || f.Config.Method == "std_threshold") && (row[j] == 0) != (want == 0) {
						t.Fatal("zero mask differs")
					}
				}
				if cal, ok := f.Calibrations[cr.Channel]; ok {
					if cr.Calibration == nil || cal.Lag != cr.Calibration.Lag || math.Abs(cal.A-cr.Calibration.A) > 1e-7 || math.Abs(cal.C-cr.Calibration.C) > 1e-24+1e-7*scale {
						t.Fatalf("calibration got %+v want %+v", cr.Calibration, cal)
					}
				}
			}
		})
	}
}
func sample() protocol.Data {
	return protocol.Data{TimeUnit: "min", Time: []float64{0, 1, 2}, Channels: []string{"a"}, Intensity: [][]float64{{1}, {2}, {3}}}
}
func TestParams(t *testing.T) {
	r := NewRegistry()
	for _, s := range []string{`{"window_size":3.0}`, `{"window_size":3e0}`, `{"window_size":null}`, `{"bad":1}`, `{"window_size":0}`} {
		_, e := r.Parse(protocol.Request{Config: protocol.Config{Method: "mean", Params: json.RawMessage(s)}}, 4095)
		if e == nil {
			t.Fatal(s)
		}
	}
	for _, c := range []protocol.Config{{Method: "ewma", Params: json.RawMessage(`{"alpha":0}`)}, {Method: "kalman", Params: json.RawMessage(`{"measurement_variance":0}`)}} {
		if _, e := r.Parse(protocol.Request{Config: c}, 4095); e == nil {
			t.Fatal(c)
		}
	}
}
func TestBasic(t *testing.T) {
	r := NewRegistry()
	d := sample()
	cfg, e := r.Parse(protocol.Request{Data: d, Config: protocol.Config{Method: "mean", Params: json.RawMessage(`{"window_size":3}`)}}, 4095)
	if e != nil {
		t.Fatal(e)
	}
	out, _, err := r.Apply(context.Background(), d, cfg)
	if err != nil {
		t.Fatal(err)
	}
	for i, w := range []float64{4. / 3, 2, 8. / 3} {
		if math.Abs(out.Intensity[i][0]-w) > 1e-14 {
			t.Fatal(out)
		}
	}
	if d.Intensity[0][0] != 1 {
		t.Fatal("input modified")
	}
}
func TestRecursive(t *testing.T) {
	r := NewRegistry()
	d := sample()
	d.Intensity = [][]float64{{1}, {3}, {5}}
	out, _, e := r.Apply(context.Background(), d, Config{"ewma", map[string]any{"alpha": .5}})
	if e != nil || out.Intensity[1][0] != 2 {
		t.Fatal(out, e)
	}
}
func TestCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, _, e := NewRegistry().Apply(ctx, sample(), Config{"none", map[string]any{}})
	if e == nil || !strings.Contains(e.Error(), "canceled") {
		t.Fatal(e)
	}
}
