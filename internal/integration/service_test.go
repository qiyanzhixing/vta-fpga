package integration

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"math"
	"net/http/httptest"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
	"vta-fpga/internal/denoise"
	"vta-fpga/internal/engine"
	"vta-fpga/internal/protocol"
	"vta-fpga/internal/server"
	"vta-fpga/internal/telemetry"
	"vta-fpga/internal/testclient"
)

func sampleCSV(t testing.TB) protocol.Data {
	t.Helper()
	f, e := os.Open("../../docs/cleandata_ms.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	d := protocol.Data{TimeUnit: "min", Channels: rows[0][1:]}
	for _, row := range rows[1:] {
		v, e := strconv.ParseFloat(row[0], 64)
		if e != nil {
			t.Fatal(e)
		}
		d.Time = append(d.Time, v)
		vals := make([]float64, len(d.Channels))
		for j, s := range row[1:] {
			vals[j], e = strconv.ParseFloat(s, 64)
			if e != nil {
				t.Fatal(e)
			}
		}
		d.Intensity = append(d.Intensity, vals)
	}
	return d
}
func client(t *testing.T) *testclient.Client {
	t.Helper()
	reg := denoise.NewRegistry()
	cfg := server.DefaultConfig()
	cfg.HeartbeatInterval = 20 * time.Millisecond
	metrics := telemetry.New(telemetry.Config{}, nil)
	s := server.New(cfg, reg, engine.New(reg), engine.NewGate(1), metrics)
	ts := httptest.NewServer(s.Handler())
	c := testclient.New(testclient.Config{})
	ctx, cancel := context.WithCancel(context.Background())
	if e := c.Connect(ctx, "ws"+strings.TrimPrefix(ts.URL, "http")+"/vta"); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() {
		closeCtx, closeCancel := context.WithTimeout(context.Background(), time.Second)
		defer closeCancel()
		c.Close(closeCtx)
		cancel()
		s.Close(closeCtx)
		ts.Close()
	})
	return c
}
func TestCompleteCSV(t *testing.T) {
	c := client(t)
	d := sampleCSV(t)
	if len(d.Time) != 1911 || len(d.Channels) != 63 {
		t.Fatal("fixture dimensions changed")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	res, e := c.Denoise(ctx, protocol.Request{Version: 1, Type: "denoise", RequestID: "full-csv", Data: d, Config: protocol.Config{Method: "none", Params: json.RawMessage(`{}`)}})
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(res.Data, d) || res.Status != "ok" || res.Stats.Skipped != 63 {
		t.Fatal("complete CSV did not round trip exactly", res.Stats, res.Status)
	}
}
func TestReferenceOverWebSocket(t *testing.T) {
	c := client(t)
	b, e := os.ReadFile("../denoise/testdata/reference.json")
	if e != nil {
		t.Fatal(e)
	}
	var doc struct {
		Cases []struct {
			Name     string          `json:"name"`
			Data     protocol.Data   `json:"data"`
			Config   protocol.Config `json:"config"`
			Expected [][]float64     `json:"expected_intensity"`
			Codes    []string        `json:"expected_column_codes"`
		} `json:"cases"`
	}
	if e = json.Unmarshal(b, &doc); e != nil {
		t.Fatal(e)
	}
	for _, f := range doc.Cases {
		t.Run(f.Name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			res, e := c.Denoise(ctx, protocol.Request{Version: 1, Type: "denoise", RequestID: f.Name, Data: f.Data, Config: f.Config})
			if e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(res.Data.Time, f.Data.Time) || !reflect.DeepEqual(res.Data.Channels, f.Data.Channels) {
				t.Fatal("axes changed")
			}
			for j, cr := range res.ColumnResults {
				if cr.Code != f.Codes[j] {
					t.Fatal(cr)
				}
				scale := 0.
				for _, row := range f.Data.Intensity {
					scale = math.Max(scale, math.Abs(row[j]))
				}
				for i, row := range res.Data.Intensity {
					want := f.Expected[i][j]
					if math.Abs(row[j]-want) > 1e-24+1e-7*math.Max(math.Abs(want), scale) {
						t.Fatalf("value mismatch at %d,%d", i, j)
					}
				}
			}
		})
	}
}
