package app

import (
	"testing"
	"vta-fpga/internal/protocol"
)

func TestDefaults(t *testing.T) {
	c, e := Parse(nil)
	if e != nil {
		t.Fatal(e)
	}
	if c.Listen != ":8080" || c.Server.Limits != protocol.DefaultLimits() {
		t.Fatal(c)
	}
}
func TestInvalidConfig(t *testing.T) {
	for _, args := range [][]string{{"--max-connections=0"}, {"--ping-interval-ms=0"}, {"--max-inflight-per-connection=2"}, {"--max-request-bytes=-1"}, {"extra"}} {
		if _, e := Parse(args); e == nil {
			t.Fatal(args)
		}
	}
}
