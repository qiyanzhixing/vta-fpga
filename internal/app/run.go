package app

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"time"
	"vta-fpga/internal/denoise"
	"vta-fpga/internal/engine"
	"vta-fpga/internal/server"
	"vta-fpga/internal/telemetry"
)

func Run(ctx context.Context, cfg Config) error {
	listener, e := net.Listen("tcp", cfg.Listen)
	if e != nil {
		return e
	}
	defer listener.Close()
	sampleCtx, stop := context.WithCancel(ctx)
	defer stop()
	metrics := telemetry.New(telemetry.Config{DeviceID: cfg.DeviceID, DeviceModel: cfg.DeviceModel, Interval: cfg.Server.HeartbeatInterval}, nil)
	go metrics.Run(sampleCtx)
	reg := denoise.NewRegistry()
	s := server.New(cfg.Server, reg, engine.New(reg), engine.NewGate(cfg.Server.Limits.MaxActiveRequests), metrics)
	httpServer := &http.Server{Handler: s.Handler(), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16384}
	done := make(chan error, 1)
	go func() { done <- httpServer.Serve(listener) }()
	log.Printf("vta-service listening on %s/vta", listener.Addr())
	select {
	case e = <-done:
		if !errors.Is(e, http.ErrServerClosed) {
			return e
		}
	case <-ctx.Done():
	}
	closeCtx, cancel := context.WithTimeout(context.Background(), cfg.Server.CloseTimeout)
	defer cancel()
	_ = listener.Close()
	_ = s.Close(closeCtx)
	_ = httpServer.Shutdown(closeCtx)
	return nil
}
