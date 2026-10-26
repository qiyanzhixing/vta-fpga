package denoise

import (
	"context"
	"encoding/json"
	"testing"
	"vta-fpga/internal/protocol"
)

func TestBaseline(t *testing.T) {
	r := NewRegistry()
	d := protocol.Data{Channels: []string{"ref", "target"}, Time: make([]float64, 20), Intensity: make([][]float64, 20)}
	for i := range d.Intensity {
		d.Intensity[i] = []float64{1, 2}
	}
	for _, p := range []string{`{}`, `{"no_gas_mz":["missing"]}`, `{"no_gas_mz":["ref","target"]}`, `{"no_gas_mz":["ref"],"T_idx":[0,21]}`, `{"no_gas_mz":["ref"],"T_idx":[0,20],"T_frac":0.5}`} {
		_, e := r.Parse(protocol.Request{Data: d, Config: protocol.Config{Method: "mz5_11", Params: json.RawMessage(p)}}, 4095)
		if e == nil || e.Detail.Code != "INVALID_CONFIG" {
			t.Fatalf("%s: %+v", p, e)
		}
	}
	c, e := r.Parse(protocol.Request{Data: d, Config: protocol.Config{Method: "mz5_11", Params: json.RawMessage(`{"no_gas_mz":["ref"]}`)}}, 4095)
	if e != nil {
		t.Fatal(e)
	}
	o, cr, err := r.Apply(context.Background(), d, c)
	if err != nil || cr[0].Code != "REFERENCE_CHANNEL" || cr[1].Code != "CALIBRATION_LOW_VARIATION" || o.Intensity[0][1] != 2 {
		t.Fatal(o, cr, err)
	}
	if c.Params["k"] != 5 || c.Params["max_lag"] != 30 || len(r.Methods()) != 10 {
		t.Fatal(c, r.Methods())
	}
}
