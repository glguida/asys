package machine

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/glguida/dcomp/composition"
	"github.com/glguida/dcomp/engine"
	"github.com/glguida/dcomp/lifecycle"
)

func readinessFixture() (Config, lifecycle.Status) {
	config := Config{System: "test", Prefix: "provider", Running: true, Endpoint: "wrapper.provider", Providers: []Provider{
		{Name: "root"},
		{Name: "wrapper", Links: map[string]string{"upstream": "root.provider"}},
		{Name: "unused"},
	}}
	status := lifecycle.Status{Desired: true, Proxy: lifecycle.ProxyStatus{Ready: true},
		Spec: &composition.ResolvedSpec{Globals: []composition.Global{global("provider-wrapper")}},
		Components: []lifecycle.ComponentStatus{
			{Name: "provider-root", Status: "running", Health: engine.HealthHealthy},
			{Name: "provider-wrapper", Status: "running", Health: engine.HealthStarting},
			{Name: "provider-unused", Status: "exited", ExitCode: 1},
		},
	}
	return config, status
}

func TestStartupWaitsForHealthAndIgnoresUnusedBranch(t *testing.T) {
	config, status := readinessFixture()
	calls := 0
	err := awaitReady(context.Background(), config, func(context.Context) (lifecycle.Status, error) {
		calls++
		if calls == 2 {
			status.Components[1].Health = engine.HealthHealthy
		}
		return status, nil
	}, time.Millisecond)
	if err != nil || calls != 2 {
		t.Fatalf("startup returned before health check passed: calls=%d, %v", calls, err)
	}
}

func TestStartupReportsExitCodeImmediately(t *testing.T) {
	config, status := readinessFixture()
	status.Components[1].Status = "exited"
	status.Components[1].ExitCode = 17
	err := awaitReady(context.Background(), config, func(context.Context) (lifecycle.Status, error) { return status, nil }, time.Hour)
	var failure *providerFailure
	if !errors.As(err, &failure) || failure.Name != "wrapper" || !strings.Contains(err.Error(), "exited with code 17") {
		t.Fatalf("missing provider exit diagnostic: %v", err)
	}
}

func TestStartupTimeoutNamesProvider(t *testing.T) {
	config, status := readinessFixture()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	err := awaitReady(ctx, config, func(context.Context) (lifecycle.Status, error) { return status, nil }, time.Millisecond)
	if !errors.Is(err, context.DeadlineExceeded) || !strings.Contains(err.Error(), "wrapper") {
		t.Fatalf("missing startup timeout diagnostic: %v", err)
	}
}

func TestPublicationKeepsExistingOutputUntilReady(t *testing.T) {
	root, wrapper := producer("root"), producer("wrapper")
	previous := Bundle{Components: []composition.Instance{root}, Global: global("root")}
	target := Bundle{Components: []composition.Instance{root, wrapper}, Global: global("wrapper")}
	stage := beforePublication(&previous, target)
	if stage.Global != previous.Global || target.Global != global("wrapper") || len(stage.Components) != 2 {
		t.Fatal("staging replaced the public output or lost the new provider")
	}
	target.Components = []composition.Instance{wrapper}
	if stage = beforePublication(&previous, target); stage.Global.Target != (composition.EndpointRef{}) {
		t.Fatal("staging retained an output that was removed")
	}
}

func TestStatusDistinguishesRequestedAndServingOutput(t *testing.T) {
	config, status := readinessFixture()
	status.Spec.Globals = []composition.Global{global("provider-root")}
	status.Components[1].Status = "exited"
	status.Components[1].ExitCode = 1
	document := Document{Config: config, Revision: 2, AppliedRevision: 1}
	result := Observe(&document, status)
	if result.Operational || !result.Serving || result.ConfiguredEndpoint != "wrapper.provider" || result.Components[1].ExitCode != 1 {
		t.Fatalf("status concealed failed replacement or serving endpoint: %+v", result)
	}
	document.Config.Endpoint = "root.provider"
	document.AppliedRevision = document.Revision
	if result = Observe(&document, status); !result.Operational {
		t.Fatal("unused failed branch disabled a healthy selected endpoint")
	}
}
