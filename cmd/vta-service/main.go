package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"vta-fpga/internal/app"
)

func main() {
	cfg, e := app.Parse(os.Args[1:])
	if e == flag.ErrHelp {
		fmt.Println("vta-service --listen :8080 --device-id board-001 --device-model MODEL\nProtocol and all resource options: README.md and docs/deployment.md")
		return
	}
	if e != nil {
		log.Print(e)
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if e = app.Run(ctx, cfg); e != nil {
		log.Print(e)
		os.Exit(1)
	}
}
