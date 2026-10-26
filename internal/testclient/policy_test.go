package testclient

import (
	"testing"
	"time"
)

func TestBackoff(t *testing.T) {
	want := []time.Duration{1000, 2000, 4000, 8000, 16000, 30000}
	for k, v := range want {
		if got := Backoff(k, 1); got != v*time.Millisecond {
			t.Fatal(k, got)
		}
	}
	if Backoff(1000, .5) != 15*time.Second {
		t.Fatal("overflow")
	}
}
func TestPolicy(t *testing.T) {
	p := Policy{}
	g := p.Begin()
	if p.Disconnected(g, 1006) != true {
		t.Fatal("network failure must retry")
	}
	if p.Disconnected(g, 1006) {
		t.Fatal("double reconnect")
	}
	newGen := p.Begin()
	if p.Disconnected(g, 1006) {
		t.Fatal("old close")
	}
	p.Stop()
	if p.Disconnected(newGen, 1006) {
		t.Fatal("manual close retries")
	}
	for _, code := range []int{1000, 1002, 1003, 1007, 1008, 1009, 4999} {
		if RetryClose(code) {
			t.Fatal(code)
		}
	}
	for _, code := range []int{1001, 1006, 1011, 4000, 4001} {
		if !RetryClose(code) {
			t.Fatal(code)
		}
	}
	if RetryHTTP(404) || !RetryHTTP(503) {
		t.Fatal("HTTP policy")
	}
}
