package machine

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"text/tabwriter"

	"github.com/glguida/dcomp/composition"
)

func componentDirectory(root string) (string, error) {
	if root == "" {
		root = os.Getenv("ASYS_INFERENCE_COMPONENTS_ROOT")
	}
	if root == "" {
		root = os.Getenv("AIM_COMPONENTS_ROOT")
	}
	if root == "" {
		executable, err := os.Executable()
		if err != nil {
			return "", err
		}
		base := filepath.Dir(executable)
		for _, candidate := range []string{filepath.Join(base, "../components"), filepath.Join(base, "../share/asys-inference/components")} {
			if info, err := os.Stat(candidate); err == nil && info.IsDir() {
				root = candidate
				break
			}
		}
	}
	if root == "" {
		return "", fmt.Errorf("cannot find component directory; set --components-root")
	}
	return filepath.Abs(root)
}

type componentEntry struct {
	Name string `json:"name"`
	composition.Component
}

func (cli CLI) components(store Store, args []string) error {
	flags := cli.flags("components")
	root := flags.String("components-root", "", "default component directory")
	jsonOutput := flags.Bool("json", false, "JSON component list")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return usage("components takes options only")
	}
	if *root == "" {
		document, err := store.Read()
		if err == nil {
			*root = document.Config.ComponentsRoot
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	directory, err := componentDirectory(*root)
	if err != nil {
		return err
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return err
	}
	components := []componentEntry{}
	for _, entry := range entries {
		source := filepath.Join(directory, entry.Name())
		if _, err := os.Stat(filepath.Join(source, "component.dcomp")); errors.Is(err, os.ErrNotExist) {
			continue
		} else if err != nil {
			// Shared dependencies and regular files are not component sources.
			if info, statErr := os.Stat(source); statErr == nil && !info.IsDir() {
				continue
			}
			return err
		}
		component, err := composition.LoadComponent(source)
		if err != nil {
			return fmt.Errorf("component %s: %w", entry.Name(), err)
		}
		components = append(components, componentEntry{Name: entry.Name(), Component: component})
	}
	if *jsonOutput {
		return writeJSON(cli.Out, components)
	}
	writer := tabwriter.NewWriter(cli.Out, 0, 4, 2, ' ', 0)
	fmt.Fprintf(cli.Out, "Available component sources in %s\n", directory)
	fmt.Fprintln(writer, "SOURCE\tINPUTS\tOUTPUTS")
	names := func(endpoints []composition.Endpoint) string {
		var names []string
		for _, endpoint := range endpoints {
			names = append(names, endpoint.Name)
		}
		if len(names) == 0 {
			return "-"
		}
		return strings.Join(names, ", ")
	}
	for _, component := range components {
		fmt.Fprintf(writer, "%s\t%s\t%s\n", component.Name, names(component.Definition.Inputs), names(component.Definition.Outputs))
	}
	return writer.Flush()
}
