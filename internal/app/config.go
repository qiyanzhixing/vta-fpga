package app

import (
	"flag"
	"fmt"
	"io"
	"time"
	"vta-fpga/internal/server"
)

type Config struct {
	Listen, DeviceID, DeviceModel string
	Server                        server.Config
}

func Parse(args []string) (Config, error) {
	c := Config{Server: server.DefaultConfig()}
	fs := flag.NewFlagSet("vta-service", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.StringVar(&c.Listen, "listen", ":8080", "HTTP listen address")
	fs.StringVar(&c.DeviceID, "device-id", "", "stable device identifier")
	fs.StringVar(&c.DeviceModel, "device-model", "", "device model override")
	l := &c.Server.Limits
	fs.Int64Var(&l.MaxRequestBytes, "max-request-bytes", l.MaxRequestBytes, "complete JSON request byte limit")
	fs.Int64Var(&l.MaxResponseBytes, "max-response-bytes", l.MaxResponseBytes, "result byte limit")
	ints := map[string]*int{"max-time-points": &l.MaxTimePoints, "max-channels": &l.MaxChannels, "max-cells": &l.MaxCells, "max-window-size": &l.MaxWindowSize, "max-inflight-per-connection": &l.MaxInflightPerConnection, "max-active-requests": &l.MaxActiveRequests, "max-connections": &l.MaxConnections}
	for name, p := range ints {
		fs.IntVar(p, name, *p, name)
	}
	durations := map[string]*time.Duration{"processing-timeout-ms": &l.ProcessingTimeout, "upload-timeout-ms": &l.UploadTimeout, "write-timeout-ms": &l.WriteTimeout, "ping-interval-ms": &l.PingInterval, "pong-timeout-ms": &l.PongTimeout, "report-interval-ms": &l.ReportInterval, "report-stale-timeout-ms": &l.ReportStaleTimeout, "close-timeout-ms": &c.Server.CloseTimeout}
	milliseconds := map[string]*int64{}
	for name, p := range durations {
		milliseconds[name] = fs.Int64(name, p.Milliseconds(), "milliseconds")
	}
	if e := fs.Parse(args); e != nil {
		return c, e
	}
	if fs.NArg() != 0 {
		return c, fmt.Errorf("unexpected positional arguments")
	}
	if c.Listen == "" || l.MaxRequestBytes < 1 || l.MaxResponseBytes < 1 {
		return c, fmt.Errorf("listen and byte limits must be valid")
	}
	for name, p := range ints {
		if *p < 1 {
			return c, fmt.Errorf("%s must be positive", name)
		}
	}
	if l.MaxInflightPerConnection != 1 {
		return c, fmt.Errorf("v1 requires exactly one inflight request per connection")
	}
	for name, p := range durations {
		v := *milliseconds[name]
		if v <= 0 || v > int64((24*time.Hour)/time.Millisecond) {
			return c, fmt.Errorf("%s must be between 1 and 86400000", name)
		}
		*p = time.Duration(v) * time.Millisecond
	}
	if l.PingInterval >= l.PongTimeout || l.ReportInterval >= l.ReportStaleTimeout {
		return c, fmt.Errorf("timeouts must exceed their intervals")
	}
	c.Server.PingInterval = l.PingInterval
	c.Server.PongTimeout = l.PongTimeout
	c.Server.HeartbeatInterval = l.ReportInterval
	return c, nil
}
