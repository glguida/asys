package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/glguida/asys/asys-inference/internal/machine"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	os.Exit((machine.CLI{In: os.Stdin, Out: os.Stdout, Err: os.Stderr}).Run(ctx, os.Args[1:]))
}
