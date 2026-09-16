package machine

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/glguida/dcomp/composition"
	"github.com/glguida/dcomp/engine"
	"github.com/glguida/dcomp/lifecycle"
)

// requiredProviders follows the selected output's local dependencies. A failed
// unused branch is visible in status, but does not disable a healthy endpoint.
func requiredProviders(config Config, status lifecycle.Status) map[string]string {
	providers := map[string]Provider{}
	physical := map[string]string{}
	for _, provider := range config.Providers {
		providers[provider.Name] = provider
		physical[config.Physical(provider.Name)] = provider.Name
	}
	required := map[string]string{}
	root, err := composition.ParseEndpointRef(config.Endpoint)
	if err != nil {
		return required
	}
	queue := []string{root.Component}
	for len(queue) > 0 {
		name := queue[0]
		queue = queue[1:]
		provider, exists := providers[name]
		if !exists || required[config.Physical(name)] != "" {
			continue
		}
		required[config.Physical(name)] = name
		for _, link := range provider.Links {
			target, err := composition.ParseTarget(link)
			if err != nil {
				continue // Config.Validate checks link syntax before applying it.
			}
			if target.Global == "" {
				queue = append(queue, target.Component)
			} else if target.Global == GlobalName {
				queue = append(queue, root.Component)
			} else if status.Spec != nil {
				for _, global := range status.Spec.Globals {
					if global.Name == target.Global {
						queue = append(queue, physical[global.Target.Component])
					}
				}
			}
		}
	}
	return required
}

type providerFailure struct {
	Name, Component, Reason string
}

func (failure *providerFailure) Error() string {
	return fmt.Sprintf("provider %q %s", failure.Name, failure.Reason)
}

func awaitReady(ctx context.Context, config Config, poll func(context.Context) (lifecycle.Status, error), interval time.Duration) error {
	waiting := "provider health checks"
	for {
		if err := ctx.Err(); err != nil {
			return fmt.Errorf("waiting for %s: %w", waiting, err)
		}
		status, err := poll(ctx)
		if err != nil {
			return fmt.Errorf("check provider readiness: %w", err)
		}
		required := requiredProviders(config, status)
		pending := []string{}
		seen := map[string]bool{}
		for _, component := range status.Components {
			name, needed := required[component.Name]
			if !needed {
				continue
			}
			seen[component.Name] = true
			reason := ""
			switch {
			case component.Status == "exited" || component.Status == "dead":
				reason = fmt.Sprintf("exited with code %d", component.ExitCode)
			case component.Problem != "":
				reason = component.Problem
			case component.Health == engine.HealthUnhealthy:
				reason = "failed its health check"
			}
			if reason != "" {
				return &providerFailure{Name: name, Component: component.Name, Reason: reason}
			}
			if component.Status != "running" || component.Health != engine.HealthHealthy {
				pending = append(pending, name+" ("+component.Status+", "+string(component.Health)+")")
			}
		}
		for component, name := range required {
			if !seen[component] {
				pending = append(pending, name+" (absent)")
			}
		}
		if !status.Desired || !status.Proxy.Ready || status.Proxy.Problem != "" || status.Operation != "" {
			pending = append(pending, "provider connections")
		}
		if len(pending) == 0 && len(required) > 0 {
			return nil
		}
		waiting = strings.Join(pending, ", ")
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
		case <-timer.C:
		}
	}
}

func (runtime *Runtime) waitReady(ctx context.Context, config Config) error {
	waitCtx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	err := awaitReady(waitCtx, config, func(ctx context.Context) (lifecycle.Status, error) {
		return runtime.Controller.Status(ctx, config.System)
	}, 250*time.Millisecond)
	var failure *providerFailure
	if !errors.As(err, &failure) {
		return err
	}
	// Keep startup diagnostics bounded and preserve the readiness error if logs
	// are unavailable. Credentials are never read from component storage.
	logCtx, stopLogs := context.WithTimeout(ctx, 2*time.Second)
	defer stopLogs()
	var lines []string
	_ = runtime.Controller.Logs(logCtx, config.System, false, func(record lifecycle.LogRecord) error {
		line := record.Line.Message
		if len(line) > 500 {
			line = line[:500] + "…"
		}
		lines = append(lines, line)
		if len(lines) > 8 {
			lines = lines[1:]
		}
		return nil
	}, failure.Component)
	if len(lines) > 0 {
		return fmt.Errorf("%w\n%s", err, strings.Join(lines, "\n"))
	}
	return err
}
