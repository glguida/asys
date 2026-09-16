package machine

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"

	"github.com/glguida/dcomp/composition"
	"github.com/glguida/dcomp/hostfs"
)

const hostRootTarget = "/run/asys-host"

// Channel support is declared by a source, not selected by its directory name.
// Only that provider receives its host root; credentials keep their own volume.
func (runtime *Runtime) hostRuntime(config Config, provider Provider) (composition.Runtime, error) {
	result := provider.Runtime
	defaults, err := config.loadRuntimeDefaults(provider)
	if err != nil || defaults.HostChannel == "" {
		return result, err
	}
	if !composition.ValidName(defaults.HostChannel) {
		return result, fmt.Errorf("invalid host channel name %q", defaults.HostChannel)
	}
	for _, bind := range result.Binds {
		if bind.Target == hostRootTarget {
			return result, fmt.Errorf("%s: %s is reserved for its host channel", provider.Name, hostRootTarget)
		}
	}
	private := filepath.Join(runtime.Store.Root, "host")
	if err := hostfs.MkdirAll(private, 0700); err != nil {
		return result, err
	}
	if err := hostfs.RestrictDirectory(private); err != nil {
		return result, err
	}
	root := filepath.Join(private, provider.Name)
	channel := filepath.Join(root, "channels", defaults.HostChannel)
	// The private parent is never mounted. Shared descendants support a
	// container UID different from the host without exposing files to host peers.
	for _, path := range []string{root, filepath.Join(root, "channels"), channel, filepath.Join(channel, "in"), filepath.Join(channel, "out")} {
		if err := os.MkdirAll(path, 0777); err != nil {
			return result, err
		}
		info, err := os.Stat(path)
		if err != nil {
			return result, err
		}
		if info.Mode().Perm() != 0777 {
			if err := os.Chmod(path, 0777); err != nil {
				return result, err
			}
		}
	}
	identity, err := json.Marshal(map[string]any{"version": 1, "name": provider.Name})
	if err != nil {
		return result, err
	}
	identityPath := filepath.Join(root, "provider.json")
	if err := AtomicWrite(identityPath, append(identity, '\n')); err != nil {
		return result, err
	}
	if err := os.Chmod(identityPath, 0644); err != nil {
		return result, err
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return result, err
	}
	result.Binds = append(append([]composition.BindMount(nil), result.Binds...), composition.BindMount{Source: root, Target: hostRootTarget})
	return result, nil
}

func gatewayHelper() (string, error) {
	return channelHelper("gateway-channel")
}

func channelHelper(name string) (string, error) {
	executable, err := os.Executable()
	if err != nil {
		return "", err
	}
	base := filepath.Dir(executable)
	for _, path := range []string{filepath.Join(base, "../tools", name), filepath.Join(base, "../share/asys-inference", name)} {
		if info, err := os.Stat(path); err == nil && info.Mode().IsRegular() {
			return filepath.Abs(path)
		}
	}
	return "", fmt.Errorf("cannot find the installed %s client; reinstall asys-inference", name)
}

func (cli CLI) requestGateway(ctx context.Context, root, operation string, body any, options ...string) error {
	helper, err := gatewayHelper()
	if err != nil {
		return err
	}
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}
	args := append([]string{helper, root, operation, string(data)}, options...)
	command := exec.CommandContext(ctx, "python3", args...)
	command.Stdin, command.Stdout, command.Stderr = cli.In, cli.Out, cli.Err
	command.Cancel = func() error { return command.Process.Signal(syscall.SIGTERM) }
	command.WaitDelay = 5 * time.Second
	if err := command.Run(); err != nil {
		return fmt.Errorf("gateway %s: %w", operation, err)
	}
	return nil
}
