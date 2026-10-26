package protocol

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

const valid = `{"version":1,"type":"denoise","request_id":"ms-1","data":{"time_unit":"min","time":[0,0.1],"channels":["mz28"],"intensity":[[1],[-2]]},"config":{"method":"mean","params":{"window_size":3}}}`

func TestDecode(t *testing.T) {
	cases := []struct{ name, raw, code string }{
		{"valid", valid, ""}, {"duplicate", strings.Replace(valid, `"version":1`, `"version":1,"version":1`, 1), "INVALID_JSON"},
		{"nested duplicate", strings.Replace(valid, `"window_size":3`, `"window_size":3,"window_size":5`, 1), "INVALID_JSON"},
		{"null", strings.Replace(valid, `[[1],[-2]]`, `[[null],[-2]]`, 1), "INVALID_DATA"},
		{"overflow", strings.Replace(valid, `[[1],[-2]]`, `[[1e999],[-2]]`, 1), "INVALID_DATA"},
		{"shape", strings.Replace(valid, `[[1],[-2]]`, `[[1]]`, 1), "INVALID_SHAPE"},
		{"legacy", strings.Replace(valid, `"method":"mean","params":{"window_size":3}`, `"default":{"method":"mean"}`, 1), "INVALID_REQUEST"},
		{"trailing", valid + ` {}`, "INVALID_JSON"}, {"unknown", strings.Replace(valid, `"time_unit"`, `"extra":1,"time_unit"`, 1), "INVALID_REQUEST"},
		{"null params", strings.Replace(valid, `{"window_size":3}`, `null`, 1), "INVALID_CONFIG"},
		{"time", strings.Replace(valid, `[0,0.1]`, `[0,0]`, 1), "INVALID_DATA"},
		{"version decimal", strings.Replace(valid, `"version":1`, `"version":1.0`, 1), "INVALID_REQUEST"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, e := DecodeRequest(context.Background(), []byte(c.raw), DefaultLimits())
			got := ""
			if e != nil {
				got = e.Detail.Code
			}
			if got != c.code {
				t.Fatalf("got %s (%+v), want %s", got, e, c.code)
			}
		})
	}
}
func TestDecodeLimits(t *testing.T) {
	l := DefaultLimits()
	l.MaxCells = 1
	_, e := DecodeRequest(context.Background(), []byte(valid), l)
	if e == nil || e.Detail.Code != "LIMIT_EXCEEDED" {
		t.Fatal(e)
	}
}
func TestHelloSummary(t *testing.T) {
	b, _ := json.Marshal(Hello{Version: 1, Type: "hello", Functions: map[string][]string{"denoise": {"none"}}})
	var m map[string]any
	json.Unmarshal(b, &m)
	if len(m) != 6 {
		t.Fatal(string(b))
	}
	for _, k := range []string{"version", "type", "device", "cpu", "memory", "functions"} {
		if _, ok := m[k]; !ok {
			t.Fatal(k)
		}
	}
	if strings.Contains(string(b), "usage_percent") {
		t.Fatal(string(b))
	}
}
func TestUnderflow(t *testing.T) {
	for _, tc := range []struct{ token, code string }{{"1e-999", "INVALID_DATA"}, {"-1e-999", "INVALID_DATA"}, {"0e-999", ""}, {"5e-324", ""}} {
		_, e := DecodeRequest(context.Background(), []byte(strings.Replace(valid, "[[1],[-2]]", "[["+tc.token+"],[-2]]", 1)), DefaultLimits())
		got := ""
		if e != nil {
			got = e.Detail.Code
		}
		if got != tc.code {
			t.Fatalf("%s got %s want %s", tc.token, got, tc.code)
		}
	}
}
