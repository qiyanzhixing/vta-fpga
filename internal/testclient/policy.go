// Package testclient is a headless integration aid, not a production PC UI.
package testclient

import (
	"math"
	"time"
)

type Policy struct {
	Generation uint64
	Desired    bool
	cleaned    bool
}

func (p *Policy) Begin() uint64 {
	p.Generation++
	p.Desired = true
	p.cleaned = false
	return p.Generation
}
func (p *Policy) Disconnected(g uint64, code int) bool {
	if g != p.Generation || p.cleaned {
		return false
	}
	p.cleaned = true
	return p.Desired && RetryClose(code)
}
func (p *Policy) Stop()     { p.Desired = false }
func RetryClose(c int) bool { return c == 1001 || c == 1006 || c == 1011 || c == 4000 || c == 4001 }
func RetryHTTP(c int) bool  { return c >= 500 && c <= 599 }
func Backoff(k int, r float64) time.Duration {
	capMS := 30000
	if k < 5 {
		capMS = 1000 << max(0, k)
	}
	if math.IsNaN(r) {
		r = 0
	}
	r = max(0, min(1, r))
	return time.Duration(r*float64(capMS)) * time.Millisecond
}
