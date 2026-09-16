package machine

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"syscall"
	"time"

	"github.com/glguida/dcomp/composition"
	"github.com/glguida/dcomp/lifecycle"
)

// Read the live global: a failed update can leave the previous endpoint serving
// while Config.Endpoint already names its requested replacement.
func modelsTarget(document *Document, status lifecycle.Status) (string, error) {
	var target composition.EndpointRef
	if status.Spec != nil {
		for _, global := range status.Spec.Globals {
			if global.Name == GlobalName {
				if global.Service != Service {
					return "", fmt.Errorf("@%s has incompatible service %s", GlobalName, global.Service)
				}
				target = global.Target
			}
		}
	}
	if target == (composition.EndpointRef{}) {
		return "", fmt.Errorf("@%s is unbound", GlobalName)
	}
	for _, component := range status.Spec.Components {
		if component.Name != target.Component {
			continue
		}
		output, err := ProviderOutput(composition.Component{Definition: component.Definition})
		if err != nil || output != target.Endpoint {
			return "", fmt.Errorf("%s is not the component's Provider output", target.String())
		}
		for _, provider := range document.Config.Providers {
			if document.Config.Physical(provider.Name) == target.Component {
				return provider.Name, nil
			}
		}
	}
	return "", fmt.Errorf("@%s points to %s, which is not managed by this inference machine", GlobalName, target.String())
}

func (cli CLI) models(ctx context.Context, runtime *Runtime, document *Document, args []string) error {
	flags := cli.flags("models")
	jsonOutput := flags.Bool("json", false, "include full exported model metadata")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return usage("models takes no arguments")
	}
	// Keep the selected component and its global assignment stable for the call.
	lock, exists, err := runtime.Controller.State.AcquireShared(ctx, document.Config.System)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("@%s is unbound", GlobalName)
	}
	defer lock.Close()
	status, err := runtime.Controller.Status(ctx, document.Config.System)
	if err != nil {
		return err
	}
	name, err := modelsTarget(document, status)
	if err != nil {
		return err
	}
	channel := ""
	for _, provider := range document.Config.Providers {
		if provider.Name == name {
			defaults, err := document.Config.loadRuntimeDefaults(provider)
			if err != nil {
				return err
			}
			channel = defaults.HostChannel
		}
	}
	if channel != "gateway" && channel != "provider" {
		return fmt.Errorf("provider %s does not declare a catalogue host channel", name)
	}
	root, err := providerRoot(ctx, runtime, document, name, channel)
	if err != nil {
		return err
	}
	requestCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	var output bytes.Buffer
	if channel == "gateway" {
		client := cli
		client.Out = &output
		err = client.requestGateway(requestCtx, root, "models", map[string]any{})
	} else {
		helper, locateErr := channelHelper("provider-channel")
		if locateErr != nil {
			return locateErr
		}
		command := exec.CommandContext(requestCtx, "python3", helper, root)
		command.Stdin, command.Stdout, command.Stderr = cli.In, &output, cli.Err
		command.Cancel = func() error { return command.Process.Signal(syscall.SIGTERM) }
		command.WaitDelay = 5 * time.Second
		err = command.Run()
	}
	if err != nil {
		return fmt.Errorf("list models from %s: %w", name, err)
	}
	var catalogue struct {
		Models []struct {
			ID string `json:"id"`
		} `json:"models"`
	}
	if err := json.Unmarshal(output.Bytes(), &catalogue); err != nil {
		return fmt.Errorf("invalid model catalogue: %w", err)
	}
	if catalogue.Models == nil {
		return fmt.Errorf("provider %s returned no model catalogue", name)
	}
	if *jsonOutput {
		_, err = cli.Out.Write(output.Bytes())
		return err
	}
	for _, model := range catalogue.Models {
		fmt.Fprintln(cli.Out, model.ID)
	}
	return nil
}
