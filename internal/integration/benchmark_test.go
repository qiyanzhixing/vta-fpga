package integration

import (
	"context"
	"encoding/json"
	"testing"
	"vta-fpga/internal/denoise"
	"vta-fpga/internal/engine"
	"vta-fpga/internal/protocol"
)

func BenchmarkSample(b *testing.B) {
	d := sampleCSV(b)
	reg := denoise.NewRegistry()
	runner := engine.New(reg)
	for _, method := range reg.Methods() {
		b.Run(method, func(b *testing.B) {
			params := json.RawMessage(`{}`)
			if method == "mz5_11" {
				params = json.RawMessage(`{"no_gas_mz":["mz5","mz6","mz7","mz8","mz9","mz10","mz11"]}`)
			}
			req := protocol.Request{Version: 1, Type: "denoise", RequestID: "bench", Data: d, Config: protocol.Config{Method: method, Params: params}}
			cfg, e := reg.Parse(req, 4095)
			if e != nil {
				b.Fatal(e)
			}
			b.ReportAllocs()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				if _, e := runner.Run(context.Background(), req, cfg); e != nil {
					b.Fatal(e)
				}
			}
		})
	}
}
